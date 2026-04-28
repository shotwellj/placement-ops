"""
SourcingNav Talent Engine — API v1
Deployed at sourcingnav.com/api/*

Two tiers of endpoints in one file:
1. DEMO endpoints (/api/dashboard/*, /api/candidates, etc) — read-only seed data
   powering the hardcoded demos at /ui/dashboard.html and /ui/people-ops.html.
2. PRODUCTION endpoints (/api/intake, /api/auth/*, /api/reqs/*, etc) — the real
   talent engine at /app/, backed by Turso SQLite + BYOK AI.
"""

import os
import json
import asyncio
import uuid
import hashlib
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

# Compliance helpers — lives in api/_compliance.py. See that module's docstring.
from api._compliance import (
    register_data_subject,
    register_model_version,
    write_audit_event,
    write_decision_explanation,
    write_submission_dimensions,
    write_req_skills,
    write_candidate_skills,
)
from api._calibration import (
    record_calibration_event,
    run_calibration,
    signal_for_transition,
    STAGE_SIGNALS,
    REJECT_SIGNALS,
)
from api._skill_resolution import (
    list_unresolved_candidates,
    get_or_generate_suggestion,
    apply_alias,
    apply_promote,
    apply_reject,
    normalize_raw_text,
)

# Optional deps (graceful if missing so demos still deploy)
# Turso access is via HTTP (see _TursoHTTPClient below), no native libsql dep needed
HAS_DB = True
HAS_DB_LEGACY = False

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# ---------- CONFIG ----------

TURSO_URL = os.environ.get("TURSO_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
BYOK_ENCRYPTION_KEY = os.environ.get("BYOK_ENCRYPTION_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
MAGIC_LINK_SECRET = os.environ.get("MAGIC_LINK_SECRET", "")
# Shared Together.ai key for free-tier users who haven't added their own BYOK.
# Free tier caps (5 intakes/mo) are enforced at the intake-flow level via
# check_cap() BEFORE call_ai() ever runs, so this key cannot be abused beyond
# the documented free quota. Pro-tier users can still bring their own key.
SERVER_TOGETHER_KEY = os.environ.get("SERVER_TOGETHER_KEY", "")
# Server-side Anthropic key used as automatic FAILOVER when Together.ai is
# slow, unreachable, or returning 5xx. Together is primary because it's
# cheapest; Anthropic is fallback because it's the most reliable provider
# we have. The fallback only fires on transient errors (timeouts, 5xx) —
# never on 4xx (which would just hide bugs). See _call_with_failover().
SERVER_ANTHROPIC_KEY = os.environ.get("SERVER_ANTHROPIC_KEY", "")

fernet = None
if HAS_CRYPTO and BYOK_ENCRYPTION_KEY:
    try:
        fernet = Fernet(BYOK_ENCRYPTION_KEY.encode() if isinstance(BYOK_ENCRYPTION_KEY, str) else BYOK_ENCRYPTION_KEY)
    except Exception:
        fernet = None

SEED_DIR = Path(__file__).parent / "seed"

# ---------- APP ----------

app = FastAPI(
    title="SourcingNav Talent Engine",
    description="Demos (read-only) + production talent engine.",
    version="1.0.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sourcingnav.com",
        "https://www.sourcingnav.com",
        "http://localhost:8000",
        "http://localhost:3000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_seed(name: str) -> dict:
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Seed '{name}' not found")
    with open(path, "r") as f:
        return json.load(f)


def _turso_http_url() -> str:
    """Turn libsql://... into https://... for the HTTP API."""
    if TURSO_URL.startswith("libsql://"):
        return "https://" + TURSO_URL[len("libsql://"):]
    return TURSO_URL


def _to_py_value(cell: dict):
    """Convert a Turso HTTP response cell into a plain Python value."""
    if cell is None:
        return None
    t = cell.get("type")
    v = cell.get("value")
    if t == "null":
        return None
    if t == "integer":
        return int(v) if v is not None else None
    if t == "float":
        return float(v) if v is not None else None
    if t == "text":
        return v
    if t == "blob":
        return v
    return v


class _Result:
    """Matches the subset of libsql_client.ResultSet used by this app."""
    def __init__(self, raw_result: dict):
        self._raw = raw_result
        cols = raw_result.get("cols", []) if raw_result else []
        self.columns = tuple(c.get("name") for c in cols)
        self.rows = []
        for raw_row in (raw_result or {}).get("rows", []):
            self.rows.append(tuple(_to_py_value(cell) for cell in raw_row))
        self.rows_affected = (raw_result or {}).get("affected_row_count", 0)


class _TursoHTTPClient:
    """Minimal async Turso client using the HTTP /v2/pipeline endpoint.
    Drop-in replacement for the subset of libsql_client.Client we use."""

    def __init__(self, base_url: str, token: str):
        self._base = base_url.rstrip("/")
        self._token = token
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def execute(self, sql: str, params: Optional[list] = None) -> _Result:
        if self._http is None:
            raise RuntimeError("Use `async with` before calling execute()")
        # Build a positional-args SQL statement for Hrana
        if params:
            args = []
            for p in params:
                if p is None:
                    args.append({"type": "null"})
                elif isinstance(p, bool):
                    args.append({"type": "integer", "value": "1" if p else "0"})
                elif isinstance(p, int):
                    args.append({"type": "integer", "value": str(p)})
                elif isinstance(p, float):
                    args.append({"type": "float", "value": p})
                else:
                    args.append({"type": "text", "value": str(p)})
            stmt = {"sql": sql, "args": args}
        else:
            stmt = {"sql": sql}

        r = await self._http.post(
            f"{self._base}/v2/pipeline",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        )
        if r.status_code != 200:
            raise HTTPException(500, f"Turso HTTP error {r.status_code}: {r.text[:200]}")
        body = r.json()
        res = body.get("results", [])
        if not res:
            raise HTTPException(500, "Turso returned no results")
        first = res[0]
        if first.get("type") == "error":
            err = first.get("error", {})
            raise HTTPException(500, f"Turso query error: {err.get('message', 'unknown')}")
        return _Result(first.get("response", {}).get("result", {}))


def db():
    if not TURSO_URL or not TURSO_TOKEN:
        raise HTTPException(
            503,
            "Database not configured. Set TURSO_URL and TURSO_AUTH_TOKEN in Vercel.",
        )
    return _TursoHTTPClient(_turso_http_url(), TURSO_TOKEN)


# ---------- META ----------

@app.get("/api")
def root():
    return {
        "name": "SourcingNav Talent Engine",
        "version": "1.0.0",
        "status": "ok",
        "demo_endpoints": [
            "/api/health", "/api/dashboard/agency", "/api/dashboard/company",
            "/api/candidates", "/api/pipeline", "/api/market-intel",
            "/api/scan", "/api/batch", "/api/calibration", "/api/integrations",
        ],
        "app_endpoints": [
            "/api/auth/magic-link", "/api/auth/verify",
            "/api/user/me", "/api/user/byok-key",
            "/api/intake", "/api/reqs", "/api/reqs/{id}",
        ],
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "sourcingnav-api",
        "db_configured": bool(TURSO_URL and TURSO_TOKEN),
        "crypto_configured": fernet is not None,
        "time": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
# DEMO ENDPOINTS — read-only, unchanged from v0.1
# Powers the hardcoded demos at /ui/dashboard.html and /ui/people-ops.html
# =====================================================================

@app.get("/api/dashboard/agency")
def dashboard_agency():
    return load_seed("dashboard")["agency"]


@app.get("/api/dashboard/company")
def dashboard_company():
    return load_seed("dashboard")["company"]


@app.get("/api/candidates")
def list_candidates_demo():
    return load_seed("candidates")


@app.get("/api/candidates/{candidate_id}")
def get_candidate_demo(candidate_id: str):
    data = load_seed("candidates")
    for c in data["candidates"]:
        if c["id"] == candidate_id:
            return c
    raise HTTPException(404, f"Candidate '{candidate_id}' not found")


@app.get("/api/pipeline")
def pipeline_demo():
    return load_seed("pipeline")


@app.get("/api/market-intel")
def market_intel_demo():
    return load_seed("market_intel")


@app.get("/api/scan")
def scan_demo():
    return load_seed("scan")


@app.get("/api/batch")
def batch_demo():
    return load_seed("batch")


@app.get("/api/calibration")
def calibration_demo():
    return load_seed("calibration")


@app.get("/api/integrations")
def integrations_demo():
    return load_seed("integrations")


# =====================================================================
# PRODUCTION — the real talent engine at /app/
# =====================================================================

FREE_CAPS = {"intake": 5, "eval": 10, "outreach": 10}


# Free-tier billing period is rolling 30 days, not calendar months. Fairer
# to users who sign up mid-month, simpler to reason about, no cron needed —
# we reset lazily on the next cap check.
FREE_PERIOD_DAYS = 30


async def check_cap(user_id: str, cap_type: str):
    """Check the cap WITHOUT incrementing. Raises 402 if the user is over.

    Lazy monthly reset: if the user's usage_reset_at is more than
    FREE_PERIOD_DAYS old (or NULL for legacy users), reset all three
    usage counters to zero and bump usage_reset_at to now BEFORE
    checking the cap. No cron job required.
    """
    async with db() as client:
        rs = await client.execute(
            """SELECT plan, usage_intake, usage_eval, usage_outreach,
                      usage_reset_at, created_at
               FROM users WHERE id = ?""",
            [user_id],
        )
        if not rs.rows:
            raise HTTPException(404, "User not found")
        row = rs.rows[0]
        plan = row[0]
        usage_map = {"intake": row[1] or 0, "eval": row[2] or 0, "outreach": row[3] or 0}
        reset_at = row[4]  # may be None on legacy rows
        created_at = row[5]

        # ---- Lazy rolling-30-day reset ----
        # Use SQLite to do the date math so we don't have to parse timestamps
        # in Python. If reset_at is NULL we treat created_at as the anchor.
        anchor = reset_at or created_at
        if anchor:
            check = await client.execute(
                "SELECT (julianday('now') - julianday(?)) >= ?",
                [anchor, FREE_PERIOD_DAYS],
            )
            should_reset = bool(check.rows and check.rows[0] and check.rows[0][0])
            if should_reset:
                await client.execute(
                    """UPDATE users
                       SET usage_intake = 0, usage_eval = 0, usage_outreach = 0,
                           usage_reset_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    [user_id],
                )
                # In-memory map needs to reflect the reset for this call
                usage_map = {"intake": 0, "eval": 0, "outreach": 0}

        if plan == "free" and usage_map[cap_type] >= FREE_CAPS[cap_type]:
            raise HTTPException(
                402,
                f"Free tier cap reached ({FREE_CAPS[cap_type]}/mo, resets every {FREE_PERIOD_DAYS} days). Upgrade to Pro for unlimited.",
            )


async def increment_cap(user_id: str, cap_type: str):
    """Increment the usage counter. Call this ONLY after the work succeeds."""
    col = f"usage_{cap_type}"
    async with db() as client:
        await client.execute(
            f"UPDATE users SET {col} = {col} + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [user_id],
        )


async def check_and_increment_cap(user_id: str, cap_type: str):
    """Legacy combined function — kept for any callers that want the old behavior."""
    await check_cap(user_id, cap_type)
    await increment_cap(user_id, cap_type)


# ---------- AUTH ----------

class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyTokenRequest(BaseModel):
    token: str


def sign_token(email: str, exp_minutes: int = 15, kind: str = "magic_link") -> str:
    """Sign a token with an email, expiry, and purpose tag.

    kind="magic_link" — short-lived (15 min), sent via email, used once
    kind="session"    — long-lived (30 days), stored in browser, used for API auth
    """
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)).timestamp())
    payload = f"{email}|{exp}|{kind}"
    sig = hashlib.sha256(f"{payload}|{MAGIC_LINK_SECRET}".encode()).hexdigest()[:32]
    return f"{email}|{exp}|{kind}|{sig}"


def verify_token(token: str, expected_kind: Optional[str] = None) -> Optional[str]:
    """Verify a signed token. If expected_kind is set, require it match."""
    try:
        parts = token.split("|")
        # New format: email|exp|kind|sig (4 parts)
        # Old format: email|exp|sig (3 parts) — treat as session for backwards compat
        if len(parts) == 4:
            email, exp_str, kind, sig = parts
        elif len(parts) == 3:
            email, exp_str, sig = parts
            kind = "session"  # legacy tokens get treated as sessions
        else:
            return None
        exp = int(exp_str)
        if datetime.now(timezone.utc).timestamp() > exp:
            return None
        if len(parts) == 4:
            expected = hashlib.sha256(f"{email}|{exp}|{kind}|{MAGIC_LINK_SECRET}".encode()).hexdigest()[:32]
        else:
            # Legacy signature format
            expected = hashlib.sha256(f"{email}|{exp}|{MAGIC_LINK_SECRET}".encode()).hexdigest()[:32]
        if sig != expected:
            return None
        if expected_kind and kind != expected_kind and kind != "session":
            # Sessions can be used anywhere; but a magic_link can't be used as a session
            return None
        return email
    except Exception:
        return None


def _hash_token(token: str) -> str:
    """Hash a session token for storage. We never store raw tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


def _client_ip(request_headers: dict) -> Optional[str]:
    """Extract the real client IP from Vercel's x-forwarded-for header."""
    xff = request_headers.get("x-forwarded-for") or request_headers.get("x-real-ip")
    if xff:
        return xff.split(",")[0].strip()
    return None


async def get_current_user(authorization: str = Header(None), user_agent: Optional[str] = Header(None)) -> dict:
    """Validate the bearer token AND check the session row exists + isn't revoked.

    Backwards compatible: tokens issued before sessions table existed will still work
    (no session row found just means "legacy token, accept on signature alone").
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")

    # Step 1: validate signature + expiry
    email = verify_token(token)
    if not email:
        raise HTTPException(401, "Invalid or expired token")

    token_hash = _hash_token(token)

    # Step 2: check session row (skip the check for legacy tokens with no session row)
    async with db() as client:
        rs = await client.execute(
            "SELECT id, user_id, revoked_at, expires_at FROM sessions WHERE session_token_hash = ?",
            [token_hash],
        )
        if rs.rows:
            session_id, _user_id, revoked_at, expires_at = rs.rows[0]
            if revoked_at is not None:
                raise HTTPException(401, "Session revoked. Please sign in again.")
            # last_used_at update — best-effort, don't fail the request if it errors
            try:
                await client.execute(
                    "UPDATE sessions SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [session_id],
                )
            except Exception:
                pass
        # If no session row found, this is a legacy token (issued before this table existed).
        # We accept it based on signature alone — they'll get a proper session next login.

        # Step 3: load the user
        rs = await client.execute("SELECT id, email, mode, plan FROM users WHERE email = ?", [email])
        if not rs.rows:
            raise HTTPException(404, "User not found")
        r = rs.rows[0]
        return {"id": r[0], "email": r[1], "mode": r[2], "plan": r[3]}


# ---------- RATE LIMITING ----------

MAGIC_LINK_RATE_LIMIT_PER_HOUR = 5  # max magic-link requests per email per hour
MAGIC_LINK_RATE_LIMIT_PER_IP_PER_HOUR = 20  # max per IP across all emails


async def check_magic_link_rate_limit(email: str, ip: Optional[str]):
    """Raise HTTPException(429) if email or IP has exceeded magic-link rate limits.

    NOTE: SQLite/Turso stores TIMESTAMP DEFAULT CURRENT_TIMESTAMP as 'YYYY-MM-DD HH:MM:SS'
    (no T, no tz). We use the SQL function datetime('now', '-1 hour') to do the
    comparison server-side so format matching is automatic.
    """
    async with db() as client:
        rs = await client.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE email = ? AND attempted_at > datetime('now', '-1 hour')",
            [email],
        )
        if rs.rows and rs.rows[0][0] >= MAGIC_LINK_RATE_LIMIT_PER_HOUR:
            raise HTTPException(429, "Too many login attempts for this email. Try again in an hour.")
        if ip:
            rs = await client.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE ip_address = ? AND attempted_at > datetime('now', '-1 hour')",
                [ip],
            )
            if rs.rows and rs.rows[0][0] >= MAGIC_LINK_RATE_LIMIT_PER_IP_PER_HOUR:
                raise HTTPException(429, "Too many login attempts from this network. Try again in an hour.")


async def log_login_attempt(email: str, ip: Optional[str], success: bool):
    """Best-effort logging of login attempts. Never raises."""
    try:
        async with db() as client:
            await client.execute(
                "INSERT INTO login_attempts (id, email, ip_address, success) VALUES (?, ?, ?, ?)",
                [str(uuid.uuid4()), email, ip, 1 if success else 0],
            )
    except Exception:
        pass


# ---------- AI ROUTER (BYOK) ----------

async def call_ai(user_id: str, prompt: str, max_tokens: int = 8000) -> str:
    """Single code path: server keys with automatic Together -> Anthropic failover.

    Earlier versions had a BYOK branch that bypassed the failover wrapper,
    causing Together 503s to surface directly to users. Recruiters (our
    target audience) don't know what an API key is and shouldn't be asked
    to bring one. Now everyone — free and Pro — uses our server keys with
    failover.

    Free-tier abuse is prevented one layer up: every intake/eval/outreach
    endpoint calls check_cap() BEFORE call_ai(), so a free user who hit
    5/5 intakes cannot trigger another LLM call. Pro users have unlimited
    intakes (no check_cap gate for them), and the LLM cost is on us until
    a billing system is wired.

    user_id is kept in the signature for the diagnostic log line and for
    future per-user routing (e.g., model selection by tier).
    """
    print(f"[ai-call] user={user_id[:8]}... source=server-shared (with failover)")
    return await _call_with_failover(prompt, max_tokens)


def _ai_error(provider: str, status: int, body: str) -> HTTPException:
    """Build a readable error from an AI provider's error response."""
    snippet = body[:400] if body else "<empty body>"
    return HTTPException(500, f"{provider} {status}: {snippet}")


async def _call_anthropic(api_key: str, prompt: str, max_tokens: int) -> str:
    """Call Anthropic Claude. Used as fallback when Together.ai fails AND as
    a BYOK option. The system message enforces JSON-only output so the
    response works with the same parse_json_strict() the Together path uses.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "system": "Respond with valid JSON only. No markdown code fences. No prose before or after the JSON object.",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if r.status_code >= 400:
            raise _ai_error("anthropic", r.status_code, r.text)
        text = r.json()["content"][0]["text"]
        # Strip code fences and any leading/trailing prose just in case
        return text.replace("```json", "").replace("```", "").strip()


async def _call_openai(api_key: str, prompt: str, max_tokens: int) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if r.status_code >= 400:
            raise _ai_error("openai", r.status_code, r.text)
        return r.json()["choices"][0]["message"]["content"]


async def _call_together(api_key: str, prompt: str, max_tokens: int) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.together.xyz/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                # Together hosts the FP8-quantized variant. The non-FP8 name
                # returns a 400 because that model isn't deployed on their endpoint.
                "model": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "Respond with valid JSON only. No markdown code fences."},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        if r.status_code >= 400:
            raise _ai_error("together", r.status_code, r.text)
        text = r.json()["choices"][0]["message"]["content"]
        if "<think>" in text and "</think>" in text:
            text = text.split("</think>")[-1].strip()
        return text.replace("```json", "").replace("```", "").strip()



# ---------- FAILOVER WRAPPER ----------

async def _call_with_failover(prompt: str, max_tokens: int = 8000) -> str:
    """Server-key path with automatic provider failover.

    Tries Together.ai first (cheapest), falls back to Anthropic if Together
    times out, refuses connections, or returns 5xx. Falls through to a
    helpful 503 only if BOTH providers fail.

    BYOK callers do NOT go through this function — they hit _call_anthropic /
    _call_openai / _call_together directly. Failover is for shared-server-key
    flows only because we control the env-var keys.

    Transient error policy:
      - httpx.ReadTimeout, ConnectError, ConnectTimeout, ReadError -> failover
      - HTTPException with status_code 5xx -> failover (Together's 503 fits)
      - Anything else (4xx, parse errors) -> raise unchanged. We do NOT want
        to mask 401/403 (key issues) or 429 (rate limit) by retrying — the
        fallback won't help and would hide a real bug.

    Logs which provider served the request and whether failover fired so we
    can monitor reliability in production.
    """
    if not SERVER_TOGETHER_KEY and not SERVER_ANTHROPIC_KEY:
        raise HTTPException(
            500,
            "No server-side AI keys configured. Set SERVER_TOGETHER_KEY and/or SERVER_ANTHROPIC_KEY in Vercel env.",
        )

    transient_exc = (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError)

    def _is_transient_http(exc: Exception) -> bool:
        """5xx HTTPExceptions are transient. 4xx are not (would hide bugs)."""
        if isinstance(exc, HTTPException):
            return exc.status_code >= 500
        return False

    # Primary: Together.ai
    if SERVER_TOGETHER_KEY:
        try:
            result = await _call_together(SERVER_TOGETHER_KEY, prompt, max_tokens)
            print(f"[ai-call] source=server-shared provider=together status=ok")
            return result
        except transient_exc as e:
            print(f"[ai-call FAILOVER] together transient {type(e).__name__}: {str(e)[:160]} -> trying anthropic")
        except HTTPException as e:
            if _is_transient_http(e):
                print(f"[ai-call FAILOVER] together {e.status_code}: {str(e.detail)[:160]} -> trying anthropic")
            else:
                # 4xx — surface to caller, do NOT failover
                raise

    # Fallback: Anthropic Claude
    if SERVER_ANTHROPIC_KEY:
        try:
            result = await _call_anthropic(SERVER_ANTHROPIC_KEY, prompt, max_tokens)
            print(f"[ai-call] source=server-shared provider=anthropic status=ok-after-failover")
            return result
        except (transient_exc + (HTTPException,)) as e:
            detail = str(e.detail) if isinstance(e, HTTPException) else str(e)
            print(f"[ai-call FAILED-BOTH] anthropic also failed: {type(e).__name__}: {detail[:200]}")
            raise HTTPException(
                503,
                "Both AI providers are unreachable right now. Please try again in a moment. "
                "If this persists, email hello@sourcingnav.com.",
            )

    # Together failed and there's no Anthropic key configured
    raise HTTPException(
        503,
        "AI provider unreachable. Please try again in a moment.",
    )


def parse_json_strict(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


# ---------- PROMPTS ----------

JD_PARSER_PROMPT = """You are an expert technical recruiter with 13+ years in sourcing.

Parse this job description and return a structured JSON analysis.

JOB DESCRIPTION:
{jd}

CRITICAL RULE — NEVER RECOMMEND POACHING THE HIRING COMPANY:

The company in the JD is the CLIENT. It is a non-solicit violation in most
recruiting contracts and a legal risk to recommend sourcing candidates from
the same company the recruiter is hiring FOR. Auto-reject any suggestion that
points candidates back at the client.

Apply this rule EVERYWHERE in your output:
  - recommended_first_moves: never mention the hiring company by name as a
    source. Never write "target engineers from [hiring company]" or
    "reach out to [hiring company] employees".
  - poaching_targets: NEVER include the hiring company. Exclude it even if
    it is the most obvious source of this exact skillset.
  - top_hiring_companies and talent_hotspots: the hiring company can appear
    here as context (they are hiring, after all) but NEVER as a poaching
    source.
  - sourcing_strategy: tactics must target COMPETITORS and ADJACENT
    companies, never the client.

If you identify the hiring company from the JD, treat it as a filter: it is
the one company the recruiter cannot source from. List 3+ real competitors
instead.

CRITICAL RULES FOR must_have_skills:
You MUST stratify the must-haves by REAL hiring impact, not by what the JD claims is required.
JDs lie. They list 15 required skills but realistically only 2-4 will get a candidate
auto-rejected at the resume screen. Use these severity levels:

  - "blocker"   = Cannot proceed without this. Resume gets tossed in 30 seconds.
                  Examples: specific years of experience in the core domain, a license/cert
                  that's legally required, a hard technical skill that defines the role
                  (RTL for chip design, Solidity for blockchain, FDA experience for medical).
                  HARD CAP: maximum 4 blockers.

  - "preferred" = Listed as required in the JD, but realistically the hiring manager will
                  trade off if everything else is great. Most "must-haves" in JDs are
                  actually preferences. The remaining required-section items go here.

Be honest. If a JD lists "10+ years of experience" but the role is a Senior IC, that's
a preference, not a blocker. Most "team player / strong communication" requirements are
preferences, not blockers, unless the role is explicitly customer/sales-facing.

CRITICAL RULES FOR canonical_skills:
In addition to must_have_skills (which is prose for the UI), you MUST also output
a flat list of canonical_skills. These are CLEAN skill names suitable for database
matching, not sentences.

BAD (these are rationale, not skills):
  - "5+ years in chip design, verification, or EDA (RTL, timing closure, co-design)"
  - "Direct experience with RTL, simulators, or verification environments"
  - "Production-grade coding in Python or systems languages"

GOOD (clean canonical names, one per skill):
  - "RTL Design"
  - "SystemVerilog"
  - "UVM"
  - "Python"
  - "Timing Closure"

Rules:
  - Each entry is a proper-noun skill name (2-5 words max).
  - Split compound requirements: "RTL + timing closure" becomes TWO entries,
    "RTL Design" and "Timing Closure".
  - Use the most common industry name: "PyTorch" not "Torch", "Apache Spark"
    not "Spark Core", "UVM" not "Universal Verification Methodology".
  - Mark each with severity matching its source must_have/nice_to_have entry.
  - 6-15 entries total. If the JD mentions a skill, extract it.
  - Do NOT include soft skills like "communication" or "teamwork" here — those
    belong in must_have_skills prose, not canonical_skills.

CRITICAL RULES FOR comp_snapshot:
ALWAYS populate this with realistic ranges, even if comp is not in the JD.
Use your knowledge of the role title, level, location, company tier, and industry.

EMPLOYMENT TYPE DETECTION (do this FIRST before formatting comp):
Look at the JD for hourly / contract / 1099 / W2-contract indicators:
  - Phrases like "$X/hr", "$X per hour", "/hour", "hourly rate"
  - Phrases like "contract", "1099", "C2C", "contract-to-hire", "W2 contract"
  - Phrases like "freelance", "gig", "task-based pay"
  - Companies known for crowdsourced/task-based work (DataAnnotation, Scale AI taskers, Mechanical Turk, Outlier, Labelbox annotators, Surge AI raters)

If ANY of those signals are present, this is a HOURLY role. NEVER convert
hourly rates to fake annual figures. A "$50-100/hr" rate is NOT "$104k-$208k
annual" — taskers don't work 40hr/wk for 52 weeks. Reporting fake annual
comp on hourly work is the kind of error that destroys recruiter trust
in the tool.

SCHEMA LOCK. comp_snapshot MUST use exactly these four string fields and NO others:
  - base_range:        STRING. For salaried roles: "$XXXk - $XXXk" (e.g. "$220k - $280k").
                       For hourly/contract roles: "$XX - $XX/hr" (e.g. "$50 - $100/hr"). Preserve
                       hourly format AS-IS, do NOT convert to annual.
  - total_comp_range:  STRING. For salaried: "$XXXk - $XXXk (incl. equity/bonus)".
                       For hourly/contract: "$XX - $XX/hr (variable, work-dependent)". Do NOT
                       fabricate annual totals from hourly rates.
  - equity_notes:      STRING, 1-2 sentences on equity expectations. For hourly/contract roles,
                       say something like "No equity. Pay is hourly/per-task only."
  - negotiation_notes: STRING, 1-2 sentences on what levers to pull. For hourly/contract roles,
                       focus on rate negotiation, project scope, and shift availability rather
                       than equity/bonus levers.

Do NOT use base_min, base_max, total_comp_min, total_comp_max, or any numeric fields.
Do NOT nest objects inside comp_snapshot. All four values are flat strings.
If you cannot estimate comp, still return strings (e.g. base_range: "Unknown - market dependent").

CRITICAL RULES FOR alt_titles:
This is what separates a junior sourcer from a senior one. Your job is to
expand the searchable surface beyond the literal job title.

Three dimensions, all required:

  level_progression — same role at different IC levels. If the JD is for
    a "Senior Backend Engineer", give the actual titles peer companies use
    at junior, mid, senior, and staff_plus levels. Real titles, not generic
    ones. "L4 Software Engineer" is fine if that's what FAANG uses. Aim for
    2-4 titles per level. Reflect title inflation — "Staff Engineer" at a
    50-person Series B is doing what "Senior" does at FAANG; capture both.

  functional_aliases — what the SAME PERSON is called at peer companies
    that name the role differently. A Backend Engineer at a startup is a
    Platform Engineer at infra-heavy shops, a Distributed Systems Engineer
    at scale companies, an Infrastructure Engineer at cloud-native shops.
    Give 3-6 functional aliases with one-line rationale per alias. These
    are pure title-naming differences for the same skill profile.

  adjacent_crossover — DIFFERENT roles where the same person could shift.
    A Site Reliability Engineer with strong systems chops can take a
    Backend Engineer role; a senior Data Engineer can often shift to
    Platform Engineer; etc. Give 3-5 adjacent titles with rationale on
    why the crossover works AND a transition_difficulty rating
    ("easy" if 70%+ of skills overlap, "moderate" if 40-70%, "hard" if
    25-40%). Skip anything below 25% overlap. These are POACHING
    candidates the recruiter wouldn't have searched for.

The whole point: a recruiter searching only for "Senior Backend Engineer"
misses 60% of qualified candidates who hold one of these alternative titles.
Your alt_titles output is the broader search universe.

CRITICAL RULES FOR watering_holes:
This is venue-specific sourcing intelligence — the actual websites, forums,
events, mailing lists, Discords, and communities where THIS specific
archetype congregates. Generic ("LinkedIn", "GitHub") doesn't count.

For each watering hole, give:
  - venue: the specific name (lore.kernel.org, NeurIPS, Bootlin, HuggingFace,
    DEFCON CTF, KX/Q forums, Embedded World speakers list — be specific)
  - venue_type: mailing_list | conference | community | publication |
    code_host | training_alumni | competition | discord_slack
  - signal: what kind of candidate signal you find there in 1 sentence
    ("Linux kernel maintainers — Signed-off-by tags = professional-grade
    upstream contribution")
  - how_to_use: 1 sentence on how to actually source from this venue.
    Use Google X-ray syntax with DOUBLE quotes and no literal AND:
    ("X-ray: site:lore.kernel.org \"Signed-off-by:\" \"embedded\" (\"arm\" OR \"aarch64\")")

Aim for 5-8 watering holes. Span at least 3 venue_types. Skip generic
catch-alls like "LinkedIn" or "Indeed" — those are already in the X-ray
strings. The point is the NICHE venues only a grandmaster would know.

Examples by archetype:

  Embedded firmware/kernel: lore.kernel.org (mailing_list),
    Embedded World speakers (conference), Bootlin training alumni
    (training_alumni), RISC-V Summit (conference), kernel.org maintainers
    (publication), JESD204B working groups (community)

  ML/AI research: NeurIPS authors (publication), HuggingFace top
    contributors (code_host), EleutherAI Discord (discord_slack),
    arXiv recent submissions (publication), MLSys conference (conference),
    ICML/ICLR authors (publication)

  Security: DEFCON CTF leaderboards (competition), BugCrowd top 100
    (competition), Black Hat speakers (conference), specific Twitter
    circles (community), CVE assignees (publication)

  Finance engineering: KX/Q Code Group (community), HFT alumni networks
    (training_alumni), QuantConnect (community), specific Slack groups
    (discord_slack), kdb+ user forums (community)

  Defense/cleared: AFCEA chapter events (conference), MORS conferences
    (conference), specific cleared-talent meetups (community), patents
    (publication), DARPA program alumni (training_alumni)

Pick venues that match the JD's domain. If you don't know good venues
for a niche, return fewer high-quality ones rather than guessing.

Return ONLY valid JSON with this shape:
{{
  "core": {{
    "role_title": "...", "level": "...", "company": "...",
    "location": "...", "remote_policy": "remote|hybrid|onsite", "industry": "..."
  }},
  "executive_brief": {{
    "summary": "2-3 sentences on what this role is really about",
    "market_temperature": "hot|warm|cool",
    "recommended_first_moves": ["action 1", "action 2", "action 3"]
  }},
  "must_have_skills": [
    {{"skill": "...", "rationale": "why this is a true blocker", "severity": "blocker"}},
    {{"skill": "...", "rationale": "why this is preferred but negotiable", "severity": "preferred"}}
  ],
  "canonical_skills": [
    {{"name": "RTL Design", "severity": "blocker"}},
    {{"name": "SystemVerilog", "severity": "blocker"}},
    {{"name": "Python", "severity": "preferred"}}
  ],
  "nice_to_have_skills": [{{"skill": "...", "rationale": "..."}}],
  "transferable_skill_clusters": [{{"cluster_name": "...", "variants": [], "adjacent_skills": []}}],
  "alt_titles": {{
    "level_progression": {{
      "ic_junior": ["title at junior level"],
      "ic_mid": ["title at mid level"],
      "ic_senior": ["title at senior level"],
      "ic_staff_plus": ["title at staff/principal level"]
    }},
    "functional_aliases": [
      {{"title": "Platform Engineer", "rationale": "what infra-heavy shops call backend engineers"}},
      {{"title": "Distributed Systems Engineer", "rationale": "what scale-focused companies call this same person"}}
    ],
    "adjacent_crossover": [
      {{"title": "Site Reliability Engineer", "rationale": "SREs at scale companies often have the systems chops to make this jump", "transition_difficulty": "easy|moderate|hard"}}
    ]
  }},
  "comp_snapshot": {{
    "base_range": "$XXXk - $XXXk",
    "total_comp_range": "$XXXk - $XXXk (incl. equity/bonus)",
    "equity_notes": "...",
    "negotiation_notes": "..."
  }},
  "market_dynamics": {{
    "talent_saturation": "low|medium|high",
    "time_to_fill_days": [30, 60],
    "difficulty_score": 7
  }},
  "market360": {{
    "top_hiring_companies": [],
    "talent_hotspots": [],
    "poaching_targets": [{{"company": "...", "tier": 1, "rationale": "..."}}]
  }},
  "sourcing_strategy": {{"priority_channels": [], "key_tactics": []}},
  "watering_holes": [
    {{
      "venue": "lore.kernel.org",
      "venue_type": "mailing_list",
      "signal": "Linux kernel maintainers — Signed-off-by tags signal professional-grade upstream contribution",
      "how_to_use": "X-ray: site:lore.kernel.org \"Signed-off-by:\" \"embedded\" \"arm\""
    }}
  ]
}}

No em dashes. No code fences. Just JSON.
"""

BOOLEAN_BUILDER_PROMPT = """You are an expert sourcer with 13+ years of Boolean search experience.

PARSED JD:
{parsed_jd}

Generate 10 Boolean strings: 3 LinkedIn Recruiter strings (for paid LR users) and
7 X-ray search strings (Google operators that work for everyone, no LR seat needed).

X-ray searches are the universal sourcer's weapon. They find candidates who:
  - Aren't on LinkedIn Recruiter at all
  - Have public work (GitHub commits, Kaggle notebooks, conference talks)
  - Host resumes on personal sites
  - Talk publicly about their work (Twitter/X)
  - Are active on niche platforms (HuggingFace, Devpost, Stack Overflow)

Use REAL technology names from the parsed JD (Verilog not "HDL", PyTorch not "ML framework").
Use proper Google syntax for X-ray: site:, intitle:, in:bio, in:readme, OR, AND, quoted phrases.

Return ONLY valid JSON. CRITICAL SYNTAX NOTES before the schema:
  - Every phrase in an X-ray string MUST be wrapped in escaped double quotes (\"...\")
    not single quotes. Single quotes are treated as apostrophes by Google and
    return garbage. JSON requires double quotes to be escaped: \"embedded linux\".
  - Do NOT write the word AND between terms in X-ray strings. Google treats a
    SPACE as AND implicitly. Writing the literal word "AND" makes Google search
    for pages containing the word AND itself, which kills your results.
  - DO write OR (uppercase) between alternatives, always inside parentheses:
    (\"BSP\" OR \"board support package\")
  - LinkedIn Recruiter strings are the exception — they use single quotes and
    accept the AND keyword. Keep LR and X-ray syntax strictly separated.

{{
  "linkedin_recruiter": {{
    "sniper": "tightest possible, 3-5 must-have terms, expect <100 results",
    "precision": "strong matches with seniority signal, ~50-200 results",
    "expanded": "broader recall with adjacent skills, ~200-1000 results"
  }},
  "xray": {{
    "linkedin": "site:linkedin.com/in/ \"Senior Embedded Linux Engineer\" (\"BSP\" OR \"device driver\") \"San Diego\"",
    "github": "site:github.com (\"Yocto\" OR \"meta-layer\") \"embedded linux\" \"device driver\"",
    "medium": "site:medium.com (\"embedded linux\" OR \"kernel driver\") (\"tutorial\" OR \"deep dive\")",
    "stackoverflow": "site:stackoverflow.com/users \"embedded\" \"[linux-kernel]\" \"[device-driver]\"",
    "conferences": "(site:youtube.com OR site:slideshare.net) \"Embedded World\" \"device driver\"",
    "personal_sites": "(intitle:resume OR intitle:CV) \"embedded linux\" \"C++\" -site:linkedin.com -site:indeed.com",
    "specialty": "site:lore.kernel.org \"Signed-off-by:\" \"embedded\" \"driver\""
  }},
  "company_clusters": {{
    "tier_1_direct_competitors": ["Company1", "Company2", "Company3"],
    "tier_2_adjacent": ["Company4", "Company5", "Company6"]
  }},
  "mentor_notes": {{
    "best_xray_to_start": "1 sentence: which X-ray to run first and why",
    "keyword_reasoning": "1 sentence: why these specific keywords",
    "pro_tip": "1 sentence: a tactical tip a senior sourcer would share"
  }}
}}

Rules:
- No em dashes anywhere
- LR strings use LR syntax (title:, location:, current_company:)
- X-ray strings use Google syntax: site:, intitle:, -site: (exclusion), OR (uppercase), double-quoted phrases. DO NOT write the literal word AND — a space is already an implicit AND on Google and writing AND makes Google search for the word "AND" itself.
- Tier 1 = same product/market as the hiring company
- Tier 2 = adjacent industry/skill overlap
- NEVER include the hiring company itself in tier_1 or tier_2. The hiring company
  is the client, and recommending sourcing from them is a non-solicit violation.
  If the JD identifies the hiring company, exclude it from all company lists
  and replace with real competitors.
- Be specific. Generic strings like "engineer AND python" are useless.

X-RAY SEARCH CONSTRAINTS (these strings must actually run on Google, not just look smart):

0. DOUBLE QUOTES ONLY AROUND PHRASES. Single quotes (apostrophes) are IGNORED
   by Google — they do nothing. Every multi-word phrase in an X-ray MUST be
   wrapped in double quotes. Because these strings are going into a JSON string
   field, escape them as \"...\". Example of the WRONG pattern:
     site:linkedin.com/in/ 'Senior Embedded Linux Engineer' AND 'BSP'
   Example of the RIGHT pattern:
     site:linkedin.com/in/ \"Senior Embedded Linux Engineer\" \"BSP\"

0a. NO LITERAL AND BETWEEN TERMS. A space is already an implicit AND on
    Google. Writing the word AND makes Google search for pages containing
    the literal word "AND" — killing your string. OR (uppercase) IS required
    between alternatives, always inside parentheses.

1. MAX 3 SPACE-SEPARATED SIGNALS per string. Google's ranking collapses past 3.
   If you have 5 signals you want, pick the 3 highest-specificity ones and
   drop the rest. More ANDs = fewer results = weaker string.

2. ONLY use real Google X-ray operators. Whitelist:
     site:, intitle:, inurl:, filetype:, -site:, OR (uppercase), \"...\" (quoted phrase)
   FORBIDDEN in X-ray (these look real but Google ignores them, making your string
   return garbage or zero results):
     project:, score:, answers:, experience:, years:, company:, current_company:,
     language:, in:bio, in:readme, in:name
   The last four (language:, in:bio, in:readme, in:name) work inside GitHub's
   native search at github.com/search but NOT through a Google site: query.
   current_company: works in LinkedIn Recruiter ONLY, not in X-ray.

3. NEVER quote single letters. "C" matches every profile with any "c" word.
   If the JD wants C programming, write one of these instead:
     "C/C++"  OR  "embedded C"  OR  "C programming"  OR  "kernel C"
   Same rule for other single letters (R, D). Python, Rust, Go are fine
   because they are unique words.

4. PARENTHESIZE every OR group. Google parses left-to-right without
   parens, which breaks precedence. This is WRONG:
     'speaker' OR 'talk' AND 'embedded'
   This is RIGHT:
     ('speaker' OR 'talk') AND 'embedded'

5. Twitter X-ray is dead in 2025+. site:twitter.com and site:x.com return
   almost nothing because X removed public indexing. Do NOT generate a
   Twitter X-ray; instead, use the slot for a different source
   (e.g., Medium.com for engineering blogs, or a niche community site
   relevant to the role).

6. Stack Overflow X-ray cannot filter by score or answer count from
   Google. Use tag-based URL patterns instead, like:
     site:stackoverflow.com/users "embedded" "[c]" "[arm]"
   Square-bracketed tags are how SO pages label user expertise.

7. For conference/talk searches, the presence of the conference name
   IS the signal. No need to also AND in "speaker" or "talk". Example:
     (site:youtube.com OR site:slideshare.net) "Embedded World" "device driver"
   Three tokens max. That filters harder than six ANDed tokens.

8. Stack Overflow tag searches: use MAX 2 tags ANDed together, not 3+.
   User profile pages are sparse and 3-way tag intersections return zero.
   Pick the 2 most-specific tags for the role. Example for embedded:
     GOOD: site:stackoverflow.com/users "[linux-device-driver]" "[arm]"
     BAD:  site:stackoverflow.com/users "[c]" "[arm]" "[linux-device-driver]" "[kernel]"

9. Personal sites X-ray (intitle:resume OR intitle:CV) is WEAK for roles
   whose practitioners do not self-publish online. Specifically:
     - Embedded/firmware engineers
     - Chip/silicon/ASIC engineers
     - Aerospace and defense engineers (clearances discourage publishing)
     - Senior IC roles at large companies (Qualcomm, Intel, Broadcom, etc.)
   For these roles, DO NOT generate a personal_sites X-ray. Instead use
   the slot for a role-appropriate alternative from this list:
     - kernel.org mailing list: site:lore.kernel.org "device driver" "Signed-off-by:"
     - Patent DB: site:patents.google.com "inventor:" AND domain keywords
     - IEEE Xplore author search: site:ieeexplore.ieee.org "author:" AND keywords
     - USENIX / LWN.net (systems/kernel practitioner writing)
     - RFC authors: site:datatracker.ietf.org AND protocol keywords
   Pick the alternative that matches where THIS role's talent actually
   publishes or participates publicly.

Test each string mentally: would a recruiter pasting this into Google
actually see 20-200 relevant humans in the first page? If the answer is
"zero" or "generic garbage," rewrite.
"""


SKILL_ALTERNATIVES_PROMPT = """You are an expert sourcer with 13+ years of experience.

Given a parsed job description's must-have skills, generate functionally equivalent
alternatives that a grandmaster sourcer would also search for. Most recruiters search
only for the literal skill in the JD. A grandmaster knows that the same role at peer
companies often uses different tooling that produces the same outcome.

PARSED JD CONTEXT:
{parsed_context}

MUST-HAVE SKILLS TO EXPAND:
{skills_list}

For each must-have skill, generate 2-4 functional alternatives. An alternative is:
  - A DIFFERENT TOOL/TECHNOLOGY that produces equivalent outcomes for THIS role at
    THIS company tier. Apache Spark and Snowpark both do distributed compute over
    columnar data; for an analytics role they're functionally equivalent.
  - Used by the SAME PERSON at peer companies that made different stack choices.
  - Something the candidate's resume might list INSTEAD of the JD-listed skill,
    where the candidate would still be qualified.

For each alternative, include:
  - alternative: the tool/technology name (proper-noun, common industry name)
  - context: 1 sentence on WHERE/WHY this alternative gets used instead
  - transferability: "high" (>80% skills overlap, candidate is fully qualified
                     day 1, drop-in replacement),
                     "medium" (50-80% overlap, worth a phone screen — solid
                     transfer but candidate will need 1-2 weeks to ramp),
                     "low" (25-50% overlap, candidate could ramp but isn't
                     ready day 1 — needs 30-60 days)

──────────────────────────────────────────────────────────
DISTRIBUTION CALIBRATION — REQUIRED
──────────────────────────────────────────────────────────

Real-world skill alternative distributions cluster around:
  - ~30% high (genuine drop-in replacements)
  - ~50% medium (worth a phone screen, will need ramp)
  - ~20% low (transferable foundation, longer ramp)

If you mark 80%+ of your alternatives as "high", you are inflating the
ratings. This destroys the recruiters ability to triage candidates —
everything looks equally great, so nothing is actually prioritized.

GOOD CALIBRATION RULES:

A "high" alternative is rare. It means the candidates resume could
literally have one tool name swapped for another and they would still
do the job equivalently from day 1. Examples that genuinely qualify:
  - PostgreSQL <-> MySQL (for app-layer dev — both relational, similar SQL)
  - React <-> Preact (near-identical APIs, same mental model)
  - Apache Kafka <-> Apache Pulsar (similar pub/sub semantics at scale)

Most cross-stack moves are "medium". The candidate has the right mental
model and 50-80% of the tooling, but will spend their first 1-2 weeks
learning the specific quirks of the target stack:
  - Apache Spark -> Snowpark (different runtime, same PySpark-ish API)
  - PyTorch -> TensorFlow (both DL, but different ergonomics)
  - Kubernetes -> ECS (both containers, but very different control planes)
  - Datadog -> New Relic (same APM category, different query languages)

"Low" alternatives are valuable but require real ramp:
  - Pinecone -> FAISS (vector search but different abstraction level)
  - PostgreSQL -> Cassandra (relational vs wide-column — paradigm shift)
  - REST APIs -> gRPC (different mental model, different tooling)

Skip alternatives below 25% overlap. Skip generic synonyms ("PyTorch" -> "Torch"
isn't an alternative, that's the same thing). Focus on STACK SUBSTITUTIONS.

Examples of good calibrated alternatives (note the distribution):
  - Apache Spark -> [Snowpark (medium), Databricks DLT (high), Trino (medium), Polars (low)]
  - Pinecone -> [pgvector (medium), Weaviate (medium), Milvus (medium), FAISS (low)]
  - Kubernetes -> [ECS (medium), Nomad (medium), bare-metal w/ systemd (low)]
  - PyTorch -> [JAX (medium), TensorFlow (medium)]
  - PostgreSQL -> [MySQL (high), CockroachDB (medium), Cassandra (low)]

Notice: across these 5 examples, only 2 of 17 alternatives are "high".
That is the realistic distribution. Most cross-stack candidates need
a phone screen and a ramp period; the "high" rating should be used
sparingly for genuine drop-in replacements.

Examples of BAD alternatives (do not include these patterns):
  - Generic synonyms ("AWS" -> "Amazon Web Services")
  - Complete category swaps ("PyTorch" -> "scikit-learn" — different problem space)
  - Overly broad ("any Python framework" — too vague to be useful)
  - Inflating ratings to "high" when the candidate genuinely needs ramp time

Skip skills that don't have meaningful alternatives. A skill like "U.S. Citizenship"
or "Active Secret Clearance" has no alternative — just omit it from output.

Return STRICT JSON only:

{{
  "skill_alternatives": {{
    "Apache Spark": [
      {{"alternative": "Snowpark", "context": "Snowflake-native shops use this for the same distributed analytics workload", "transferability": "high"}},
      {{"alternative": "Trino", "context": "Open-source query engine over object storage, common at OSS-heavy companies", "transferability": "medium"}}
    ]
  }}
}}

If a skill has no good alternatives, omit it from the output entirely. Do not return
empty arrays.

No em dashes. No code fences. Just JSON.
"""


OBJECTION_PLAYBOOK_PROMPT = """You are an expert sourcer with 13+ years of experience.

Given a parsed job description, produce an OBJECTION-HANDLING PLAYBOOK
that helps the recruiter craft outreach BEFORE the candidate responds
with the predictable "no". Most recruiters get a rejection and scramble.
A grandmaster anticipates the rejection and pre-empts it in the first
message.

PARSED JD CONTEXT:
{parsed_context}

CRITICAL — TRUTHFULNESS RULES (read this first, every time):

A counter that contains an INVENTED fact about the company is worse than
no counter at all. The recruiter pastes it into an InMail, the candidate
asks a follow-up question, and now the recruiter is exposed as either
lying or uninformed. This destroys their credibility and the placement.

You may ONLY reference facts that fit one of these categories:

  ALLOWED — facts visible in the parsed JD context:
    - Role title, level, location, remote policy, industry
    - Comp range and any explicit benefits in the JD
    - Required and preferred skills as stated
    - Company name (only if mentioned in the JD)
    - The role's responsibilities as written in the JD
    - Any explicit clearance, citizenship, or eligibility requirements

  ALLOWED — universally true industry knowledge:
    - "DoD contracts require US citizenship" (true by federal law)
    - "Most defense roles cannot sponsor H1B visas" (regulatory fact)
    - "FAANG L5 total comp typically exceeds $400k" (well-known market data)
    - "ML engineers at top labs typically need PhD or equivalent
       publication record" (industry-recognized norm)
    - General industry trends and common career trajectories

  FORBIDDEN — DO NOT INVENT any of the following, ever:
    - Team size or headcount ("12-person team", "80 engineers")
    - Specific leader names, titles, or career history
       ("led by a former SpaceX avionics engineer")
    - Internal tools or processes not stated in the JD
       ("they use GitLab CI", "agile prototyping", "Python automation")
    - Relocation stipends, signing bonuses, perks not in the JD
       ("$10k relocation stipend", "free lunches")
    - Specific customer names not mentioned in the JD
       ("ships to Stripe and Cloudflare")
    - Technical roadmap or future plans
       ("they're rolling out RISC-V next quarter")
    - Funding details, acquisition rumors, or financial projections
    - Any specific company practice, culture detail, or workplace fact
       not literally stated in the JD

When in doubt, GENERALIZE rather than fabricate. Instead of:
  "The 12-person team is led by a former SpaceX avionics engineer"
write:
  "Defense embedded teams at this scale typically offer more
   ownership-per-engineer than larger primes. The JD emphasizes
   hands-on design across the full lifecycle, which is rare."

The second version uses only the JD's own framing ("hands-on design"
appears in the JD) and a universally-true industry observation. It's
defensible.

For this specific role + company + comp + location combination, generate
3-5 of the most likely candidate objections, each with a SPECIFIC counter
that references what's actually true about THIS opportunity (not generic).

Common objection categories (use the ones that apply, skip the ones that
don't matter for this role):

  industry_perception — "I'd never work at [defense / FAANG / startup /
    legacy / non-mission-driven]". Counter must reference what's
    SURPRISING and TRUE about this specific industry or role —
    using only facts from the JD or universal industry knowledge.

  comp_below_market — "I just got a raise" / "I'm already at $X". Counter
    must address the comp delta gap honestly OR reframe what the role
    offers beyond base. Use only the comp_snapshot from the JD.

  location_remote — "I want fully remote" or "I won't relocate to X".
    Counter must be honest about the requirement AND offer what's
    actually compelling about being there. DO NOT invent stipends or
    perks; reference only what the JD says about location/remote.

  brand_unknown — "I've never heard of this company". Counter is a
    1-paragraph elevator pitch built ONLY from facts in the JD:
    what the company says it does (in the JD), the industry, the
    products mentioned by name in the JD, and any verifiable scale
    indicators the JD provides.

  career_risk — "What if this doesn't work out / the company fails /
    I get RIF'd". Counter addresses general industry stability or
    candidate-side mitigations. DO NOT invent severance terms,
    vesting schedules, or specific company stability claims.

  visa_clearance_blocker — "I don't have clearance" or "I need
    sponsorship". Counter uses only what the JD says about clearance
    and citizenship requirements, plus universal regulatory facts.

  tech_stack_skepticism — "Your stack is ancient" or "I don't want
    to work in [legacy tech]". Counter references ONLY the technologies
    the JD mentions and explains why they matter in this domain. DO NOT
    invent additional modern tooling not stated in the JD.

For each objection in your output:
  - objection_type: one of the categories above
  - likely_phrasing: how the candidate would actually say it
    (1-2 sentences, sounds like a real person, not a script)
  - counter: the recruiter's pre-emptive response.
    Specific to THIS role using ONLY facts from the JD or universal
    industry knowledge. 2-4 sentences max. Should sound like something
    a recruiter would actually paste into an InMail. NO invented facts.
  - confidence: "high" if you're sure this objection will come up,
    "medium" if it might, "low" if it's a long-shot but worth
    preparing for.
  - sources_used: an array of 1-3 short strings naming what the counter
    is grounded in. Each entry is one of:
      "JD: <quote or paraphrase from the JD>"
      "industry: <general industry fact>"
    This gives the recruiter a transparent provenance trail. If you
    cannot fill sources_used with real grounding, you should not be
    writing the counter — drop the objection entirely.
  - safe_to_paste_verbatim: true if every claim in the counter is
    directly traceable to the JD or universal industry knowledge,
    false if the recruiter should verify any specific claim before
    using it.

Pick ONLY the 3-5 most likely objections for THIS role. Quality over
quantity. If you cannot ground a counter in the allowed sources, omit
the objection entirely rather than invent.

Skip the elevator pitch as a separate objection — instead, work it
INTO whichever counter benefits most (usually brand_unknown or
industry_perception), still grounded in JD-only facts.

Return STRICT JSON only. Example showing the SAFE pattern (note how
every claim traces to either the JD or industry knowledge):

{{
  "objection_playbook": [
    {{
      "objection_type": "visa_clearance_blocker",
      "likely_phrasing": "I don't have a security clearance and I'm not sure if I'm eligible for one.",
      "counter": "The JD allows for either an active Secret clearance OR the ability to obtain one, which means US citizenship plus a clean background is the realistic bar — not prior clearance experience. Many embedded engineers in San Diego have moved into cleared work this way; the company sponsors the clearance process. The bigger filter here is the citizenship and eligibility piece, which is non-negotiable for DoD contracts.",
      "confidence": "high",
      "sources_used": [
        "JD: 'Must possess a Secret level security clearance; or the ability to obtain one will be considered'",
        "industry: DoD contracts require US citizenship per federal contracting rules"
      ],
      "safe_to_paste_verbatim": true
    }}
  ]
}}

No em dashes. No code fences. Just JSON.
"""


SEQUENCED_PLAY_PROMPT = """You are an expert sourcer with 13+ years of experience.

Given a parsed job description, produce a SEQUENCED 21-DAY SOURCING PLAY
that a recruiter can follow day-by-day. Most recruiters do one LinkedIn
blast on day 1, wait a week, then complain candidates aren't responding.
A grandmaster sequences: warmest channels first, progressively broader
outreach, then unconventional channels, then back-channels.

PARSED JD CONTEXT:
{parsed_context}

TIER 1 COMPANIES (if known):
{tier1_companies}

WATERING HOLES (if known):
{watering_holes}

Produce a 5-phase sequenced play covering days 1 through 22+. Each phase
has a different channel mix, different message style, different urgency
level, and a different expected response rate.

The 5 phases (all required, in order):

  Phase 1 — Days 1-3 — Warm-channel opener
    Channels: 1st-degree LinkedIn connections, alumni networks, referrals
    from current employees at Tier 1 companies, past placements the
    recruiter already knows. No cold yet.
    Message style: personal, concise, direct ask for intro or interest.
    Expected response rate: 30-50%. Tiny universe, high-quality signal.

  Phase 2 — Days 4-7 — Tier 1 cold with hyper-personalization
    Channels: Tier 1 target-company employees via LinkedIn Recruiter /
    InMail, outreach via verified personal email (Hunter/Apollo).
    Message style: references something SPECIFIC about the candidate —
    their recent talk, OSS commit, patent, promotion, their company's
    recent news (layoff, acquisition, IPO). First line should prove the
    recruiter actually looked at their profile.
    Expected response rate: 8-15%.

  Phase 3 — Days 8-14 — Tier 2 broader outreach
    Channels: Tier 2 companies, less-personalized but still role-fit
    targeted. Template-based with 2-3 customized fields.
    Message style: leads with the ROLE + COMP + COMPANY story since less
    personal context exists per candidate. Volume game.
    Expected response rate: 3-7%.

  Phase 4 — Days 15-21 — Unconventional channels + watering holes
    Channels: X-ray (personal sites, GitHub, Stack Overflow), niche
    communities (specific Discords, mailing lists, conference speakers),
    the watering_holes from the parsed JD.
    Message style: venue-specific. Reach out as a peer, not a recruiter.
    Reference the work they posted. These candidates are often NOT
    actively job-searching and respond to curiosity, not pitches.
    Expected response rate: 10-20% from a much smaller universe.

  Phase 5 — Days 22+ — Back-channels + parallel escalation
    Channels: recruiters' Discord groups, friend-of-friend referrals,
    former colleagues. If the search is still open past 21 days, this
    is where grandmasters ask their network for intros directly.
    Also: revisit Phase 2 candidates who didn't respond with a new
    angle (often: a news hook — their company just announced layoffs,
    a comp change, a reorg).
    Message style: asking for intros or advice, not pitching the role.
    Expected response rate: varies wildly — depends on network depth.

For each phase, produce:
  - phase: 1-5
  - name: short title ("Warm-channel opener", "Tier 1 cold")
  - days: string ("Days 1-3", "Days 22+")
  - channels: array of 2-4 specific channel names (use the Tier 1
    companies and watering holes provided, don't be generic)
  - message_style: 1-sentence description of the voice and angle
  - first_move: the ONE specific action to take on day 1 of this phase.
    Has to be concrete in SHAPE but NEVER fabricate specific candidate
    details. Bad: "message Sarah Chen about her power-sequencing PR".
    Good: "send InMail to 8-12 Tier 1 embedded engineers who have merged
    PRs in the OpenBMC repo in the last 90 days, leading with the specific
    repo area their commits touched (fan control, sensor monitoring, or
    power sequencing)". The shape is concrete; the candidate-specific
    detail stays a placeholder for the recruiter to fill in.
  - expected_response_rate: string ("30-50%", "8-15%", etc.)

Make it specific to THIS role, company, and watering holes. Generic
advice fails. Reference the Tier 1 companies and watering holes that
were passed in.

──────────────────────────────────────────────────────────
TRUTHFULNESS RULES — MANDATORY
──────────────────────────────────────────────────────────

A first_move that contains an INVENTED candidate detail is worse than no
first_move at all. The recruiter will paste it into outreach, the candidate
asks "how did you find my PR on X?" — and the recruiter is exposed because
that PR doesnt exist.

You may ONLY reference facts that fit one of these categories:

  ALLOWED — facts visible in the parsed JD context:
    - Tier 1 / Tier 2 company names from the JD parser output
    - Watering hole venues from the watering_holes list provided
    - Role title, level, location, industry from the parsed JD
    - Skills explicitly mentioned in the JD

  ALLOWED — universally true industry knowledge:
    - "Most LR-based searches Tuesday-Thursday outperform Monday or Friday"
    - "OpenBMC mailing list traffic peaks mid-week"
    - "FAANG L5 engineers respond more to comp + scope than equity hooks"

  FORBIDDEN — DO NOT INVENT any of the following, ever:
    - Specific candidate names ("Sarah Chen", "Jian Wei")
    - Specific candidate work products ("their fan control PR",
      "their JTAG debugging talk", "their patent on power sequencing")
    - Specific recent events at named companies that you did not see
      in the JD ("Intels recent firmware reorg", "AMDs Austin layoffs")
    - Internal Discord channels, Slack workspaces, or alumni networks
      not literally named in the watering_holes input
    - University names not in the parsed JD or watering holes
    - Technical conference dates / locations / agendas

When you would otherwise fabricate, GENERALIZE. Instead of:
  "Reference Sarah Chens fan control PR from August"
write:
  "Reference a recent commit they made to a relevant subsystem in the
   OpenBMC repo (fan control, sensor monitoring, power sequencing, etc)
   if their author history shows one"

Instead of:
  "Mention Intels recent layoffs in the firmware org"
write:
  "If a Tier 2 company has had recent public news (layoffs, reorg,
   acquisition) in the last 90 days, reference it as a re-engagement hook"

The second versions describe a SHAPE of action. The recruiter fills in the
specific detail before sending. This is the difference between a useful
playbook and a fabrication that destroys credibility on the first reply.

Apply this rule to BOTH first_move AND message_style. Phrases like
"reference their recent OSS commit" are fine; "reference their kernel
patch on i2c-mux from October" is invention.

Return STRICT JSON only:

{{
  "sequenced_play": [
    {{
      "phase": 1,
      "name": "Warm-channel opener",
      "days": "Days 1-3",
      "channels": ["1st-degree LinkedIn connections at Fluke and Keysight", "UCSD alumni network", "Former colleagues from past embedded placements"],
      "message_style": "Personal, concise, direct ask for intro or interest. Under 100 words.",
      "first_move": "Post in UCSD embedded systems alumni Slack with a 2-sentence description of the role and ask for intros. Message the 3-5 known Fluke/Keysight 1st-degree connections asking if they know anyone open to conversations.",
      "expected_response_rate": "30-50%"
    }}
  ]
}}

No em dashes. No code fences. Just JSON.
"""





PRO_INTAKE_PROMPT = """You are a senior technical recruiter with 13+ years at FAANG-tier companies.
You have negotiated hundreds of req-defining conversations with hiring managers.

You are doing the "Pro skill briefing" pass on a parsed JD. The free tier already
classified each must-have skill as blocker vs preferred. Your job is to go deeper:
  - Re-classify into THREE tiers based on REAL hiring impact (not what the JD claims)
  - Provide rationale grounded in JD quotes plus your domain knowledge
  - Identify which interview stage each skill actually gets tested at
  - Give the recruiter language to push back on the hiring manager
  - Suggest acceptable substitutions (so a strong candidate isn't filtered out
    just because their resume uses different keywords)

PARSED CONTEXT:
{parsed_context}

MUST-HAVE SKILLS TO ANALYZE:
{must_have_list}

RAW JD (for grounding quotes):
{jd_excerpt}

──────────────────────────────────────────────────────────
TIER DEFINITIONS — these are the ONLY valid tier values
──────────────────────────────────────────────────────────

  tier 1 — NON-NEGOTIABLE
    Without this skill the candidate gets auto-rejected at the resume screen.
    Hiring manager will not even take a phone screen. There is no candidate
    success path that bypasses this skill.
    HARD CAP: maximum 3 skills can be Tier 1.

  tier 2 — STRONG PREFERENCE
    Listed as required in the JD, but a strong candidate missing this can
    still get an interview if they have a credible substitute or strong
    other-dimension signal. The recruiter will need to advocate for them.

  tier 3 — NICE-TO-HAVE (ACTUALLY)
    The JD says "required" but realistically the hiring manager will trade
    this off for almost any candidate who covers the Tier 1 and Tier 2
    requirements well. Most "team player / strong communication" lines are
    here unless the role is customer-facing.

The whole point: most JDs list 8-15 "required" skills. In reality, 2-3 are
true Tier 1 blockers and the rest are negotiable. A senior recruiter knows
which is which. Your job is to surface that distinction explicitly.

──────────────────────────────────────────────────────────
INTERVIEW STAGE — the ONLY valid values
──────────────────────────────────────────────────────────

  resume_screen        — assessed from resume keywords + recent companies
  phone_screen         — comes up in a 30-min recruiter or HM screen
  onsite_technical     — tested in a coding/system-design/take-home
  not_directly_tested  — inferred from background; never directly assessed

Map each skill to the stage where it actually gets tested. Don't guess.
"Strong communication skills" is not_directly_tested at resume_screen but
shows up as a yes/no signal at phone_screen. "Distributed systems" is
phone_screen for level confirmation and onsite_technical for the deep dive.

──────────────────────────────────────────────────────────
TRUTHFULNESS RULES — MANDATORY
──────────────────────────────────────────────────────────

For every entry, you MUST populate the grounded_in array with literal phrases
from the JD or pieces of the parsed_context. If you cannot find a JD quote
or context piece that justifies a tier or interview-stage classification,
default to a more conservative tier (move from 1 to 2, or 2 to 3).

NEVER fabricate:
  - Specific years of experience that aren't in the JD
  - Specific tools/frameworks the JD doesn't mention
  - Hiring manager preferences that aren't in the JD
  - Compensation tradeoffs based on imagined budget conversations
  - Team size, reporting structure, or org details not in the JD

If a skill genuinely cannot be classified from the JD alone (because the JD
is sparse), set safe_to_paste_verbatim to false and write a rationale that
says so explicitly: "JD is sparse on this — recommend asking the hiring
manager directly whether X is hard-required or negotiable."

The recruiter will paste your output into Slack to brief their hiring
manager. If you fabricate, you damage the recruiter's credibility.

──────────────────────────────────────────────────────────
PUSHBACK GUIDANCE — what to write
──────────────────────────────────────────────────────────

For each skill, write 1-2 sentences the recruiter would say to the hiring
manager when defending a candidate who is missing this skill. Be specific
to the tier:

  tier 1: "This is non-negotiable — we'd be wasting the panel's time
          interviewing without it" (or similar firm language)

  tier 2: "If we're seeing a candidate strong on Tier 1 skills who has
          [substitute X] instead of [exact JD requirement Y], I'd push to
          phone-screen them. Here's why: [reason grounded in domain]."

  tier 3: "I'm going to deprioritize this in screens and only flag
          candidates who have it as an unexpected bonus."

Tone: peer-to-peer. The recruiter and the hiring manager are colleagues.
No salesy language. No "I'd love to discuss." Just the call.

──────────────────────────────────────────────────────────
ACCEPTABLE SUBSTITUTIONS
──────────────────────────────────────────────────────────

For each skill, give 1-3 acceptable substitutions that should NOT
auto-disqualify a candidate. Examples:

  JD says "Kafka" → acceptable substitutions: ["Apache Pulsar", "AWS Kinesis", "Redpanda"]
  JD says "PyTorch" → acceptable substitutions: ["JAX", "TensorFlow 2.x"]
  JD says "Kubernetes" → acceptable substitutions: ["Nomad", "ECS at scale"]

If the skill is truly unique (no real substitutes — e.g., "FDA 510(k) clearance
process"), return an empty array and note in pushback_guidance that there
genuinely is no substitute.

──────────────────────────────────────────────────────────
OUTPUT SCHEMA — RETURN ONLY THIS JSON
──────────────────────────────────────────────────────────

{{
  "pro_skill_briefing": [
    {{
      "skill": "exact skill name from must_have_list",
      "tier": 1,
      "tier_label": "non-negotiable",
      "rationale": "2-3 sentences on WHY this is in this tier, grounded in the JD",
      "interview_stage": "onsite_technical",
      "pushback_guidance": "what the recruiter says to the HM",
      "acceptable_substitutions": ["sub1", "sub2"],
      "grounded_in": [
        "JD: literal phrase from the JD",
        "context: piece from parsed_context that supports this"
      ],
      "safe_to_paste_verbatim": true
    }}
  ]
}}

Every must_have_list entry must appear in pro_skill_briefing exactly once.
No skipping. No duplicates.

No em dashes. No code fences. JSON only.
"""

PRO_BOOLEAN_PROMPT = """You are an expert sourcer with 13+ years of Boolean search experience.
You have done thousands of senior technical searches at FAANG-tier and venture-backed companies.

This is the Pro Boolean extensions pass. The free tier already produced:
  - 3 LinkedIn Recruiter strings (sniper / precision / expanded)
  - 7 X-ray strings (linkedin, github, medium, stackoverflow, conferences, personal_sites, specialty)
  - Tier 1 / Tier 2 company cluster names

Your job is to extend that with everything a senior sourcer would do but a junior wouldn't:
  - Annotate WHY each existing LR tier is structured the way it is
  - Add 2 NEW LR tiers (Dragnet for desperate-mode breadth, Company-targeted for direct poaching)
  - Add rationale on each Tier 1 + Tier 2 company (why poach from THEM specifically)
  - Convert the JD's watering_holes into runnable X-ray strings (role-aware: which venues match THIS specific archetype)
  - Estimate hit volume + signal/noise per Pro string so the recruiter knows what to expect
  - Extend mentor notes to 5-8 tactical tips, not 3

PARSED CONTEXT:
{parsed_context}

EXISTING FREE-TIER OUTPUT (that you are extending — do NOT regenerate, only ANNOTATE):
{existing_booleans}

WATERING HOLES from the JD parser pass (your raw material for Pro X-rays):
{watering_holes_list}

──────────────────────────────────────────────────────────
RATIONALE on existing 3 LR tiers
──────────────────────────────────────────────────────────

For each of sniper / precision / expanded, write 1-2 sentences explaining:
  - WHY this string is structured this way (why these specific terms)
  - When to use this vs the others
  - What kind of candidate it surfaces

Tone: peer-to-peer. Like a senior sourcer explaining their reasoning to a mid-level peer.

──────────────────────────────────────────────────────────
NEW LR Tier 4: DRAGNET (widest possible net)
──────────────────────────────────────────────────────────

When sniper/precision/expanded are all dry, the dragnet runs. Should:
  - Drop most "preferred" requirements
  - Use only Tier 1 blocker skills + level signals
  - Open up location to multi-state or even nationwide
  - Drop seniority constraints if the role allows
  - Expected hits: 1000-5000 results
  - Use case: dry market, willing to accept lower fit for higher volume

LR syntax: title:, location:, current_company:, AND keyword OK in LR.

──────────────────────────────────────────────────────────
NEW LR Tier 5: COMPANY-TARGETED (direct poach)
──────────────────────────────────────────────────────────

Names specific Tier 1 + Tier 2 companies in the LR string itself:
  current_company:('Company A' OR 'Company B' OR 'Company C')
  AND title:('Role A' OR 'Role B')

Use case: when warm-channel and watering-hole sourcing are exhausted and
you're going direct. Explicit poaching list.

NEVER include the hiring company in the company-targeted string.

──────────────────────────────────────────────────────────
COMPANY CLUSTER RATIONALE
──────────────────────────────────────────────────────────

For each Tier 1 and Tier 2 company already in the existing output, write
1 sentence explaining WHY it's a poaching target. Examples:

  Tier 1 NVIDIA: "Direct competitor in cloud AI infrastructure. Their data
  center BMC team is the most direct functional analog to this role."

  Tier 2 Supermicro: "Server hardware manufacturer with deep BMC expertise.
  Their firmware engineers ship at scale into hyperscaler customers."

Be specific. Don't write "they are a tech company." Write what makes them
a defensible source for THIS role.

──────────────────────────────────────────────────────────
WATERING HOLE -> RUNNABLE X-RAY conversion
──────────────────────────────────────────────────────────

For each watering hole in the input, produce a runnable X-ray string
following the same rules as the free-tier X-rays:
  - DOUBLE quotes around phrases (escape them as \"...\")
  - NO literal AND keyword (Google treats space as implicit AND)
  - OR (uppercase) inside parentheses for alternatives
  - site: operator
  - Skip generic catch-alls

Each Pro X-ray gets:
  - venue_name (matches the watering_hole entry)
  - venue_type (mailing_list / conference / etc)
  - xray_string (the actual runnable string)
  - signal: what kind of candidate signal this surfaces
  - hit_volume: low (<50) / medium (50-500) / high (500+)
  - signal_to_noise: high (most results are real candidates) / medium / low

──────────────────────────────────────────────────────────
DIFFICULTY SCORING
──────────────────────────────────────────────────────────

For every NEW Pro string (the 2 LR tiers + each Pro X-ray), include:
  - hit_volume: low / medium / high
  - signal_to_noise: high / medium / low

Be honest. If the dragnet returns 5000 mostly-noise hits, mark it
hit_volume=high, signal_to_noise=low. The recruiter needs to know
what they're getting into.

──────────────────────────────────────────────────────────
EXTENDED MENTOR NOTES (5-8 tactical tips)
──────────────────────────────────────────────────────────

Free tier has 3 notes. Pro extends to 5-8. New notes should cover:
  - Sequencing: which string to run FIRST and why
  - Time-of-day or day-of-week tactics if relevant
  - Specific watering hole insights (e.g., "openbmc.dev mailing list activity peaks Tuesdays")
  - Skill substitution patterns (which JD skills to relax first if results are dry)
  - Compensation positioning (when to lead with cash vs equity in InMails)
  - Anti-patterns: things a junior sourcer might try that wastes time

──────────────────────────────────────────────────────────
RETURN ONLY THIS JSON
──────────────────────────────────────────────────────────

{{
  "lr_rationale": {{
    "sniper": "1-2 sentences on why this string is structured this way and when to use it",
    "precision": "...",
    "expanded": "..."
  }},
  "lr_dragnet": {{
    "string": "LR syntax string for the widest possible net",
    "rationale": "1-2 sentences on when to deploy this",
    "hit_volume": "high",
    "signal_to_noise": "low"
  }},
  "lr_company_targeted": {{
    "string": "LR syntax with current_company:('A' OR 'B' OR 'C') AND title:('X' OR 'Y')",
    "rationale": "1-2 sentences on why these companies + how to follow up",
    "hit_volume": "medium",
    "signal_to_noise": "high"
  }},
  "company_cluster_rationale": {{
    "tier_1": [
      {{"company": "Company A", "rationale": "1 sentence on why this is a defensible source for THIS role"}}
    ],
    "tier_2": [
      {{"company": "Company B", "rationale": "..."}}
    ]
  }},
  "pro_xrays": [
    {{
      "venue_name": "openbmc.dev mailing list",
      "venue_type": "mailing_list",
      "xray_string": "site:lists.ozlabs.org \"openbmc\" \"patch\" \"review\"",
      "signal": "Active OpenBMC maintainers — patch submissions = real production-grade contribution",
      "hit_volume": "low",
      "signal_to_noise": "high"
    }}
  ],
  "extended_mentor_notes": [
    {{"label": "Sequencing", "note": "Run the GitHub OpenBMC X-ray first — merged PRs are higher signal than LR for this archetype"}},
    {{"label": "Anti-pattern", "note": "Don't blast InMails on Mondays — Staff-level engineers triage their inbox Sunday night"}}
  ]
}}

No em dashes. No code fences. JSON only.
"""

CANDIDATE_EVAL_PROMPT = """You are an expert technical recruiter with 13+ years of experience evaluating candidates.

You receive two inputs: a parsed job requisition and a raw candidate profile (could be a LinkedIn dump, resume text, or pasted notes).

Your job is to produce a clear, actionable evaluation that a senior recruiter would write before submitting a candidate to a hiring manager.

PARSED REQUISITION:
{parsed_jd}

CANDIDATE PROFILE:
{candidate_text}

Score the candidate honestly. Do NOT inflate scores to be polite. A candidate who fails a blocker should NOT score above 60. A candidate who matches every blocker AND most preferred skills should score 85+.

Scoring rubric:
- 90-100: Strong submit. All blockers met, most preferred met, evidence of impact at appropriate level.
- 75-89: Submit with caveats. All blockers met but gaps in preferred or seniority signal.
- 60-74: Borderline. One blocker is weak or unclear. Worth a screen call to verify.
- 40-59: Pass with feedback. Multiple blockers weak or missing.
- 0-39: Hard pass. Fundamental mismatch.

Return ONLY valid JSON with this shape:
{{
  "fit_score": 0-100,
  "recommendation": "SUBMIT|INTERVIEW|PASS",
  "headline": "1-sentence summary a hiring manager would read first",
  "summary": "2-3 sentences on why this score, what stands out, what concerns",
  "extracted_skills": [
    {{"name": "PyTorch", "evidence": "3yr building recommendation models at Pinterest", "recency": "current", "depth": "production", "confidence": 0.9}}
  ],
  "blocker_assessment": [
    {{"skill": "...", "status": "met|partial|missing|unclear", "evidence": "specific quote or signal from profile, or 'not found'"}}
  ],
  "preferred_assessment": [
    {{"skill": "...", "status": "met|partial|missing|unclear", "evidence": "..."}}
  ],
  "strengths": ["specific strength 1", "specific strength 2", "..."],
  "risks_to_probe": ["question or concern 1", "question or concern 2", "..."],
  "interview_questions": [
    {{"question": "...", "what_to_listen_for": "..."}},
    {{"question": "...", "what_to_listen_for": "..."}}
  ],
  "comp_check": "1 sentence on whether candidate's likely current/expected comp fits the role's range, or 'unknown' if no signal"
}}

Rules:
- No em dashes anywhere
- "evidence" must be specific. Quote from profile when possible. "not found" is honest if the signal isn't there.
- 3-5 blocker_assessment entries, 2-4 preferred_assessment entries
- 3-5 strengths, 2-4 risks_to_probe, 3-5 interview_questions
- Interview questions should be specific to this candidate's gaps and strengths, not generic
- recommendation must align with fit_score (90+ = SUBMIT, 60-89 = INTERVIEW, <60 = PASS)
- extracted_skills: list ALL technical skills the candidate demonstrates, 5-15 entries, one per skill.
  Use canonical names when possible (e.g. "PyTorch" not "Torch", "Apache Spark" not "Spark").
  recency: "current" (at current job) | "recent" (1-3yr ago) | "dated" (3+ yr ago)
  depth: "expert" (taught/designed/deep) | "production" (shipped) | "project" (side work) | "mentioned" (listed only)
  confidence: 0.0-1.0 (how sure you are based on the evidence)
- No code fences, no preamble. Just JSON.
"""


# ---------- MODELS ----------

class IntakeRequest(BaseModel):
    jd_text: str = Field(..., min_length=50)
    org_name: Optional[str] = None
    req_title: Optional[str] = None


class ByokRequest(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|together)$")
    api_key: str = Field(..., min_length=10)


class CandidateEvalRequest(BaseModel):
    req_id: str = Field(..., min_length=1)
    candidate_text: str = Field(..., min_length=50, description="Raw candidate profile: LinkedIn dump, resume, or notes")
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    source: Optional[str] = None  # where you found them: "linkedin", "github", "referral", etc.



# ---------- EMAIL HELPERS ----------

async def _send_email(to: str, subject: str, html: str) -> bool:
    """Send a transactional email via Resend. Returns True on success.

    Used by intake-completion retention emails (and future flows). Magic-link
    sending stays inline in /api/auth/magic-link because that flow has more
    elaborate error handling around login_attempts logging.

    Caller policy: this helper SWALLOWS errors after logging them. Intake
    emails are best-effort; a Resend hiccup must NEVER break a successful
    intake response. If you need send-or-fail semantics, write a different
    helper.
    """
    if not RESEND_API_KEY:
        print(f"[email] SKIP to={to} reason=no-resend-key")
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": "SourcingNav <hello@sourcingnav.com>",
                    "to": to,
                    "subject": subject,
                    "html": html,
                },
            )
        if r.status_code >= 400:
            print(f"[email FAIL] to={to} status={r.status_code} body={r.text[:200]}")
            return False
        print(f"[email OK] to={to} subject={subject[:60]}")
        return True
    except Exception as e:
        print(f"[email ERROR] to={to} type={type(e).__name__} err={str(e)[:200]}")
        return False


def _build_intake_completion_email(
    parsed: dict,
    booleans: dict,
    req_id: str,
) -> tuple[str, str]:
    """Build (subject, html) for the post-intake retention email.

    Goal: give the user something USEFUL in their inbox they can act on
    without logging back in. Specifically:
      - Subject names the role + company (search-from-inbox handle)
      - Body shows the top 3 most-likely-to-be-used strings
      - Closes with a 'next move' nudge from mentor_notes
      - Direct link back to the req for the full output

    All inputs come from the same `parsed` and `booleans` dicts the API
    response returns, so we know the shape; defensive .get() everywhere
    in case the JD parser produced an unusual shape.
    """
    core = parsed.get("core", {}) or {}
    role = core.get("role_title") or "your search"
    company = core.get("company") or "this role"

    # Subject — specific, useful as an inbox handle later
    subject = f"Sourcing kit ready: {role} at {company}"

    # Pull the three highest-leverage strings:
    #   1. LR sniper (tightest match — what they'll run first)
    #   2. GitHub X-ray (highest signal for technical archetypes)
    #   3. Best watering-hole (the unique-to-this-role insight)
    #
    # If anything is missing, the section just doesn't render — better to
    # ship a slightly thinner email than to put placeholder text in front
    # of the user.
    lr_strings = booleans.get("linkedin_recruiter") or []
    sniper = next((s for s in lr_strings if (s.get("tier") or "").lower() == "sniper"), None)

    xrays = booleans.get("xray_searches") or []
    github_xray = next((x for x in xrays if "github" in (x.get("platform") or "").lower()), None)

    holes = parsed.get("watering_holes") or []
    top_hole = holes[0] if holes else None

    # Pull the mentor note's first tip — that's the 'do this first' nudge
    mentor = booleans.get("mentor_notes") or {}
    next_move = (
        mentor.get("best_xray_to_start")
        or mentor.get("pro_tip")
        or "Run the GitHub X-ray first — public commits are the highest signal for technical roles."
    )

    req_url = f"https://sourcingnav.com/app/pipeline.html?req={req_id}"

    # HTML — minimal styling, mobile-readable, no images (better deliverability)
    parts = []
    parts.append(f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1a1a1a;">
      <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a1a1a;">Your sourcing kit for <span style="color:#2d7eb8;">{role}</span></h2>
      <p style="margin:0 0 24px 0;color:#666;font-size:14px;">at {company}. Here are the three strings worth running first.</p>
    """)

    if sniper and sniper.get("string"):
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #2d7eb8;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#2d7eb8;margin-bottom:6px;">LinkedIn Recruiter — Sniper (start here)</div>
        <div style="font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a;word-break:break-word;line-height:1.5;">{sniper["string"]}</div>
      </div>
        """)

    if github_xray and github_xray.get("string"):
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #4a9d4a;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#4a9d4a;margin-bottom:6px;">GitHub X-ray — public code, highest signal</div>
        <div style="font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a;word-break:break-word;line-height:1.5;">{github_xray["string"]}</div>
      </div>
        """)

    if top_hole and top_hole.get("how_to_use"):
        venue = top_hole.get("venue", "specialty venue")
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #b85e2d;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#b85e2d;margin-bottom:6px;">Watering hole — {venue}</div>
        <div style="font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a;word-break:break-word;line-height:1.5;">{top_hole["how_to_use"]}</div>
      </div>
        """)

    parts.append(f"""
      <div style="margin:24px 0 18px 0;padding:14px;background:#fff8e1;border:1px solid #f0d97a;border-radius:6px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#996600;margin-bottom:6px;">What to run first</div>
        <div style="font-size:14px;color:#5a4500;line-height:1.5;">{next_move}</div>
      </div>

      <div style="margin:32px 0 0 0;text-align:center;">
        <a href="{req_url}" style="display:inline-block;padding:12px 24px;background:#2d7eb8;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;">View the full sourcing kit →</a>
      </div>

      <div style="margin:32px 0 0 0;padding-top:20px;border-top:1px solid #eee;font-size:12px;color:#999;text-align:center;">
        Sent because you ran an intake on SourcingNav. <a href="https://sourcingnav.com/app/settings.html" style="color:#999;">Manage emails</a>
      </div>
    </div>
    """)

    return subject, "".join(parts)


# ---------- PRODUCTION ROUTES ----------

@app.post("/api/auth/magic-link")
async def send_magic_link(
    req: MagicLinkRequest,
    x_forwarded_for: Optional[str] = Header(None),
    x_real_ip: Optional[str] = Header(None),
):
    if not RESEND_API_KEY:
        raise HTTPException(500, "Email service not configured")
    if not MAGIC_LINK_SECRET:
        raise HTTPException(500, "Magic link secret not configured")

    # Get client IP from Vercel headers
    ip = None
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    elif x_real_ip:
        ip = x_real_ip

    # Step 0: rate limit check (raises 429 if over limits)
    try:
        await check_magic_link_rate_limit(req.email, ip)
    except HTTPException:
        raise
    except Exception as e:
        # If rate-limit DB query fails, log and continue (don't block legit logins)
        pass

    # Step 1: ensure user exists
    try:
        async with db() as client:
            rs = await client.execute("SELECT id FROM users WHERE email = ?", [req.email])
            if not rs.rows:
                user_id = str(uuid.uuid4())
                await client.execute("INSERT INTO users (id, email) VALUES (?, ?)", [user_id, req.email])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Database error: {type(e).__name__}: {str(e)[:200]}")

    # Step 2: sign token
    try:
        token = sign_token(req.email)
    except Exception as e:
        raise HTTPException(500, f"Token signing error: {type(e).__name__}: {str(e)[:200]}")

    link = f"https://sourcingnav.com/app/?token={token}"

    # Step 3a: log the attempt FIRST (so rate-limiting works even when email send fails).
    # If this isn't here, an attacker hitting the endpoint with random emails would
    # never get rate-limited because Resend would reject the send and we'd skip logging.
    await log_login_attempt(req.email, ip, success=False)

    # Step 3b: send email via Resend
    # sourcingnav.com is verified at resend.com/domains. DKIM is published
    # at resend._domainkey.sourcingnav.com (root). Resend's Improved
    # Deliverability also installs SPF + bounce handling on the send.
    # subdomain, but the FROM address must be on the registered root,
    # not the send subdomain — that's internal Resend infrastructure.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": "SourcingNav <hello@sourcingnav.com>",
                    "to": req.email,
                    "subject": "Your SourcingNav login link",
                    "html": (
                        f"<p>Click to sign in to SourcingNav:</p>"
                        f"<p><a href='{link}'>{link}</a></p>"
                        f"<p>This link expires in 15 minutes.</p>"
                        f"<p style='color:#888;font-size:12px'>If you didn't request this, ignore this email.</p>"
                    ),
                },
            )
        if r.status_code >= 400:
            try:
                err_body = r.json()
                msg = err_body.get("message") or err_body.get("error") or r.text[:200]
            except Exception:
                msg = r.text[:200]
            raise HTTPException(500, f"Email send failed ({r.status_code}): {msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Email error: {type(e).__name__}: {str(e)[:200]}")

    # Step 4: mark the attempt as successful (delivered)
    try:
        async with db() as client:
            await client.execute(
                """UPDATE login_attempts SET success = 1
                   WHERE email = ? AND attempted_at > datetime('now', '-1 minute')""",
                [req.email],
            )
    except Exception:
        pass

    return {"ok": True, "message": "Check your email for the login link"}


@app.post("/api/auth/verify")
async def verify(
    req: VerifyTokenRequest,
    x_forwarded_for: Optional[str] = Header(None),
    x_real_ip: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None),
):
    """Verify a magic-link token and exchange it for a long-lived session token.

    Magic-link token: 15-min expiry, sent via email, used once.
    Session token: 30-day expiry, stored in browser localStorage.
    Creates a row in the sessions table so the session can be revoked later.
    """
    email = verify_token(req.token)
    if not email:
        raise HTTPException(401, "Invalid or expired token")

    ip = None
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    elif x_real_ip:
        ip = x_real_ip

    async with db() as client:
        rs = await client.execute("SELECT id, mode, plan FROM users WHERE email = ?", [email])
        if not rs.rows:
            raise HTTPException(404, "User not found")
        user_id, mode, plan = rs.rows[0]

        session_token = sign_token(email, exp_minutes=30 * 24 * 60, kind="session")
        token_hash = _hash_token(session_token)
        session_id = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        try:
            await client.execute(
                """INSERT INTO sessions
                   (id, user_id, session_token_hash, user_agent, ip_address, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [session_id, user_id, token_hash, user_agent, ip, expires_at],
            )
        except Exception:
            pass

        try:
            await client.execute(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, last_login_ip = ? WHERE id = ?",
                [ip, user_id],
            )
        except Exception:
            pass

    return {
        "access_token": session_token,
        "user": {"id": user_id, "email": email, "mode": mode, "plan": plan},
    }


@app.get("/api/auth/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    """List all active sessions for the current user."""
    async with db() as client:
        rs = await client.execute(
            """SELECT id, user_agent, ip_address, created_at, last_used_at, expires_at
               FROM sessions
               WHERE user_id = ? AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
               ORDER BY last_used_at DESC""",
            [user["id"]],
        )
        return {
            "sessions": [
                {"id": r[0], "user_agent": r[1], "ip_address": r[2],
                 "created_at": r[3], "last_used_at": r[4], "expires_at": r[5]}
                for r in rs.rows
            ]
        }


@app.post("/api/auth/logout")
async def logout(authorization: str = Header(None)):
    """Revoke the current session."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    token_hash = _hash_token(token)
    async with db() as client:
        await client.execute(
            "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP, revoke_reason = 'user_logout' WHERE session_token_hash = ?",
            [token_hash],
        )
    return {"ok": True}


@app.post("/api/auth/logout-all")
async def logout_all(user: dict = Depends(get_current_user)):
    """Revoke ALL sessions for the current user."""
    async with db() as client:
        await client.execute(
            """UPDATE sessions
               SET revoked_at = CURRENT_TIMESTAMP, revoke_reason = 'user_logout_all'
               WHERE user_id = ? AND revoked_at IS NULL""",
            [user["id"]],
        )
    return {"ok": True, "message": "All sessions revoked. Sign in again on every device."}


@app.delete("/api/auth/sessions/{session_id}")
async def revoke_session(session_id: str, user: dict = Depends(get_current_user)):
    """Revoke a specific session by ID."""
    async with db() as client:
        await client.execute(
            """UPDATE sessions
               SET revoked_at = CURRENT_TIMESTAMP, revoke_reason = 'user_revoked'
               WHERE id = ? AND user_id = ?""",
            [session_id, user["id"]],
        )
    return {"ok": True}


@app.get("/api/user/me")
async def get_me(user: dict = Depends(get_current_user)):
    try:
        async with db() as client:
            rs = await client.execute(
                """SELECT plan, usage_intake, usage_eval, usage_outreach,
                          byok_provider, usage_reset_at, created_at
                   FROM users WHERE id = ?""",
                [user["id"]],
            )
            if not rs.rows:
                raise HTTPException(404, "User not found in DB")
            r = rs.rows[0]

            # Compute days_until_reset for the UI to show "resets in N days".
            # Mirrors the lazy reset logic in check_cap.
            anchor = r[5] or r[6]  # usage_reset_at or created_at
            days_until_reset = None
            if anchor and r[0] == "free":
                days_check = await client.execute(
                    "SELECT MAX(0, CAST(? - (julianday('now') - julianday(?)) AS INTEGER))",
                    [FREE_PERIOD_DAYS, anchor],
                )
                if days_check.rows and days_check.rows[0]:
                    days_until_reset = int(days_check.rows[0][0] or 0)

            return {
                **user, "plan": r[0],
                "usage": {"intake": r[1] or 0, "eval": r[2] or 0, "outreach": r[3] or 0},
                "caps": FREE_CAPS if r[0] == "free" else {"intake": 100, "eval": None, "outreach": None},
                "usage_reset_at": r[5],
                "days_until_reset": days_until_reset,
                "period_days": FREE_PERIOD_DAYS,
                "byok_provider": r[4],
                "byok_configured": bool(r[4]),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[me] {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/user/byok-key")
async def save_byok_key(req: ByokRequest, user: dict = Depends(get_current_user)):
    """Deprecated. BYOK has been removed entirely.

    Recruiters (our target audience) don't know what an API key is.
    All users now use the server-keyed failover path in call_ai().
    The byok_provider / byok_key_enc columns remain in the users table
    for backwards compat but are no longer read by call_ai().

    Endpoint kept (returning 410 Gone) so old clients that still POST
    here get a clear error rather than a confusing 404.
    """
    raise HTTPException(
        410,
        "BYOK has been removed. All users now use SourcingNav's shared infrastructure with automatic provider failover. No API key needed.",
    )


@app.post("/api/intake")
async def intake(req: IntakeRequest, user: dict = Depends(get_current_user)):
    """Paste JD → parsed analysis + Boolean strings. Saves as a requisition.

    Cap policy: check upfront so we reject over-cap requests cleanly,
    but only INCREMENT after the AI calls succeed. Failed AI calls don't
    burn quota.
    """
    # Step 0: rate-limit check (does NOT increment)
    try:
        await check_cap(user["id"], "intake")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[cap] {type(e).__name__}: {str(e)[:200]}")

    # Step 1: parse JD with AI
    try:
        parsed_text = await call_ai(user["id"], JD_PARSER_PROMPT.format(jd=req.jd_text))
    except HTTPException:
        raise
    except Exception as e:
        # ReadTimeout, ConnectError, etc. — the AI provider didn't respond in time.
        # Surface a user-friendly message instead of the raw exception class name.
        etype = type(e).__name__
        prompt_len = len(JD_PARSER_PROMPT.format(jd=req.jd_text))
        print(f"[ai-parse FAIL] type={etype} prompt_len={prompt_len} jd_len={len(req.jd_text)} err={str(e)[:200]}")
        if "Timeout" in etype or "ConnectError" in etype:
            raise HTTPException(
                503,
                "The AI provider is slow or unreachable right now. Please try again in a moment.",
            )
        raise HTTPException(500, f"[ai-parse] {etype}: {str(e)[:300]}")

    # Step 2: JSON-parse the AI response
    try:
        parsed = parse_json_strict(parsed_text)
    except Exception as e:
        raise HTTPException(
            500,
            f"[json-parse] {type(e).__name__}: {str(e)[:200]}. AI returned: {parsed_text[:300]}",
        )

    # Step 3: generate Boolean strings with AI
    try:
        boolean_text = await call_ai(
            user["id"],
            BOOLEAN_BUILDER_PROMPT.format(parsed_jd=json.dumps(parsed, indent=2)),
        )
        booleans = parse_json_strict(boolean_text)
    except HTTPException:
        raise
    except Exception as e:
        etype = type(e).__name__
        print(f"[ai-bool FAIL] type={etype} parsed_keys={list(parsed.keys())[:5]} err={str(e)[:200]}")
        if "Timeout" in etype or "ConnectError" in etype:
            raise HTTPException(
                503,
                "The AI provider is slow or unreachable right now. Please try again in a moment.",
            )
        raise HTTPException(500, f"[ai-bool] {etype}: {str(e)[:300]}")

    # Steps 3.5 / 3.6 / 3.7: enrichment LLM calls run in PARALLEL.
    #
    # All three calls are independent — they read from `parsed` (already
    # populated by step 1) and don't depend on each other's output. Running
    # them serially was costing ~25-40s of wall time on top of the parser
    # and boolean calls; together that pushed total intake time past
    # browser/proxy patience and triggered intermittent timeouts.
    #
    # Concurrency safety:
    #   - call_ai() creates a fresh httpx.AsyncClient per call (no shared
    #     state, no connection pool contention).
    #   - Each call independently reads the user's BYOK key from the DB
    #     (3x redundant reads, ~600ms total — acceptable, fix later by
    #     caching once at intake start).
    #   - asyncio.gather(..., return_exceptions=True) returns exception
    #     objects in place of failed task results, so one failure cannot
    #     poison the others. Each task's existing try/except is preserved
    #     to keep the per-task diagnostic logging.
    #
    # Together.ai concurrent rate limits: at the volume we're at (single
    # user, ~5 intakes/day), 3 concurrent requests is well within any
    # reasonable rate limit. If we ever hit a 429 here we'll see it in
    # the per-task error logs and can add a small jitter or fall back
    # to serial.

    async def _run_skill_alternatives():
        """Step 3.5 body — returns dict of {skill: [alternatives]}."""
        try:
            must_have = parsed.get("must_have_skills") or []
            skills_for_alts = [s.get("skill", "") for s in must_have if s.get("skill")][:8]
            if not skills_for_alts:
                return {}
            ctx = json.dumps({
                "role_title": parsed.get("core", {}).get("role_title"),
                "level": parsed.get("core", {}).get("level"),
                "industry": parsed.get("core", {}).get("industry"),
                "company": parsed.get("core", {}).get("company"),
            })
            text = await call_ai(
                user["id"],
                SKILL_ALTERNATIVES_PROMPT.format(
                    parsed_context=ctx,
                    skills_list="\n".join(f"- {s}" for s in skills_for_alts),
                ),
                max_tokens=2000,
            )
            return parse_json_strict(text).get("skill_alternatives") or {}
        except Exception as e:
            print(f"[ai-skill-alts FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return {}

    async def _run_objection_playbook():
        """Step 3.6 body — returns list of objection entries."""
        try:
            ctx = json.dumps({
                "role_title": parsed.get("core", {}).get("role_title"),
                "level": parsed.get("core", {}).get("level"),
                "industry": parsed.get("core", {}).get("industry"),
                "company": parsed.get("core", {}).get("company"),
                "location": parsed.get("core", {}).get("location"),
                "remote_policy": parsed.get("core", {}).get("remote_policy"),
                "comp_snapshot": parsed.get("comp_snapshot"),
                "executive_brief": parsed.get("executive_brief", {}).get("summary"),
            })
            text = await call_ai(
                user["id"],
                OBJECTION_PLAYBOOK_PROMPT.format(parsed_context=ctx),
                max_tokens=2500,
            )
            return parse_json_strict(text).get("objection_playbook") or []
        except Exception as e:
            print(f"[ai-objections FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return []

    async def _run_sequenced_play():
        """Step 3.7 body — returns list of phase entries.

        Reads tier 1 companies and watering_holes from `parsed` so the
        phases reference specific venues, not generic advice."""
        try:
            market360 = parsed.get("market360") or {}
            poaching = market360.get("poaching_targets") or []
            tier1 = [p.get("company") for p in poaching if p.get("tier") == 1 and p.get("company")]
            if not tier1:
                tier1 = (market360.get("top_hiring_companies") or [])[:5]
            tier1_str = ", ".join(tier1[:8]) if tier1 else "(not specified — use general Tier 1 targets for this industry)"

            holes = parsed.get("watering_holes") or []
            holes_str = "\n".join(
                f"- {h.get('venue', '')}: {h.get('signal', '')}"
                for h in holes[:6] if h.get('venue')
            )
            if not holes_str:
                holes_str = "(not specified)"

            ctx = json.dumps({
                "role_title": parsed.get("core", {}).get("role_title"),
                "level": parsed.get("core", {}).get("level"),
                "industry": parsed.get("core", {}).get("industry"),
                "company": parsed.get("core", {}).get("company"),
                "location": parsed.get("core", {}).get("location"),
                "remote_policy": parsed.get("core", {}).get("remote_policy"),
                "difficulty": (parsed.get("market_dynamics") or {}).get("difficulty_score"),
                "executive_brief": parsed.get("executive_brief", {}).get("summary"),
            })
            text = await call_ai(
                user["id"],
                SEQUENCED_PLAY_PROMPT.format(
                    parsed_context=ctx,
                    tier1_companies=tier1_str,
                    watering_holes=holes_str,
                ),
                max_tokens=3000,
            )
            return parse_json_strict(text).get("sequenced_play") or []
        except Exception as e:
            print(f"[ai-seq-play FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return []


    async def _run_pro_skill_briefing():
        """Step 3.8 body — Pro tier ONLY. Returns list of per-skill briefings.

        Gated on user["plan"] == "pro". Free users get an empty list (the
        UI shows a locked placeholder card with the structure but not the
        content — see renderProSkillBriefingCard).

        We pass:
          - parsed_context: the same context dict the other enrichment calls use
          - must_have_list: the must-have skills (skill name + severity) so
            the model knows exactly what to classify
          - jd_excerpt: first 2000 chars of the raw JD so the model can
            ground its rationale in real quotes (truthfulness guardrail).
        """
        if user.get("plan") != "pro":
            return []
        try:
            must_have = parsed.get("must_have_skills") or []
            if not must_have:
                return []
            must_have_text = "\n".join(
                f"- {s.get('skill', '')} (currently classified as {s.get('severity', 'unknown')})"
                for s in must_have if s.get("skill")
            )
            ctx = json.dumps({
                "role_title": parsed.get("core", {}).get("role_title"),
                "level": parsed.get("core", {}).get("level"),
                "industry": parsed.get("core", {}).get("industry"),
                "company": parsed.get("core", {}).get("company"),
                "executive_brief": parsed.get("executive_brief", {}).get("summary"),
            })
            text = await call_ai(
                user["id"],
                PRO_INTAKE_PROMPT.format(
                    parsed_context=ctx,
                    must_have_list=must_have_text,
                    jd_excerpt=req.jd_text[:2000],
                ),
                max_tokens=3500,
            )
            return parse_json_strict(text).get("pro_skill_briefing") or []
        except Exception as e:
            print(f"[ai-pro-skill-briefing FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return []


    async def _run_pro_boolean_extensions():
        """Step 3.9 body — Pro tier ONLY. Returns dict of pro boolean extensions.

        Gated on user["plan"] == "pro". Free users get an empty dict; the UI
        shows a locked placeholder card with structure visible but content
        blocked out (see renderProBooleanExtensionsCard).

        Inputs:
          - parsed_context: same context dict the other enrichment calls use
          - existing_booleans: the free-tier output from step 3 (so the model
            can ANNOTATE the existing 3 LR tiers, not regenerate them)
          - watering_holes_list: stringified watering_holes from parsed (raw
            material for the Pro X-ray conversion — role-aware by construction)

        IMPORTANT: this depends on `booleans` (step 3 output) being available,
        so it runs in the parallel block alongside the other enrichments.
        Step 3 finishes BEFORE the parallel block starts (see flow).
        """
        if user.get("plan") != "pro":
            return {}
        try:
            holes = parsed.get("watering_holes") or []
            if not holes:
                holes_str = "(none — produce 3-5 generic Pro X-rays based on the role archetype)"
            else:
                holes_str = "\n".join(
                    f"- {h.get('venue', '')} ({h.get('venue_type', 'unknown')}): {h.get('signal', '')}"
                    for h in holes if h.get('venue')
                )
            ctx = json.dumps({
                "role_title": parsed.get("core", {}).get("role_title"),
                "level": parsed.get("core", {}).get("level"),
                "industry": parsed.get("core", {}).get("industry"),
                "company": parsed.get("core", {}).get("company"),
                "location": parsed.get("core", {}).get("location"),
            })
            text = await call_ai(
                user["id"],
                PRO_BOOLEAN_PROMPT.format(
                    parsed_context=ctx,
                    existing_booleans=json.dumps(booleans, indent=2)[:3000],
                    watering_holes_list=holes_str,
                ),
                max_tokens=3500,
            )
            return parse_json_strict(text) or {}
        except Exception as e:
            print(f"[ai-pro-boolean FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return {}

    # Fire all enrichment tasks concurrently. return_exceptions=True ensures we get a
    # value back for each task even if one explodes — but each task already
    # catches its own exceptions and returns a safe default ({} or []), so
    # the gather should never actually surface an exception. Belt + suspenders.
    enrich_t0 = datetime.now(timezone.utc)
    skill_alternatives, objection_playbook, sequenced_play, pro_skill_briefing, pro_boolean_extensions = await asyncio.gather(
        _run_skill_alternatives(),
        _run_objection_playbook(),
        _run_sequenced_play(),
        _run_pro_skill_briefing(),
        _run_pro_boolean_extensions(),
        return_exceptions=False,  # tasks handle their own exceptions
    )
    print(f"[intake] enrichment parallel block took {(datetime.now(timezone.utc) - enrich_t0).total_seconds():.1f}s "
          f"(skill_alts={'ok' if skill_alternatives else 'empty'}, "
          f"objections={len(objection_playbook) if isinstance(objection_playbook, list) else 'err'}, "
          f"seq_play={len(sequenced_play) if isinstance(sequenced_play, list) else 'err'}, "
          f"pro_briefing={len(pro_skill_briefing) if isinstance(pro_skill_briefing, list) else 'skip/err'}, "
          f"pro_boolean={'ok' if pro_boolean_extensions else 'skip/empty'} plan={user.get('plan')})")

    # Step 4: save to DB + compliance records
    try:
        # Company override rule: JD body is source of truth. If the AI
        # extracted a company from the JD, use it — even if the user typed
        # something different. Users mistype. JDs don't lie about who is
        # hiring. The override is surfaced back to the client in the
        # response payload as `company_override` so the UI can banner it.
        user_entered = (req.org_name or "").strip() or None
        parsed_company = (parsed.get("core", {}).get("company") or "").strip() or None
        company_override = None
        if parsed_company and user_entered and parsed_company.lower() != user_entered.lower():
            # Mismatch — the AI found a company in the JD that differs
            # from what the user typed. Use the parsed one.
            org_name = parsed_company
            company_override = {
                "user_entered": user_entered,
                "detected_from_jd": parsed_company,
                "reason": "JD body is the source of truth for the hiring company. "
                          "We've used the name detected in the JD instead of what was typed.",
            }
        else:
            # Priority when no conflict: parsed JD > user input > fallback
            org_name = parsed_company or user_entered or "Unspecified"

        req_title = req.req_title or parsed.get("core", {}).get("role_title") or "Untitled Role"

        async with db() as client:
            rs = await client.execute(
                "SELECT id FROM organizations WHERE user_id = ? AND name = ?",
                [user["id"], org_name],
            )
            if rs.rows:
                org_id = rs.rows[0][0]
            else:
                org_id = str(uuid.uuid4())
                await client.execute(
                    "INSERT INTO organizations (id, user_id, name, org_type) VALUES (?, ?, ?, ?)",
                    [org_id, user["id"], org_name, "client" if user["mode"] == "agency" else "own"],
                )

            req_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            # Merge skill_alternatives into parsed so it persists alongside
            # the rest of the parsed JD data. UI reads it from parsed.skill_alternatives.
            if skill_alternatives:
                parsed["skill_alternatives"] = skill_alternatives
            if objection_playbook:
                parsed["objection_playbook"] = objection_playbook
            if sequenced_play:
                parsed["sequenced_play"] = sequenced_play
            if pro_skill_briefing:
                parsed["pro_skill_briefing"] = pro_skill_briefing
            if pro_boolean_extensions:
                parsed["pro_boolean_extensions"] = pro_boolean_extensions
            await client.execute(
                """INSERT INTO requisitions
                   (id, org_id, user_id, title, jd_raw, parsed_json, boolean_strings_json, status, opened_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                [req_id, org_id, user["id"], req_title, req.jd_text,
                 json.dumps(parsed), json.dumps(booleans), now, now],
            )

            await client.execute(
                "INSERT INTO activity_log (id, user_id, entity_type, entity_id, action, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                [str(uuid.uuid4()), user["id"], "req", req_id, "created", json.dumps({"source": "intake"})],
            )

            # ----- Compliance layer (non-blocking) -----
            # Note: an intake is NOT about a natural person's data, so we don't
            # register a data_subject here. We DO log the automated decision
            # (AI-parsed JD) and the structured req_skills for calibration.
            try:
                # Register the JD parser prompt+model as a model_version
                rs = await client.execute(
                    "SELECT byok_provider FROM users WHERE id = ?", [user["id"]]
                )
                provider = rs.rows[0][0] if rs.rows and rs.rows[0][0] else "unknown"
                mv_id = await register_model_version(
                    client,
                    prompt_name="jd_parser",
                    prompt_text=JD_PARSER_PROMPT,
                    model_provider=provider,
                    model_name=provider,
                )

                # Audit event — the JD was parsed by AI, this is the record
                ae_id = await write_audit_event(
                    client,
                    event_type="ai_decision",
                    action="parse_jd",
                    actor_user_id=user["id"],
                    entity_type="requisition",
                    entity_id=req_id,
                    inputs={"jd_length": len(req.jd_text), "org": org_name},
                    outputs={
                        "role_title": parsed.get("core", {}).get("role_title"),
                        "must_have_count": len(parsed.get("must_have_skills") or []),
                    },
                    model_version_id=mv_id,
                )

                # Decision explanation — the "why this was parsed this way" record
                must_have = parsed.get("must_have_skills") or []
                top_factors = [
                    {"factor": s.get("skill", ""), "severity": s.get("severity", "preferred"),
                     "rationale": (s.get("rationale") or "")[:200]}
                    for s in must_have[:5]
                ]
                plain_english = parsed.get("executive_brief", {}).get("summary") or ""
                await write_decision_explanation(
                    client,
                    audit_event_id=ae_id,
                    subject_id=None,  # no natural person subject for a JD parse
                    decision_type="jd_parse",
                    decision_outcome="parsed",
                    top_factors=top_factors,
                    plain_english=plain_english[:500],
                )

                # Structured req_skills — THIS populates the brain's demand signal
                await write_req_skills(client, req_id, parsed)

                # Company override audit event — fires only when the AI extracted
                # a different company from the JD than what the user typed in.
                # Per RISK_ASSESSMENT.md §2.3, automated overrides of user input
                # must be recorded. This lets an auditor trace why the DB org
                # differs from what appeared in the intake form.
                if company_override:
                    await write_audit_event(
                        client,
                        event_type="system_override",
                        action="override_user_company",
                        actor_user_id=user["id"],
                        entity_type="requisition",
                        entity_id=req_id,
                        inputs={
                            "user_entered_company": company_override["user_entered"],
                            "jd_length": len(req.jd_text),
                        },
                        outputs={
                            "final_company": company_override["detected_from_jd"],
                            "reason": "JD body named a different hiring company",
                        },
                        model_version_id=mv_id,  # same prompt that extracted the name
                    )

                # Second AI decision in the intake pipeline: Boolean string generation.
                # Separate model_version + audit_event so the chain records WHICH
                # version of BOOLEAN_BUILDER_PROMPT produced these strings. EU AI
                # Act Article 11 traceability: every AI decision linked to its
                # exact prompt version, even when multiple prompts run in one request.
                bool_mv_id = await register_model_version(
                    client,
                    prompt_name="boolean_builder",
                    prompt_text=BOOLEAN_BUILDER_PROMPT,
                    model_provider=provider,
                    model_name=provider,
                )
                company_clusters = booleans.get("company_clusters") or {}
                bool_ae_id = await write_audit_event(
                    client,
                    event_type="ai_decision",
                    action="generate_booleans",
                    actor_user_id=user["id"],
                    entity_type="requisition",
                    entity_id=req_id,
                    inputs={
                        "parsed_role_title": parsed.get("core", {}).get("role_title"),
                        "must_have_count": len(parsed.get("must_have_skills") or []),
                    },
                    outputs={
                        "xray_keys": sorted(list((booleans.get("xray") or {}).keys())),
                        "tier1_count": len(company_clusters.get("tier_1_direct_competitors") or []),
                        "tier2_count": len(company_clusters.get("tier_2_adjacent") or []),
                    },
                    model_version_id=bool_mv_id,
                )
                # Plain-English explanation of what the Boolean builder decided.
                # Keeps the audit trail human-readable for dispute investigation.
                bool_top_factors = [
                    {"factor": "tier_1_competitors", "value": company_clusters.get("tier_1_direct_competitors") or []},
                    {"factor": "xray_sources_used", "value": sorted(list((booleans.get("xray") or {}).keys()))},
                ]
                await write_decision_explanation(
                    client,
                    audit_event_id=bool_ae_id,
                    subject_id=None,
                    decision_type="boolean_generation",
                    decision_outcome="generated",
                    top_factors=bool_top_factors,
                    plain_english=(
                        f"Generated Boolean strings for {len((booleans.get('xray') or {}))} X-ray sources "
                        f"and {len(company_clusters.get('tier_1_direct_competitors') or [])} tier-1 competitors. "
                        f"Hiring company excluded from all company clusters per non-solicit rule."
                    )[:500],
                )
            except Exception as compliance_err:
                print(f"[compliance-intake] non-fatal write error: {compliance_err!r}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[db-save] {type(e).__name__}: {str(e)[:300]}")

    # Step 5: NOW increment the usage counter (everything succeeded)
    # Wrapped so a failure here doesn't break the user-facing return.
    try:
        await increment_cap(user["id"], "intake")
    except Exception:
        pass  # silently swallow — the work is done, accounting is best-effort

    # Step 6: fire-and-forget retention email.
    # asyncio.create_task() schedules the send AFTER we return, so the user
    # gets their intake response immediately and the email goes out in
    # background. The helper itself swallows all errors — a Resend hiccup
    # must NEVER break a successful intake response.
    #
    # We deliberately email even on first-intake users (no opt-in) because:
    #   1. They actively pasted a JD and ran an intake — clear engagement signal
    #   2. The email is content-rich (their booleans), not promotional
    #   3. Footer has a manage-emails link for opt-out (TODO: build the unsub flow)
    try:
        subject, html = _build_intake_completion_email(parsed, booleans, req_id)
        asyncio.create_task(_send_email(user["email"], subject, html))
    except Exception as e:
        print(f"[intake-email schedule FAIL] type={type(e).__name__} err={str(e)[:200]}")

    return {
        "req_id": req_id,
        "parsed": parsed,
        "boolean_strings": booleans,
        "created_at": now,
        "skill_alternatives": skill_alternatives,
        "objection_playbook": objection_playbook,
        "sequenced_play": sequenced_play,
        # When non-null, the UI should show a banner telling the user
        # their typed company was overridden by what the JD actually says.
        "company_override": company_override,
    }


@app.post("/api/source/evaluate")
async def evaluate_candidate(req: CandidateEvalRequest, user: dict = Depends(get_current_user)):
    """Score a candidate against a requisition.

    Pattern matches /api/intake: cap check upfront, AI call, JSON parse, DB save,
    then increment usage at the end. Failed AI calls don't burn quota.
    """
    # Step 0: cap check (does NOT increment) — uses 'eval' bucket (10/mo on free)
    try:
        await check_cap(user["id"], "eval")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[cap] {type(e).__name__}: {str(e)[:200]}")

    # Step 1: load the requisition (verify ownership AND get parsed JD)
    try:
        async with db() as client:
            rs = await client.execute(
                "SELECT id, title, parsed_json FROM requisitions WHERE id = ? AND user_id = ?",
                [req.req_id, user["id"]],
            )
            if not rs.rows:
                raise HTTPException(404, "Requisition not found")
            req_row = rs.rows[0]
            if not req_row[2]:
                raise HTTPException(400, "Requisition has no parsed data — re-run the intake first")
            parsed_jd = req_row[2]  # JSON string, pass directly to prompt
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[db-req-load] {type(e).__name__}: {str(e)[:200]}")

    # Step 2: AI evaluation
    try:
        eval_text = await call_ai(
            user["id"],
            CANDIDATE_EVAL_PROMPT.format(parsed_jd=parsed_jd, candidate_text=req.candidate_text),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[ai-eval] {type(e).__name__}: {str(e)[:300]}")

    # Step 3: parse the AI response as JSON
    try:
        # AI may wrap in code fences or add preamble; strip defensively
        cleaned = eval_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        evaluation = json.loads(cleaned)
    except Exception as e:
        raise HTTPException(500, f"[ai-parse] {type(e).__name__}: {str(e)[:300]} | raw start: {eval_text[:200]}")

    # Step 4: save the candidate (idempotent on email if provided)
    try:
        candidate_id = str(uuid.uuid4())
        candidate_name = req.candidate_name or "Unnamed candidate"
        async with db() as client:
            # If email provided, check for an existing candidate to avoid duplicates
            if req.candidate_email:
                rs = await client.execute(
                    "SELECT id FROM candidates WHERE user_id = ? AND email = ?",
                    [user["id"], req.candidate_email],
                )
                if rs.rows:
                    candidate_id = rs.rows[0][0]
                else:
                    await _insert_candidate(client, candidate_id, user["id"], req)
            else:
                await _insert_candidate(client, candidate_id, user["id"], req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[db-cand-save] {type(e).__name__}: {str(e)[:300]}")

    # Step 5: save the submission + compliance records (one transaction)
    try:
        submission_id = str(uuid.uuid4())
        async with db() as client:
            # 5a: submissions row (same as before)
            await client.execute(
                """INSERT INTO submissions
                   (id, req_id, candidate_id, ai_fit_score, recommendation, fit_analysis_json, stage)
                   VALUES (?, ?, ?, ?, ?, ?, 'evaluated')""",
                [
                    submission_id,
                    req.req_id,
                    candidate_id,
                    int(evaluation.get("fit_score", 0)),
                    evaluation.get("recommendation", "PASS"),
                    json.dumps(evaluation),
                ],
            )

            # 5b: compliance layer (best-effort — a failure here does NOT roll back
            #     the submission. Compliance is additive, not blocking the UX.)
            try:
                # Register the candidate as a GDPR data subject (idempotent on candidate_id)
                subject_id = await register_data_subject(
                    client, "candidate", candidate_id,
                )

                # Register (or reuse) the model_versions row for this eval
                async with db() as ai_client:  # new connection — reads only
                    rs = await ai_client.execute(
                        "SELECT byok_provider FROM users WHERE id = ?", [user["id"]]
                    )
                    provider = rs.rows[0][0] if rs.rows and rs.rows[0][0] else "unknown"

                mv_id = await register_model_version(
                    client,
                    prompt_name="candidate_eval",
                    prompt_text=CANDIDATE_EVAL_PROMPT,
                    model_provider=provider,
                    model_name=provider,  # exact model name known to _call_* funcs
                )

                # Audit event (tamper-evident HMAC chain)
                ae_id = await write_audit_event(
                    client,
                    event_type="ai_decision",
                    action="evaluate_candidate",
                    actor_user_id=user["id"],
                    subject_id=subject_id,
                    entity_type="submission",
                    entity_id=submission_id,
                    inputs={"req_id": req.req_id, "candidate_id": candidate_id},
                    outputs={
                        "fit_score": evaluation.get("fit_score"),
                        "recommendation": evaluation.get("recommendation"),
                    },
                    model_version_id=mv_id,
                    confidence_score=float(evaluation.get("fit_score", 0)) / 100.0,
                )

                # Decision explanation (plain-English, EU AI Act Art 13 + NYC LL144)
                top_factors = []
                for b in (evaluation.get("blocker_assessment") or [])[:5]:
                    top_factors.append({
                        "factor": b.get("skill", ""),
                        "type": "blocker",
                        "status": b.get("status", "unclear"),
                        "evidence": b.get("evidence", "")[:200],
                    })
                await write_decision_explanation(
                    client,
                    audit_event_id=ae_id,
                    subject_id=subject_id,
                    decision_type="candidate_fit_score",
                    decision_outcome=evaluation.get("recommendation", "PASS"),
                    top_factors=top_factors,
                    plain_english=evaluation.get("headline", "")[:500],
                )

                # 8-dimension scores (partial today — fit_score only; expand next session)
                await write_submission_dimensions(client, submission_id, evaluation)

                # Structured candidate skills (new — populates the taxonomy)
                # Reads evaluation['extracted_skills'] if present, else falls back
                # to blocker_assessment + preferred_assessment with status='met'/'partial'
                await write_candidate_skills(client, candidate_id, evaluation)
            except Exception as compliance_err:
                # Log but don't fail the request. The submission is already saved.
                # TODO: wire this into proper error monitoring.
                print(f"[compliance] non-fatal write error: {compliance_err!r}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[db-sub-save] {type(e).__name__}: {str(e)[:300]}")

    # Step 6: increment usage (best effort, don't break the response)
    try:
        await increment_cap(user["id"], "eval")
    except Exception:
        pass

    return {
        "submission_id": submission_id,
        "candidate_id": candidate_id,
        "req_id": req.req_id,
        "evaluation": evaluation,
    }


async def _insert_candidate(client, candidate_id: str, user_id: str, req: CandidateEvalRequest):
    """Helper to insert a candidate row. Used by /api/source/evaluate."""
    await client.execute(
        """INSERT INTO candidates
           (id, user_id, name, email, linkedin_url, github_url, current_title, current_company, resume_text, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            candidate_id,
            user_id,
            req.candidate_name or "Unnamed candidate",
            req.candidate_email,
            req.linkedin_url,
            req.github_url,
            req.current_title,
            req.current_company,
            req.candidate_text,
            req.source,
        ],
    )


@app.get("/api/reqs")
async def list_reqs(
    status: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    """List the user's requisitions ranked by recency.

    Optional ?status=open|closed|placed filters by status. The pipeline
    list view in /app/pipeline.html calls this on every page load.
    Was silently broken (404'd) until this endpoint was added.

    Returns:
        {"reqs": [
            {"id", "title", "status", "fee_estimate",
             "opened_at", "org_name", "submission_count"},
            ...
        ]}
    """
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be 1..500")
    if status and status not in ("open", "closed", "placed", "on_hold"):
        raise HTTPException(400, "status must be one of: open, closed, placed, on_hold")

    async with db() as client:
        # Single query with subselect for submission_count so the UI
        # can show "5 submissions" per req without N+1 calls.
        if status:
            rs = await client.execute(
                """SELECT r.id, r.title, r.status, r.fee_estimate, r.opened_at,
                          o.name,
                          (SELECT COUNT(*) FROM submissions WHERE req_id = r.id) as sub_count
                   FROM requisitions r
                   JOIN organizations o ON r.org_id = o.id
                   WHERE r.user_id = ? AND r.status = ?
                   ORDER BY r.opened_at DESC
                   LIMIT ?""",
                [user["id"], status, limit],
            )
        else:
            rs = await client.execute(
                """SELECT r.id, r.title, r.status, r.fee_estimate, r.opened_at,
                          o.name,
                          (SELECT COUNT(*) FROM submissions WHERE req_id = r.id) as sub_count
                   FROM requisitions r
                   JOIN organizations o ON r.org_id = o.id
                   WHERE r.user_id = ?
                   ORDER BY r.opened_at DESC
                   LIMIT ?""",
                [user["id"], limit],
            )
    return {
        "reqs": [
            {
                "id": r[0], "title": r[1], "status": r[2],
                "fee_estimate": r[3], "opened_at": r[4],
                "org_name": r[5], "submission_count": int(r[6] or 0),
            }
            for r in (rs.rows or [])
        ]
    }


@app.get("/api/reqs/{req_id}/submissions")
async def list_submissions(req_id: str, user: dict = Depends(get_current_user)):
    """List all candidate submissions for a given requisition."""
    async with db() as client:
        # Verify the req belongs to the user first
        rs = await client.execute(
            "SELECT id FROM requisitions WHERE id = ? AND user_id = ?",
            [req_id, user["id"]],
        )
        if not rs.rows:
            raise HTTPException(404, "Requisition not found")
        rs = await client.execute(
            """SELECT s.id, s.candidate_id, s.ai_fit_score, s.recommendation, s.stage,
                      s.fit_analysis_json, s.created_at,
                      c.name, c.current_title, c.current_company
               FROM submissions s JOIN candidates c ON s.candidate_id = c.id
               WHERE s.req_id = ?
               ORDER BY s.ai_fit_score DESC, s.created_at DESC""",
            [req_id],
        )
        return {
            "submissions": [
                {
                    "id": r[0], "candidate_id": r[1], "fit_score": r[2],
                    "recommendation": r[3], "stage": r[4],
                    "evaluation": json.loads(r[5]) if r[5] else None,
                    "created_at": r[6],
                    "candidate_name": r[7], "current_title": r[8], "current_company": r[9],
                }
                for r in rs.rows
            ]
        }


@app.get("/api/reqs/{req_id}")
async def get_req(req_id: str, user: dict = Depends(get_current_user)):
    async with db() as client:
        rs = await client.execute(
            """SELECT r.id, r.title, r.jd_raw, r.parsed_json, r.boolean_strings_json,
                      r.status, r.opened_at, o.name
               FROM requisitions r JOIN organizations o ON r.org_id = o.id
               WHERE r.id = ? AND r.user_id = ?""",
            [req_id, user["id"]],
        )
        if not rs.rows:
            raise HTTPException(404, "Requisition not found")
        r = rs.rows[0]
        return {
            "id": r[0], "title": r[1], "jd_raw": r[2],
            "parsed": json.loads(r[3]) if r[3] else None,
            "boolean_strings": json.loads(r[4]) if r[4] else None,
            "status": r[5], "opened_at": r[6], "org_name": r[7],
        }


# ============================================================
# PHASE B1 — Pipeline stage transitions + calibration
# ============================================================
# POST /api/submissions/{id}/stage
#   Recruiter updates a submission's stage. Side effects:
#     1. submissions.stage (+ placed_at / rejected_at if applicable)
#     2. calibration_events row (processed=0)
#     3. audit_events row (extends HMAC chain)
#     4. Auto-trigger run_calibration if there are unprocessed events
#        (this means a single click will process THIS event AND any
#         prior unprocessed events, keeping the math fresh without
#         requiring a separate admin batch run)
#
# POST /api/calibration/run
#   Admin-triggered batch replay. Useful for backfilling events
#   that were recorded but not calibrated (e.g. if auto-trigger
#   was ever disabled), and for dev/debugging.
# ============================================================

ALLOWED_STAGES = {
    "submitted", "phone_screen", "onsite", "offer",
    "placed", "rejected", "withdrew",
}


@app.post("/api/submissions/{submission_id}/stage")
async def update_submission_stage(
    submission_id: str,
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """Update the stage of a candidate submission and record the
    calibration signal. The recruiter's one-click action is the
    whole ground truth for Phase B1 learning.
    """
    new_stage = (payload or {}).get("stage")
    reason = (payload or {}).get("reason")  # optional free-text

    if not new_stage or new_stage not in ALLOWED_STAGES:
        raise HTTPException(
            400,
            f"stage must be one of: {sorted(ALLOWED_STAGES)}",
        )

    async with db() as client:
        # 1. Load the submission + ownership check via req.user_id
        rs = await client.execute(
            """SELECT s.id, s.stage, s.req_id, s.candidate_id, r.user_id
               FROM submissions s
               JOIN requisitions r ON s.req_id = r.id
               WHERE s.id = ?""",
            [submission_id],
        )
        if not rs.rows:
            raise HTTPException(404, "Submission not found")
        _, current_stage, req_id, candidate_id, owner_id = rs.rows[0]
        if owner_id != user["id"]:
            raise HTTPException(403, "Not your submission")

        from_stage = current_stage or "submitted"

        # 2. Update the submission — set placed_at/rejected_at appropriately
        if new_stage == "placed":
            await client.execute(
                "UPDATE submissions SET stage = ?, placed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [new_stage, submission_id],
            )
        elif new_stage == "rejected":
            await client.execute(
                "UPDATE submissions SET stage = ?, rejected_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [new_stage, submission_id],
            )
        else:
            await client.execute(
                "UPDATE submissions SET stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [new_stage, submission_id],
            )

        # 3. Compliance — every stage change is a decision that will
        #    feed the learning loop. Must be audited.
        signal = signal_for_transition(from_stage, new_stage)
        try:
            ae_id = await write_audit_event(
                client,
                event_type="recruiter_action",
                action="submission_stage_change",
                actor_user_id=user["id"],
                entity_type="submission",
                entity_id=submission_id,
                inputs={"from_stage": from_stage, "to_stage": new_stage},
                outputs={"calibration_signal": signal},
                model_version_id=None,
            )
        except Exception as audit_err:
            # Non-fatal — the state change already committed. Log and continue.
            print(f"[calibration] audit write failed: {audit_err!r}")
            ae_id = None

        # 4. Record calibration event (processed=0)
        event_id = None
        if signal != 0.0:
            try:
                event_id = await record_calibration_event(
                    client,
                    user_id=user["id"],
                    submission_id=submission_id,
                    req_id=req_id,
                    from_stage=from_stage,
                    to_stage=new_stage,
                    reason=reason,
                    audit_event_id=ae_id,
                )
            except Exception as calib_err:
                print(f"[calibration] event insert failed: {calib_err!r}")

        # 5. Auto-run calibration to keep weights fresh.
        #    Processes THIS event + any prior unprocessed ones atomically.
        run_summary = None
        if event_id:
            try:
                run_summary = await run_calibration(
                    client,
                    triggered_by_user_id=user["id"],
                    notes=f"auto-trigger from stage change {submission_id[:8]}",
                )
            except Exception as run_err:
                print(f"[calibration] auto-run failed: {run_err!r}")

    return {
        "submission_id": submission_id,
        "from_stage": from_stage,
        "to_stage": new_stage,
        "calibration_signal": signal,
        "calibration_event_id": event_id,
        "calibration_run": run_summary,
    }


@app.post("/api/calibration/run")
async def trigger_calibration_run(user: dict = Depends(get_current_user)):
    """Admin/dev-triggered batch. Processes every unprocessed
    calibration_event in chronological order. Returns the run
    summary (run_id, events_processed, pairs_updated).
    Safe to call when nothing is pending — returns a no-op run.
    """
    async with db() as client:
        summary = await run_calibration(
            client,
            triggered_by_user_id=user["id"],
            notes="manual_batch_run",
        )
    return summary


# ============================================================
# PHASE B2 — Skill resolution (alias / promote / reject)
# ============================================================
# Four endpoints turn the unresolved-skill firehose into a
# manageable approval queue. The user is in control — the LLM
# only suggests. Every decision is audited as a taxonomy_change
# event for EU AI Act Article 12 record-keeping.
#
# GET  /api/taxonomy/unresolved           — ranked queue
# GET  /api/taxonomy/suggestion/{raw}     — LLM suggestion (cached)
# POST /api/taxonomy/decide               — apply alias/promote/reject
# GET  /api/taxonomy/recent-decisions     — see what's been decided
# ============================================================


@app.get("/api/taxonomy/unresolved")
async def taxonomy_unresolved(
    min_count: int = 1,
    limit: int = 50,
    user: dict = Depends(get_current_user),
):
    """List unresolved raw_skill_text strings ranked by occurrence count.

    Excludes anything already decided (alias/promote/reject) so the
    queue stays clean across reloads.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be 1..200")
    if min_count < 1:
        raise HTTPException(400, "min_count must be >= 1")
    async with db() as client:
        candidates = await list_unresolved_candidates(client, min_count=min_count, limit=limit)
    return {"candidates": candidates, "count": len(candidates)}


@app.get("/api/taxonomy/suggestion/{raw_text}")
async def taxonomy_suggestion(raw_text: str, user: dict = Depends(get_current_user)):
    """Get an LLM-generated suggestion for what to do with a raw skill.
    First call hits the LLM and caches the result. Subsequent calls
    return the cached suggestion until a decision is applied.
    """
    norm = normalize_raw_text(raw_text)
    if not norm:
        raise HTTPException(400, "raw_text is empty after normalization")
    async with db() as client:
        try:
            suggestion = await get_or_generate_suggestion(
                client,
                raw_text_normalized=norm,
                call_ai_func=call_ai,
                user_id=user["id"],
                register_model_version_func=register_model_version,
            )
        except Exception as e:
            etype = type(e).__name__
            print(f"[taxonomy_suggestion FAIL] type={etype} raw={norm[:60]!r} err={str(e)[:200]}")
            if "Timeout" in etype or "ConnectError" in etype:
                raise HTTPException(503, "The AI provider is slow or unreachable right now. Please try again in a moment.")
            raise HTTPException(500, f"[skill-suggest] {etype}: {str(e)[:200]}")
    return {"raw_text_normalized": norm, "suggestion": suggestion}


@app.post("/api/taxonomy/decide")
async def taxonomy_decide(payload: dict, user: dict = Depends(get_current_user)):
    """Apply a decision to an unresolved raw_skill_text.

    Body:
      {
        "raw_text": "python",
        "decision": "alias" | "promote" | "reject",
        // for alias:
        "target_skill_id": "sk_...",
        // for promote:
        "canonical_name": "Python",
        "category": "programming_languages",
        "aliases": ["python", "python3"],
        "adjacent_skill_ids": ["sk_xxx", "sk_yyy"],
        "weight": "high" | "medium" | "low",
        // optional:
        "notes": "free-text reasoning"
      }

    Returns the result dict from the underlying apply_* function.
    """
    raw_text = (payload or {}).get("raw_text")
    decision = (payload or {}).get("decision", "").lower()
    notes = (payload or {}).get("notes")

    if not raw_text:
        raise HTTPException(400, "raw_text is required")
    if decision not in ("alias", "promote", "reject"):
        raise HTTPException(400, "decision must be one of: alias, promote, reject")

    norm = normalize_raw_text(raw_text)
    if not norm:
        raise HTTPException(400, "raw_text is empty after normalization")

    async with db() as client:
        # Defensive — refuse to re-decide something already in the table.
        # The caller can call /undecide first if they want to change it.
        existing = await client.execute(
            "SELECT decision FROM skill_resolution_decisions WHERE raw_text_normalized = ?",
            [norm],
        )
        if existing.rows:
            raise HTTPException(
                409,
                f"raw_text already has decision '{existing.rows[0][0]}'. Call /api/taxonomy/undecide first to change it.",
            )

        try:
            if decision == "alias":
                target = (payload or {}).get("target_skill_id")
                if not target:
                    raise HTTPException(400, "target_skill_id required for alias decision")
                result = await apply_alias(
                    client, raw_text_normalized=norm,
                    target_skill_id=target, user_id=user["id"],
                    notes=notes, write_audit_event_func=write_audit_event,
                )
            elif decision == "promote":
                canonical = (payload or {}).get("canonical_name")
                category = (payload or {}).get("category")
                if not canonical or not category:
                    raise HTTPException(400, "canonical_name and category required for promote")
                result = await apply_promote(
                    client, raw_text_normalized=norm,
                    canonical_name=canonical, category=category,
                    aliases=(payload or {}).get("aliases") or [],
                    adjacent_skill_ids=(payload or {}).get("adjacent_skill_ids") or [],
                    weight=(payload or {}).get("weight", "medium"),
                    user_id=user["id"], notes=notes,
                    write_audit_event_func=write_audit_event,
                )
            else:  # reject
                result = await apply_reject(
                    client, raw_text_normalized=norm,
                    user_id=user["id"], notes=notes,
                    write_audit_event_func=write_audit_event,
                )
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            etype = type(e).__name__
            print(f"[taxonomy_decide FAIL] type={etype} decision={decision} raw={norm[:60]!r} err={str(e)[:200]}")
            raise HTTPException(500, f"[taxonomy-decide] {etype}: {str(e)[:200]}")
    return result


@app.post("/api/taxonomy/undecide")
async def taxonomy_undecide(payload: dict, user: dict = Depends(get_current_user)):
    """Remove a decision so the raw_text reappears in the queue.
    Does NOT undo aliasing/promotion side effects (skills still exist,
    rows still back-populated). Just clears the decision so the user
    can re-decide if they made a mistake.
    """
    raw_text = (payload or {}).get("raw_text")
    if not raw_text:
        raise HTTPException(400, "raw_text is required")
    norm = normalize_raw_text(raw_text)
    async with db() as client:
        rs = await client.execute(
            "SELECT id FROM skill_resolution_decisions WHERE raw_text_normalized = ?",
            [norm],
        )
        if not rs.rows:
            raise HTTPException(404, "No decision exists for that raw_text")
        await client.execute(
            "DELETE FROM skill_resolution_decisions WHERE raw_text_normalized = ?",
            [norm],
        )
        # Audit
        try:
            await write_audit_event(
                client,
                event_type="taxonomy_change",
                action="undecide",
                actor_user_id=user["id"],
                entity_type="raw_skill_text",
                entity_id=norm[:64],
                inputs={"raw_text": norm},
                outputs={"reason": "user reverted decision"},
                model_version_id=None,
            )
        except Exception:
            pass
    return {"ok": True, "raw_text_normalized": norm}


@app.get("/api/taxonomy/recent-decisions")
async def taxonomy_recent_decisions(limit: int = 20, user: dict = Depends(get_current_user)):
    """Show the most recent decisions for visibility."""
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit 1..100")
    async with db() as client:
        rs = await client.execute(
            """SELECT srd.raw_text_normalized, srd.decision, srd.decided_at,
                      srd.notes, s.canonical_name, s.category
               FROM skill_resolution_decisions srd
               LEFT JOIN skills s ON srd.resolved_skill_id = s.id
               ORDER BY srd.decided_at DESC
               LIMIT ?""",
            [limit],
        )
    return {
        "decisions": [
            {
                "raw_text": r[0],
                "decision": r[1],
                "decided_at": r[2],
                "notes": r[3],
                "resolved_canonical": r[4],
                "resolved_category": r[5],
            }
            for r in (rs.rows or [])
        ]
    }
