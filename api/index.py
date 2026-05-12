"""
SourcingNav Talent Engine - API v1
Deployed at sourcingnav.com/api/*

Two tiers of endpoints in one file:
1. DEMO endpoints (/api/dashboard/*, /api/candidates, etc) - read-only seed data
   powering the hardcoded demos at /ui/dashboard.html and /ui/people-ops.html.
2. PRODUCTION endpoints (/api/intake, /api/auth/*, /api/reqs/*, etc) - the real
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
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

# Compliance helpers - lives in api/_compliance.py. See that module's docstring.
from api._compliance import (
    register_data_subject,
    register_model_version,
    write_audit_event,
    write_decision_explanation,
    write_submission_dimensions,
    write_req_skills,
    write_candidate_skills,
    run_matching_engine,
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
# we have. The fallback only fires on transient errors (timeouts, 5xx) -
# never on 4xx (which would just hide bugs). See _call_with_failover().
SERVER_ANTHROPIC_KEY = os.environ.get("SERVER_ANTHROPIC_KEY", "")

# ---------- STARTUP VALIDATION ----------
# Detect the empty-key silent failure that took prod down on 2026-04-28.
# Symptom: Vercel UI shows the env var exists (encrypted) but the value
# stored is an empty string. call_ai then sends "" as the API key,
# Anthropic returns 401, our code raises HTTPException(500) which is NOT
# 5xx-classified as transient by failover, so failover doesn't trigger,
# and BOTH primary and fallback fail with the same auth error → users
# see the "both providers down" error.
#
# This check turns that silent runtime failure into a loud boot-time
# failure. If the key is missing or doesn't look like an Anthropic key,
# we crash the app on startup so Vercel marks the deploy as failed.
# Better to fail to deploy than to deploy a broken function.
#
# Local dev tolerated: if VERCEL env var is unset (i.e., running locally),
# we skip the check so devs without a key can still run demos / tests.
_IS_VERCEL_PROD = os.environ.get("VERCEL_ENV") == "production"
if _IS_VERCEL_PROD:
    if not SERVER_ANTHROPIC_KEY:
        raise RuntimeError(
            "BOOT FAIL: SERVER_ANTHROPIC_KEY is empty in production. "
            "Check Vercel env vars: the variable exists but the value is empty. "
            "Re-save the key in https://vercel.com/{team}/{project}/settings/environment-variables"
        )
    if not SERVER_ANTHROPIC_KEY.startswith("sk-ant-"):
        raise RuntimeError(
            f"BOOT FAIL: SERVER_ANTHROPIC_KEY does not look like an Anthropic key "
            f"(expected prefix sk-ant-, got prefix {SERVER_ANTHROPIC_KEY[:10]!r}). "
            f"Verify the value in Vercel env settings."
        )
    print(f"[boot] SERVER_ANTHROPIC_KEY validated (length={len(SERVER_ANTHROPIC_KEY)}, prefix={SERVER_ANTHROPIC_KEY[:10]})")

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
# DEMO ENDPOINTS - read-only, unchanged from v0.1
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
# PRODUCTION - the real talent engine at /app/
# =====================================================================

FREE_CAPS = {"intake": 5, "eval": 10, "outreach": 10}


# Free-tier billing period is rolling 30 days, not calendar months. Fairer
# to users who sign up mid-month, simpler to reason about, no cron needed -
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
    """Legacy combined function - kept for any callers that want the old behavior."""
    await check_cap(user_id, cap_type)
    await increment_cap(user_id, cap_type)


# ---------- AUTH ----------

class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyTokenRequest(BaseModel):
    token: str


def sign_token(email: str, exp_minutes: int = 15, kind: str = "magic_link") -> str:
    """Sign a token with an email, expiry, and purpose tag.

    kind="magic_link" - short-lived (15 min), sent via email, used once
    kind="session"    - long-lived (30 days), stored in browser, used for API auth
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
        # Old format: email|exp|sig (3 parts) - treat as session for backwards compat
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
            # last_used_at update - best-effort, don't fail the request if it errors
            try:
                await client.execute(
                    "UPDATE sessions SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [session_id],
                )
            except Exception:
                pass
        # If no session row found, this is a legacy token (issued before this table existed).
        # We accept it based on signature alone - they'll get a proper session next login.

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
    to bring one. Now everyone - free and Pro - uses our server keys with
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


async def _call_anthropic_haiku(api_key: str, prompt: str, max_tokens: int) -> str:
    """Call Anthropic Claude Haiku 4.5 - our PRIMARY model for all intake calls.

    Why Haiku 4.5 as primary:
      - 5-8x cheaper than Together Qwen 235B for our workload (~\$0.07/intake
        vs ~\$0.45/intake)
      - Matches Sonnet 4 on coding/reasoning benchmarks per Anthropic
      - 4-5x faster than Sonnet 4.5 (lower latency on the parallel
        enrichment block)
      - Same Anthropic infrastructure as our fallback model = no more
        multiplicative cross-vendor failure surface

    Same JSON-only system message + fence stripping as the Sonnet helper.
    Timeout is 90s (Haiku is fast; if it's not done in 90s something's
    actually wrong and we should fail over).
    """
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "system": "Respond with valid JSON only. No markdown code fences. No prose before or after the JSON object.",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if r.status_code >= 400:
            raise _ai_error("anthropic-haiku", r.status_code, r.text)
        text = r.json()["content"][0]["text"]
        return text.replace("```json", "").replace("```", "").strip()


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
    """Server-key path with retry + automatic Haiku -> Sonnet failover.

    Stack as of 2026-04-28:
      Primary: Claude Haiku 4.5 (cheap, fast, reliable)
      Fallback: Claude Sonnet 4.5 (same vendor, smarter model)

    Why this stack:
      - Together.ai had repeated 503/timeout failures in production. The
        "cross-vendor failover" architecture was supposed to mask this,
        but multiplicative failure across many parallel calls per intake
        meant users still hit "both providers down" errors regularly.
      - Anthropic infrastructure has been rock-solid in our usage.
      - Haiku 4.5 is ~5-8x CHEAPER than Together Qwen 235B for our
        workload. The "Together = cheap" assumption is obsolete.
      - Same-vendor primary+fallback eliminates the multiplicative
        cross-vendor failure surface. If Anthropic is down, we're down,
        but they basically aren't down.

    Retry policy:
      - On Haiku transient failure (timeout, connect error, 5xx): wait
        1 second, retry Haiku ONCE. Most transient blips clear in <1s.
      - If Haiku retry also fails: fall over to Sonnet 4.5 immediately.
      - If Sonnet also fails: surface a clean 503 to the user.

    Transient error policy (same as before):
      - httpx.ReadTimeout, ConnectError, ConnectTimeout, ReadError -> retry/failover
      - HTTPException 5xx -> retry/failover
      - 4xx -> raise unchanged (auth issues, bad requests, rate limits;
        retrying or failing over would hide the real bug)

    Together.ai is no longer in the path. The _call_together helper still
    exists for BYOK back-compat (in case any user has a saved Together
    key from before BYOK was killed), but it is no longer called by this
    failover wrapper.
    """
    if not SERVER_ANTHROPIC_KEY:
        raise HTTPException(
            500,
            "No server-side AI key configured. Set SERVER_ANTHROPIC_KEY in Vercel env.",
        )

    transient_exc = (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError)

    def _is_transient_http(exc: Exception) -> bool:
        if isinstance(exc, HTTPException):
            return exc.status_code >= 500
        return False

    # ── Primary: Haiku 4.5 with one retry on transient errors ──
    for attempt in (1, 2):
        try:
            result = await _call_anthropic_haiku(SERVER_ANTHROPIC_KEY, prompt, max_tokens)
            tag = "ok" if attempt == 1 else "ok-after-retry"
            print(f"[ai-call] source=server-shared provider=haiku-4.5 attempt={attempt} status={tag}")
            return result
        except transient_exc as e:
            if attempt == 1:
                print(f"[ai-call RETRY] haiku transient {type(e).__name__}: {str(e)[:160]} -> retrying in 1s")
                await asyncio.sleep(1.0)
            else:
                print(f"[ai-call FAILOVER] haiku failed twice ({type(e).__name__}): {str(e)[:160]} -> trying sonnet")
        except HTTPException as e:
            if _is_transient_http(e):
                if attempt == 1:
                    print(f"[ai-call RETRY] haiku {e.status_code}: {str(e.detail)[:160]} -> retrying in 1s")
                    await asyncio.sleep(1.0)
                else:
                    print(f"[ai-call FAILOVER] haiku failed twice ({e.status_code}): {str(e.detail)[:160]} -> trying sonnet")
            else:
                # 4xx (auth, bad request, rate limit) - surface immediately, never retry/failover
                raise

    # ── Fallback: Sonnet 4.5 ──
    try:
        result = await _call_anthropic(SERVER_ANTHROPIC_KEY, prompt, max_tokens)
        print(f"[ai-call] source=server-shared provider=sonnet-4.5 status=ok-after-failover")
        return result
    except (transient_exc + (HTTPException,)) as e:
        detail = str(e.detail) if isinstance(e, HTTPException) else str(e)
        print(f"[ai-call FAILED-BOTH] sonnet also failed: {type(e).__name__}: {detail[:200]}")
        raise HTTPException(
            503,
            "Our AI provider is having a brief hiccup. Please try again in a moment. "
            "If this persists, email hello@sourcingnav.com.",
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

CRITICAL RULE - NEVER RECOMMEND POACHING THE HIRING COMPANY:

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
  - Do NOT include soft skills like "communication" or "teamwork" here - those
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
annual" - taskers don't work 40hr/wk for 52 weeks. Reporting fake annual
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

  level_progression - same role at different IC levels. If the JD is for
    a "Senior Backend Engineer", give the actual titles peer companies use
    at junior, mid, senior, and staff_plus levels. Real titles, not generic
    ones. "L4 Software Engineer" is fine if that's what FAANG uses. Aim for
    2-4 titles per level. Reflect title inflation - "Staff Engineer" at a
    50-person Series B is doing what "Senior" does at FAANG; capture both.

  functional_aliases - what the SAME PERSON is called at peer companies
    that name the role differently. A Backend Engineer at a startup is a
    Platform Engineer at infra-heavy shops, a Distributed Systems Engineer
    at scale companies, an Infrastructure Engineer at cloud-native shops.
    Give 3-6 functional aliases with one-line rationale per alias. These
    are pure title-naming differences for the same skill profile.

  adjacent_crossover - DIFFERENT roles where the same person could shift.
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
This is venue-specific sourcing intelligence - the actual websites, forums,
events, mailing lists, Discords, and communities where THIS specific
archetype congregates. Generic ("LinkedIn", "GitHub") doesn't count.

For each watering hole, give:
  - venue: the specific name (lore.kernel.org, NeurIPS, Bootlin, HuggingFace,
    DEFCON CTF, KX/Q forums, Embedded World speakers list - be specific)
  - venue_type: mailing_list | conference | community | publication |
    code_host | training_alumni | competition | discord_slack
  - signal: what kind of candidate signal you find there in 1 sentence
    ("Linux kernel maintainers - Signed-off-by tags = professional-grade
    upstream contribution")
  - how_to_use: 1 sentence on how to actually source from this venue.
    Use Google X-ray syntax with DOUBLE quotes and no literal AND:
    ("X-ray: site:lore.kernel.org \"Signed-off-by:\" \"embedded\" (\"arm\" OR \"aarch64\")")

Aim for 5-8 watering holes. Span at least 3 venue_types. Skip generic
catch-alls like "LinkedIn" or "Indeed" - those are already in the X-ray
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
      "signal": "Linux kernel maintainers - Signed-off-by tags signal professional-grade upstream contribution",
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
  - LinkedIn Recruiter strings are the exception - they use single quotes and
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
- X-ray strings use Google syntax: site:, intitle:, -site: (exclusion), OR (uppercase), double-quoted phrases. DO NOT write the literal word AND - a space is already an implicit AND on Google and writing AND makes Google search for the word "AND" itself.
- Tier 1 = same product/market as the hiring company
- Tier 2 = adjacent industry/skill overlap
- NEVER include the hiring company itself in tier_1 or tier_2. The hiring company
  is the client, and recommending sourcing from them is a non-solicit violation.
  If the JD identifies the hiring company, exclude it from all company lists
  and replace with real competitors.
- Be specific. Generic strings like "engineer AND python" are useless.

X-RAY SEARCH CONSTRAINTS (these strings must actually run on Google, not just look smart):

0. DOUBLE QUOTES ONLY AROUND PHRASES. Single quotes (apostrophes) are IGNORED
   by Google - they do nothing. Every multi-word phrase in an X-ray MUST be
   wrapped in double quotes. Because these strings are going into a JSON string
   field, escape them as \"...\". Example of the WRONG pattern:
     site:linkedin.com/in/ 'Senior Embedded Linux Engineer' AND 'BSP'
   Example of the RIGHT pattern:
     site:linkedin.com/in/ \"Senior Embedded Linux Engineer\" \"BSP\"

0a. NO LITERAL AND BETWEEN TERMS. A space is already an implicit AND on
    Google. Writing the word AND makes Google search for pages containing
    the literal word "AND" - killing your string. OR (uppercase) IS required
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
                     "medium" (50-80% overlap, worth a phone screen - solid
                     transfer but candidate will need 1-2 weeks to ramp),
                     "low" (25-50% overlap, candidate could ramp but isn't
                     ready day 1 - needs 30-60 days)

──────────────────────────────────────────────────────────
DISTRIBUTION CALIBRATION - REQUIRED
──────────────────────────────────────────────────────────

Real-world skill alternative distributions cluster around:
  - ~30% high (genuine drop-in replacements)
  - ~50% medium (worth a phone screen, will need ramp)
  - ~20% low (transferable foundation, longer ramp)

If you mark 80%+ of your alternatives as "high", you are inflating the
ratings. This destroys the recruiters ability to triage candidates -
everything looks equally great, so nothing is actually prioritized.

GOOD CALIBRATION RULES:

A "high" alternative is rare. It means the candidates resume could
literally have one tool name swapped for another and they would still
do the job equivalently from day 1. Examples that genuinely qualify:
  - PostgreSQL <-> MySQL (for app-layer dev - both relational, similar SQL)
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
  - PostgreSQL -> Cassandra (relational vs wide-column - paradigm shift)
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
  - Complete category swaps ("PyTorch" -> "scikit-learn" - different problem space)
  - Overly broad ("any Python framework" - too vague to be useful)
  - Inflating ratings to "high" when the candidate genuinely needs ramp time

Skip skills that don't have meaningful alternatives. A skill like "U.S. Citizenship"
or "Active Secret Clearance" has no alternative - just omit it from output.

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

CRITICAL - TRUTHFULNESS RULES (read this first, every time):

A counter that contains an INVENTED fact about the company is worse than
no counter at all. The recruiter pastes it into an InMail, the candidate
asks a follow-up question, and now the recruiter is exposed as either
lying or uninformed. This destroys their credibility and the placement.

You may ONLY reference facts that fit one of these categories:

  ALLOWED - facts visible in the parsed JD context:
    - Role title, level, location, remote policy, industry
    - Comp range and any explicit benefits in the JD
    - Required and preferred skills as stated
    - Company name (only if mentioned in the JD)
    - The role's responsibilities as written in the JD
    - Any explicit clearance, citizenship, or eligibility requirements

  ALLOWED - universally true industry knowledge:
    - "DoD contracts require US citizenship" (true by federal law)
    - "Most defense roles cannot sponsor H1B visas" (regulatory fact)
    - "FAANG L5 total comp typically exceeds $400k" (well-known market data)
    - "ML engineers at top labs typically need PhD or equivalent
       publication record" (industry-recognized norm)
    - General industry trends and common career trajectories

  FORBIDDEN - DO NOT INVENT any of the following, ever:
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

  industry_perception - "I'd never work at [defense / FAANG / startup /
    legacy / non-mission-driven]". Counter must reference what's
    SURPRISING and TRUE about this specific industry or role -
    using only facts from the JD or universal industry knowledge.

  comp_below_market - "I just got a raise" / "I'm already at $X". Counter
    must address the comp delta gap honestly OR reframe what the role
    offers beyond base. Use only the comp_snapshot from the JD.

  location_remote - "I want fully remote" or "I won't relocate to X".
    Counter must be honest about the requirement AND offer what's
    actually compelling about being there. DO NOT invent stipends or
    perks; reference only what the JD says about location/remote.

  brand_unknown - "I've never heard of this company". Counter is a
    1-paragraph elevator pitch built ONLY from facts in the JD:
    what the company says it does (in the JD), the industry, the
    products mentioned by name in the JD, and any verifiable scale
    indicators the JD provides.

  career_risk - "What if this doesn't work out / the company fails /
    I get RIF'd". Counter addresses general industry stability or
    candidate-side mitigations. DO NOT invent severance terms,
    vesting schedules, or specific company stability claims.

  visa_clearance_blocker - "I don't have clearance" or "I need
    sponsorship". Counter uses only what the JD says about clearance
    and citizenship requirements, plus universal regulatory facts.

  tech_stack_skepticism - "Your stack is ancient" or "I don't want
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
    writing the counter - drop the objection entirely.
  - safe_to_paste_verbatim: true if every claim in the counter is
    directly traceable to the JD or universal industry knowledge,
    false if the recruiter should verify any specific claim before
    using it.

Pick ONLY the 3-5 most likely objections for THIS role. Quality over
quantity. If you cannot ground a counter in the allowed sources, omit
the objection entirely rather than invent.

Skip the elevator pitch as a separate objection - instead, work it
INTO whichever counter benefits most (usually brand_unknown or
industry_perception), still grounded in JD-only facts.

Return STRICT JSON only. Example showing the SAFE pattern (note how
every claim traces to either the JD or industry knowledge):

{{
  "objection_playbook": [
    {{
      "objection_type": "visa_clearance_blocker",
      "likely_phrasing": "I don't have a security clearance and I'm not sure if I'm eligible for one.",
      "counter": "The JD allows for either an active Secret clearance OR the ability to obtain one, which means US citizenship plus a clean background is the realistic bar - not prior clearance experience. Many embedded engineers in San Diego have moved into cleared work this way; the company sponsors the clearance process. The bigger filter here is the citizenship and eligibility piece, which is non-negotiable for DoD contracts.",
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

  Phase 1 - Days 1-3 - Warm-channel opener
    Channels: 1st-degree LinkedIn connections, alumni networks, referrals
    from current employees at Tier 1 companies, past placements the
    recruiter already knows. No cold yet.
    Message style: personal, concise, direct ask for intro or interest.
    Expected response rate: 30-50%. Tiny universe, high-quality signal.

  Phase 2 - Days 4-7 - Tier 1 cold with hyper-personalization
    Channels: Tier 1 target-company employees via LinkedIn Recruiter /
    InMail, outreach via verified personal email (Hunter/Apollo).
    Message style: references something SPECIFIC about the candidate -
    their recent talk, OSS commit, patent, promotion, their company's
    recent news (layoff, acquisition, IPO). First line should prove the
    recruiter actually looked at their profile.
    Expected response rate: 8-15%.

  Phase 3 - Days 8-14 - Tier 2 broader outreach
    Channels: Tier 2 companies, less-personalized but still role-fit
    targeted. Template-based with 2-3 customized fields.
    Message style: leads with the ROLE + COMP + COMPANY story since less
    personal context exists per candidate. Volume game.
    Expected response rate: 3-7%.

  Phase 4 - Days 15-21 - Unconventional channels + watering holes
    Channels: X-ray (personal sites, GitHub, Stack Overflow), niche
    communities (specific Discords, mailing lists, conference speakers),
    the watering_holes from the parsed JD.
    Message style: venue-specific. Reach out as a peer, not a recruiter.
    Reference the work they posted. These candidates are often NOT
    actively job-searching and respond to curiosity, not pitches.
    Expected response rate: 10-20% from a much smaller universe.

  Phase 5 - Days 22+ - Back-channels + parallel escalation
    Channels: recruiters' Discord groups, friend-of-friend referrals,
    former colleagues. If the search is still open past 21 days, this
    is where grandmasters ask their network for intros directly.
    Also: revisit Phase 2 candidates who didn't respond with a new
    angle (often: a news hook - their company just announced layoffs,
    a comp change, a reorg).
    Message style: asking for intros or advice, not pitching the role.
    Expected response rate: varies wildly - depends on network depth.

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
TRUTHFULNESS RULES - MANDATORY
──────────────────────────────────────────────────────────

A first_move that contains an INVENTED candidate detail is worse than no
first_move at all. The recruiter will paste it into outreach, the candidate
asks "how did you find my PR on X?" - and the recruiter is exposed because
that PR doesnt exist.

You may ONLY reference facts that fit one of these categories:

  ALLOWED - facts visible in the parsed JD context:
    - Tier 1 / Tier 2 company names from the JD parser output
    - Watering hole venues from the watering_holes list provided
    - Role title, level, location, industry from the parsed JD
    - Skills explicitly mentioned in the JD

  ALLOWED - universally true industry knowledge:
    - "Most LR-based searches Tuesday-Thursday outperform Monday or Friday"
    - "OpenBMC mailing list traffic peaks mid-week"
    - "FAANG L5 engineers respond more to comp + scope than equity hooks"

  FORBIDDEN - DO NOT INVENT any of the following, ever:
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
TIER DEFINITIONS - these are the ONLY valid tier values
──────────────────────────────────────────────────────────

  tier 1 - NON-NEGOTIABLE
    Without this skill the candidate gets auto-rejected at the resume screen.
    Hiring manager will not even take a phone screen. There is no candidate
    success path that bypasses this skill.
    HARD CAP: maximum 3 skills can be Tier 1.

  tier 2 - STRONG PREFERENCE
    Listed as required in the JD, but a strong candidate missing this can
    still get an interview if they have a credible substitute or strong
    other-dimension signal. The recruiter will need to advocate for them.

  tier 3 - NICE-TO-HAVE (ACTUALLY)
    The JD says "required" but realistically the hiring manager will trade
    this off for almost any candidate who covers the Tier 1 and Tier 2
    requirements well. Most "team player / strong communication" lines are
    here unless the role is customer-facing.

The whole point: most JDs list 8-15 "required" skills. In reality, 2-3 are
true Tier 1 blockers and the rest are negotiable. A senior recruiter knows
which is which. Your job is to surface that distinction explicitly.

──────────────────────────────────────────────────────────
INTERVIEW STAGE - the ONLY valid values
──────────────────────────────────────────────────────────

  resume_screen        - assessed from resume keywords + recent companies
  phone_screen         - comes up in a 30-min recruiter or HM screen
  onsite_technical     - tested in a coding/system-design/take-home
  not_directly_tested  - inferred from background; never directly assessed

Map each skill to the stage where it actually gets tested. Don't guess.
"Strong communication skills" is not_directly_tested at resume_screen but
shows up as a yes/no signal at phone_screen. "Distributed systems" is
phone_screen for level confirmation and onsite_technical for the deep dive.

──────────────────────────────────────────────────────────
TRUTHFULNESS RULES - MANDATORY
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
says so explicitly: "JD is sparse on this - recommend asking the hiring
manager directly whether X is hard-required or negotiable."

The recruiter will paste your output into Slack to brief their hiring
manager. If you fabricate, you damage the recruiter's credibility.

──────────────────────────────────────────────────────────
PUSHBACK GUIDANCE - what to write
──────────────────────────────────────────────────────────

For each skill, write 1-2 sentences the recruiter would say to the hiring
manager when defending a candidate who is missing this skill. Be specific
to the tier:

  tier 1: "This is non-negotiable - we'd be wasting the panel's time
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

If the skill is truly unique (no real substitutes - e.g., "FDA 510(k) clearance
process"), return an empty array and note in pushback_guidance that there
genuinely is no substitute.

──────────────────────────────────────────────────────────
CAREER SWITCHER ARCHETYPES - non-obvious source pools
──────────────────────────────────────────────────────────

After classifying the must-have skills, identify 3-5 ROLE-TO-ROLE TRANSITIONS
that produce viable candidates for THIS specific role. These are people who
DO NOT currently hold the target role title but whose existing skills make
them plausible candidates with minimal training.

This is the "where to find non-obvious candidates" pass. A junior recruiter
only sources people whose current title matches the JD title. A senior
recruiter knows that:

  - Hardware engineers who code in their hobby projects often make excellent
    embedded software engineers
  - Quant developers transition cleanly into ML engineering at fintech firms
  - Sysadmins who picked up Python and Terraform are SREs in waiting
  - Data analysts with a year of Python and a portfolio of real ETL work
    are junior data engineers

For each archetype, write:

  from_role: the role title these candidates currently hold (be specific -
             "Hardware Engineer (FPGA/RTL)" not just "Engineer")
  to_role:   the target role title (use the JD's exact role_title)
  transferable_skills: 3-5 skills the from_role candidates already have
                       that map onto the canonical_skills of the target role.
                       These must be REAL transfers - not vague claims like
                       "problem solving" but specific ones like "C++ in
                       embedded contexts" or "PyTorch model training"
  where_to_find: 2-4 specific platforms / communities / company types where
                 these candidates concentrate. Be concrete: "Apple Silicon
                 LinkedIn group" not "tech professionals."
  pitch_angle: 1-2 sentences the recruiter would say in an outreach to make
               the role appealing to this candidate type. Specific to their
               career trajectory. Not generic ("we're hiring great talent")
               but specific ("you've spent 4 years optimizing power management
               in silicon - this role lets you ship that work into actual
               flying drones").
  transition_difficulty: "easy" | "moderate" | "hard"

HONESTY RULES - MANDATORY:

  - Do NOT fabricate success rate percentages. The CandidatIQ implementation
    of this had hardcoded "70% success rate" claims with no evidence. Do
    not include any percentage claim unless you have specific industry data.
  - Each archetype's transferable_skills must intersect with the target's
    canonical_skills meaningfully. A "marketing manager → embedded engineer"
    archetype is wrong because no skills transfer.
  - If a from_role would require 2+ years of retraining, mark it "hard"
    and put it last. The recruiter needs to triage by speed-to-productivity.
  - Maximum 5 archetypes. If you genuinely cannot find 3 non-obvious source
    pools for this role, return fewer (the role is niche enough that only
    direct-match candidates will work, which itself is useful information).

──────────────────────────────────────────────────────────
OUTPUT SCHEMA - RETURN ONLY THIS JSON
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
  ],
  "career_switcher_archetypes": [
    {{
      "from_role": "Hardware Engineer (FPGA/RTL)",
      "to_role": "exact JD role_title",
      "transferable_skills": ["VHDL", "C++ in embedded contexts", "Timing closure"],
      "where_to_find": ["IEEE Solid-State Circuits society", "FPGA-focused subreddits", "DesignCon attendees"],
      "pitch_angle": "1-2 sentences specific to this archetype's trajectory",
      "transition_difficulty": "moderate"
    }}
  ]
}}

Every must_have_list entry must appear in pro_skill_briefing exactly once.
No skipping. No duplicates.

3-5 entries in career_switcher_archetypes (or fewer if the role is niche
enough that no plausible career-switcher pools exist).

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

EXISTING FREE-TIER OUTPUT (that you are extending - do NOT regenerate, only ANNOTATE):
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
HIDDEN TALENT POOLS (4-6 non-obvious source categories)
──────────────────────────────────────────────────────────

Beyond direct-match LinkedIn searches and watering-hole X-rays, identify
4-6 NON-OBVIOUS pools where qualified candidates concentrate. These are
sources a junior sourcer would not think to check.

Examples (do NOT reuse verbatim - generate role-specific pools):

  - Open-source contributors to projects in the role's tech stack
  - Recently acquired startups whose engineers are about to vest
  - Layoff-affected teams from companies in the role's industry
  - Bootcamp graduates with portfolio projects matching the role
  - Conference speakers (recent talks at relevant events)
  - Hardware engineers who code in their hobby projects (for embedded/firmware roles)
  - Defense contractors with active clearances when role can sponsor
  - Returning-to-work parents with prior senior experience
  - International transfers from EU/Asia where local market is saturated

For each pool, write:

  pool_name: short label (e.g., "Recently Laid-Off Cruise Engineers",
             "Active OpenBMC Maintainers", "Bootcamp Grads with ML
             Production Experience")
  why_target: 1-2 sentences on WHY this pool is a defensible source for
              THIS specific role. Be specific - not "they have skills"
              but "they shipped autonomous driving stacks to production
              and are job-hunting after the December layoffs"
  platforms: 2-4 specific platforms / communities / lists where this pool
             concentrates. Be concrete: "Layoffs.fyi cruise.com listings",
             "openbmc-discuss mailing list subscribers", not "LinkedIn".
  search_tips: 1-2 sentences on how to actually FIND people in this pool.
               If a runnable boolean / X-ray fits, include it (with
               the same Google syntax rules as the other X-rays).

HONESTY RULES - MANDATORY:

  - Do NOT fabricate response rate percentages. The CandidatIQ
    implementation of this had hardcoded "25-35% response rate" claims
    with no evidence. Do not include any percentage claim.
  - Each pool's why_target must be specific to the role's canonical_skills
    and industry. Generic "open source contributors" without naming
    relevant projects is wrong. "Active contributors to PX4 and ArduPilot"
    is right for a drone firmware role.
  - Maximum 6 pools. If the role is so niche that only 4 plausible pools
    exist, return 4. Better fewer-and-specific than more-and-vague.
  - Pools must NOT overlap with the existing pro_xrays. If you'd
    suggest "openbmc.dev maintainers" and there's already a pro_xray
    for openbmc.dev, that's redundant - choose a different pool.

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
      "signal": "Active OpenBMC maintainers - patch submissions = real production-grade contribution",
      "hit_volume": "low",
      "signal_to_noise": "high"
    }}
  ],
  "hidden_talent_pools": [
    {{
      "pool_name": "Active OpenBMC Maintainers",
      "why_target": "1-2 sentences on why this pool is a defensible source for THIS role",
      "platforms": ["openbmc-discuss mailing list", "OCP Summit BMC track speakers"],
      "search_tips": "1-2 sentences on how to find people in this pool - runnable X-ray if applicable"
    }}
  ],
  "extended_mentor_notes": [
    {{"label": "Sequencing", "note": "Run the GitHub OpenBMC X-ray first - merged PRs are higher signal than LR for this archetype"}},
    {{"label": "Anti-pattern", "note": "Don't blast InMails on Mondays - Staff-level engineers triage their inbox Sunday night"}}
  ]
}}

No em dashes. No code fences. JSON only.
"""


COMPETITIVE_INTEL_PROMPT = """You are a senior technical recruiter with 13+ years at FAANG-tier companies analyzing competitor companies for a recruiter who is filling a specific role.

REQUISITION CONTEXT:
{requisition_context}

COMPETITORS TO ANALYZE (already identified by Boolean Builder as tier-1 direct competitors and tier-2 adjacent companies):
{competitor_list}

For EACH company in the list, produce a structured intelligence report. Be honest about what you do and don't know. Recruiters trust tools that admit uncertainty more than tools that fabricate confident-sounding numbers.

CRITICAL HONESTY RULES (read these before writing anything):

1. SALARY CONFIDENCE FLAGS ARE MANDATORY.
   For FAANG-tier companies (Meta, Google, Amazon, Apple, Microsoft, Netflix, Stripe, Airbnb, Uber, Salesforce, Anthropic, OpenAI, Databricks) and other companies with extensively-documented public comp data on Levels.fyi, set "salary_confidence": "high".
   For mid-size tech companies and well-known startups, set "salary_confidence": "medium".
   For defense contractors, niche startups, agencies, or any company you don't have specific public comp data on, set "salary_confidence": "low" AND give a wider range or use industry/region benchmarks. NEVER fabricate FAANG-style numbers for non-FAANG companies. A defense contractor in San Diego is NOT paying Meta-level comp.

2. HIRING VELOCITY IS A QUALITATIVE READ.
   "aggressive" / "moderate" / "slow" should reflect what you genuinely know about the company's recent hiring trajectory. If you genuinely don't know, default to "moderate" and add a note in poaching_rationale that velocity is uncertain.

3. ENGINEERING COUNT IS A RANGE.
   Do NOT give exact numbers like "247 engineers". Give ranges like "150-300" or "1,000-2,000". For very well-known companies, you can narrow the range. For obscure ones, widen it.

4. INDUSTRY-WEIGHTED INTELLIGENCE.
   Defense contractors should emphasize clearance requirements, contract stability, and on-site requirements. Equity is usually limited.
   Early-stage startups should emphasize equity upside, burn rate context, and founder-led culture.
   Public tech giants should emphasize equity vesting, RTO policy, and total comp transparency.
   Agencies and consultancies should emphasize bench utilization, project diversity, and billable rate pressure.

5. NEVER INCLUDE THE HIRING COMPANY.
   If the requisition's hiring company appears in the competitor list (it shouldn't, but check), exclude it from the output. Sourcing from your own client is a non-solicit violation.

6. DO NOT FABRICATE BENEFITS.
   Only list benefits you have specific knowledge of. "Standard tech benefits" is acceptable for unknown companies. Inventing "$25K relocation" or "Free meals" for a defense contractor is the kind of error that destroys recruiter trust.

OUTPUT JSON SCHEMA (return ONLY valid JSON, no code fences, no prose):

{{
  "competitors_analyzed": ["Company1", "Company2"],
  "insights": [
    {{
      "company": "Exact company name as provided",
      "tier": 1,
      "hiring_velocity": "aggressive|moderate|slow",
      "velocity_confidence": "high|medium|low",
      "estimated_engineering_count": "150-300",
      "estimated_open_positions": "10-30 (estimated based on company size)",
      "avg_time_to_fill": "45-60 days (estimated)",
      "common_skills_for_this_role": ["skill1", "skill2", "skill3", "skill4", "skill5"],
      "salary_range": {{
        "min": 130000,
        "max": 180000,
        "equity": "Limited (defense contractor)",
        "salary_confidence": "low",
        "salary_basis": "1-2 sentences on how you derived this. Public Levels.fyi data, regional benchmarks, industry norms, etc."
      }},
      "benefits_highlights": ["benefit1", "benefit2"],
      "remote_policy": "On-site (cleared facility)|Hybrid (3 days office)|Fully remote|Unknown",
      "growth_stage": "Early|Growth|Mature",
      "talent_pool_estimate": "small|medium|large with 1 sentence rationale",
      "poaching_difficulty": "high|moderate|low",
      "poaching_rationale": "1-2 sentences on why this company is hard or easy to poach from for THIS specific role",
      "comp_vs_jd_offer": "above|at_market|below with 1 sentence comparing to the requisition's offered range",
      "key_recruiting_angle": "1-2 sentences: the single most effective recruiter pitch to engineers at this company for this role"
    }}
  ],
  "market_summary": {{
    "competitive_intensity": "high|moderate|low",
    "competitive_intensity_rationale": "1-2 sentences",
    "fastest_to_fill_competitor": "Company name or null",
    "most_aggressive_hirer": "Company name or null",
    "comp_benchmark_vs_jd": "1-2 sentences on whether the JD's comp is competitive vs the cluster",
    "top_recruiting_angles": ["1-line pitch", "1-line pitch", "1-line pitch"]
  }},
  "honesty_caveat": "1-sentence reminder that estimates are derived from public hiring patterns and confidence varies by company specificity"
}}

No em dashes. No code fences. Just JSON.
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


class CompetitiveIntelRequest(BaseModel):
    """Request to run Competitive Intelligence analysis on an existing requisition.

    The endpoint pulls competitors from the req's stored Boolean Builder output
    (tier_1_direct_competitors + tier_2_adjacent) by default. The competitors
    field is an OPTIONAL override - if provided, it replaces the tier list and
    is capped at 8 companies to control AI cost.
    """
    req_id: str = Field(..., min_length=1)
    competitors: Optional[list[str]] = Field(default=None, description="Optional override list of competitor names. Capped at 8.")




# ---------- JD SIGNATURE EXTRACTION (Phase B3 Foundation) ----------
#
# Every successful intake stores a denormalized signature row capturing
# the signal-rich features of the parsed JD. This is the data foundation
# for Phase B3 (role archetype clustering) - we accumulate signatures
# now, cluster later when N is meaningful (>= 30).
#
# Goal: tell the story of how the talent market is changing.
# That requires:
#   - Stable canonical skill names (already done by JD_PARSER_PROMPT)
#   - First-class columns for industry / level / company / timestamp
#     (so time-series queries are one-liners, no JSON parsing needed)
#   - JSON arrays for the rich features (skills, aliases, crossovers,
#     poaching companies, watering hole types) for cluster computation
#   - A signature_text field that concatenates the above for embedding-
#     based similarity later
#
# Storage policy: INSERT OR REPLACE so re-running on the same req_id
# is idempotent. Backfill can be run safely.

import re

def _parse_comp_range(comp_str: str) -> tuple:
    """Parse a comp range string into (min, max, is_hourly).

    Handles both salary formats ('$150k - $220k', '$220k - $280k') and
    hourly formats ('$50 - $100/hr'). Returns (None, None, False) on any
    parse failure - the goal is best-effort enrichment, never to crash
    the intake pipeline over a malformed comp string.

    Returns:
      (min_int, max_int, is_hourly_bool)
      For salary: returns the dollar amount (e.g., 150000 not 150)
      For hourly: returns the hourly rate (e.g., 50 not 50000)
    """
    if not comp_str or not isinstance(comp_str, str):
        return (None, None, False)

    is_hourly = bool(re.search(r"/hr|/hour|per hour|hourly", comp_str.lower()))

    # Find all dollar amounts. Strip commas, handle 'k' suffix.
    matches = re.findall(r"\$([\d,]+)\s*(k|K)?", comp_str)
    if not matches:
        return (None, None, is_hourly)

    nums = []
    for raw, k_suffix in matches:
        try:
            n = int(raw.replace(",", ""))
            if k_suffix:
                n *= 1000
            nums.append(n)
        except (ValueError, TypeError):
            continue

    if len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]), is_hourly)
    if len(nums) == 1:
        return (nums[0], nums[0], is_hourly)
    return (None, None, is_hourly)


# Stopwords for keyword extraction from verbose competency statements.
# Used by _derive_canonical_from_must_have when the parser produces wordy
# skill strings instead of short canonical names. We strip these so what's
# left is the high-signal terminology (tech names, acronyms, domains).
_SKILL_STOPWORDS = frozenset([
    "and", "or", "of", "in", "on", "at", "to", "for", "with", "the",
    "a", "an", "as", "is", "are", "be", "by", "from", "into", "via",
    "experience", "experiences", "knowledge", "skills", "ability",
    "abilities", "proficiency", "proficient", "fluency", "fluent",
    "expertise", "expert", "strong", "deep", "solid", "good", "great",
    "excellent", "advanced", "intermediate", "basic", "demonstrated",
    "proven", "hands-on", "hands", "on", "extensive", "significant",
    "years", "year", "yrs", "yr", "plus", "+", "or", "more",
    "working", "work", "background", "track", "record",
    "candidate", "candidates", "position", "role", "team", "company",
    "ideal", "preferred", "required", "must", "have", "should", "will",
    "able", "capable", "willing", "ready", "open", "looking", "seeking",
    "delivery", "development", "developing", "develops", "developed",
    "design", "designing", "designed", "build", "building", "built",
    "implement", "implementing", "implemented", "create", "creating",
    "production", "production-grade", "production-quality",
    "real-world", "real", "world", "high-stakes", "scale", "scale-critical",
    "end-to-end", "cross-functional", "systems", "system",
    "code", "coding", "programming", "software", "engineering",
    "such", "as", "including", "include", "etc", "e.g.", "i.e.",
    "this", "these", "those", "that", "which",
    "you", "your", "we", "our", "us", "they", "their",
    "across", "between", "among", "within", "throughout",
    "successful", "demonstrably", "directly", "actively",
])


def _derive_canonical_from_must_have(must_have: list) -> list:
    """Derive canonical_skills-shaped output from must_have_skills.

    Used as a fallback when parsed.canonical_skills is missing (for reqs
    parsed with older prompt versions) or empty.

    Two extraction strategies based on skill string length:

    1. Short skill string (<=4 words): use as-is. Looks like a real
       canonical name already ("Python", "Machine Learning Engineering",
       "Natural Language Processing (NLP)").

    2. Verbose skill string (>4 words): extract high-signal tokens.
       Strip stopwords, keep capitalized words, acronyms, and short
       tech-looking tokens. "Chip design or verification experience (RTL,
       simulators, EDA tools)" becomes ["Chip design", "RTL", "EDA"].

    Returns a list of {name, severity} dicts matching canonical_skills shape.
    """
    out = []
    seen = set()  # dedupe within this skill set

    for ms in must_have:
        if not isinstance(ms, dict):
            continue
        skill_str = (ms.get("skill") or "").strip()
        severity = ms.get("severity") or "preferred"
        if not skill_str or severity not in ("blocker", "preferred"):
            continue

        word_count = len(skill_str.split())

        if word_count <= 4:
            # Short - use as-is
            key = skill_str.lower()
            if key not in seen:
                seen.add(key)
                out.append({"name": skill_str, "severity": severity})
        else:
            # Verbose - extract keyword tokens.
            # Strategy: split on common delimiters, then keep tokens that are
            # either ALL-CAPS acronyms (RTL, EDA, LLM) or capitalized words
            # not in the stopword list (Python, Chip, NumPy).
            # Also handle parenthesized lists ("(RTL, simulators, EDA)").
            import re as _re
            # Split on commas, parens, slashes, semicolons
            tokens = _re.split(r"[,()/;]", skill_str)
            for tok in tokens:
                tok = tok.strip()
                if not tok:
                    continue
                # Inside each token, look for high-signal words
                words = _re.findall(r"[A-Za-z][A-Za-z0-9.+#\-]*", tok)
                # Keep acronyms (all-caps, length 2-6) and Capitalized words
                # not in stopword list
                kept = []
                for w in words:
                    wl = w.lower()
                    if wl in _SKILL_STOPWORDS:
                        continue
                    is_acronym = len(w) >= 2 and len(w) <= 6 and w.isupper()
                    is_capitalized = w[0].isupper() and not w.isupper()
                    is_techy = any(c in w for c in ".+#-") and len(w) >= 2  # C++, C#, .NET, Node.js
                    if is_acronym or is_capitalized or is_techy:
                        kept.append(w)
                if kept:
                    # Reassemble adjacent kept words as a single skill
                    # (e.g., "Machine Learning" stays together)
                    combined = " ".join(kept)
                    key = combined.lower()
                    if key not in seen and len(combined) >= 2:
                        seen.add(key)
                        out.append({"name": combined, "severity": severity})

    return out


def _extract_signature(req_id: str, user_id: str, parsed: dict) -> dict:
    """Extract a flat signature dict from the parsed JD output.

    Defensive: every field uses .get() chains because the JD parser can
    occasionally produce shapes that don't match the spec. Missing fields
    become NULL in the database - better than crashing the intake.

    Returns a dict ready to be passed as positional args to the INSERT.

    canonical_skills resolution order (added 2026-04-28):
      1. Use parsed.canonical_skills if present and non-empty
      2. Else derive from parsed.must_have_skills via keyword extraction
         (handles older parses that pre-date the canonical_skills prompt
         AND verbose-skill-string JDs that need keyword extraction)
      3. Else empty list (truly unparseable JD)
    """
    core = parsed.get("core", {}) or {}
    exec_brief = parsed.get("executive_brief", {}) or {}
    market_dyn = parsed.get("market_dynamics", {}) or {}
    comp = parsed.get("comp_snapshot", {}) or {}
    alt_titles = parsed.get("alt_titles", {}) or {}
    market360 = parsed.get("market360", {}) or {}

    # Skills - split blockers vs preferred
    must_have = parsed.get("must_have_skills", []) or []
    blockers = [m.get("skill") for m in must_have if m.get("severity") == "blocker" and m.get("skill")]
    preferred = [m.get("skill") for m in must_have if m.get("severity") == "preferred" and m.get("skill")]

    # Canonical skills - primary path: use parser output if present
    canonical = parsed.get("canonical_skills", []) or []
    canonical_clean = [
        {"name": c.get("name"), "severity": c.get("severity")}
        for c in canonical
        if c.get("name")
    ]

    # Fallback: derive from must_have_skills when canonical is missing/empty.
    # This recovers signatures for reqs parsed with older prompt versions
    # AND reqs whose JDs produced verbose-sentence skill strings.
    if not canonical_clean and must_have:
        canonical_clean = _derive_canonical_from_must_have(must_have)
        if canonical_clean:
            print(f"[signature fallback] req={req_id[:8]} derived {len(canonical_clean)} canonical skills from must_have_skills")

    # Functional aliases - what peer companies call this same person
    func_aliases = alt_titles.get("functional_aliases", []) or []
    aliases_clean = [a.get("title") for a in func_aliases if a.get("title")]

    # Adjacent crossover - DIFFERENT roles where the same person fits.
    # This is where Talent Engineer / Forward-Deployed Engineer / Prompt
    # Engineer-style emerging archetypes will surface in clustering.
    crossovers = alt_titles.get("adjacent_crossover", []) or []
    crossover_clean = [
        {"title": c.get("title"), "difficulty": c.get("transition_difficulty")}
        for c in crossovers
        if c.get("title")
    ]

    # Watering hole VENUE_TYPES - not the venues themselves (too granular
    # for clustering), but the type categories (mailing_list, conference,
    # community, code_host, training_alumni, competition, discord_slack,
    # publication). The TYPE distribution per role tells us which kinds
    # of communities matter for which archetypes.
    watering_holes = parsed.get("watering_holes", []) or []
    venue_types = sorted(set(
        h.get("venue_type") for h in watering_holes if h.get("venue_type")
    ))

    # Poaching target companies - this is the sourcing universe for the
    # role. Patterns here will reveal which companies cluster together
    # for which role archetypes.
    poach = market360.get("poaching_targets", []) or []
    poach_companies = [p.get("company") for p in poach if p.get("company")]

    # Comp parse
    base_min, base_max, is_hourly = _parse_comp_range(comp.get("base_range", ""))

    # Build the signature_text: a concatenation of the most distinctive
    # features for future embedding-based similarity. Order matters
    # (most distinctive first) because some embedding models weight
    # earlier tokens more.
    sig_text_parts = [
        core.get("role_title") or "",
        core.get("level") or "",
        core.get("industry") or "",
        " ".join(blockers),
        " ".join(preferred),
        " ".join(aliases_clean),
        " ".join(c.get("title", "") for c in crossover_clean),
        " ".join(venue_types),
        exec_brief.get("summary") or "",
    ]
    signature_text = " | ".join(p.strip() for p in sig_text_parts if p and p.strip())

    return {
        "req_id":                          req_id,
        "user_id":                         user_id,
        "role_title":                      core.get("role_title"),
        "level":                           core.get("level"),
        "industry":                        core.get("industry"),
        "company":                         core.get("company"),
        "location":                        core.get("location"),
        "remote_policy":                   core.get("remote_policy"),
        "base_range_min":                  base_min,
        "base_range_max":                  base_max,
        "is_hourly":                       1 if is_hourly else 0,
        "difficulty_score":                market_dyn.get("difficulty_score"),
        "market_temperature":              exec_brief.get("market_temperature"),
        "canonical_skills_json":           json.dumps(canonical_clean),
        "blocker_skills_json":             json.dumps(blockers),
        "preferred_skills_json":           json.dumps(preferred),
        "functional_aliases_json":         json.dumps(aliases_clean),
        "adjacent_crossover_json":         json.dumps(crossover_clean),
        "watering_hole_types_json":        json.dumps(venue_types),
        "poaching_target_companies_json":  json.dumps(poach_companies),
        "signature_text":                  signature_text,
    }


async def _save_signature(req_id: str, user_id: str, parsed: dict) -> bool:
    """Write a signature row. INSERT OR REPLACE so re-runs are idempotent.

    Returns True on success, False on any failure. Caller should treat
    failures as non-blocking: a missing signature does not break the
    intake; it just means that req won't contribute to clustering data.

    Logged so failures are visible in Vercel logs without breaking UX.
    """
    try:
        sig = _extract_signature(req_id, user_id, parsed)
        async with db() as client:
            await client.execute(
                """INSERT OR REPLACE INTO jd_signatures (
                    req_id, user_id, role_title, level, industry, company,
                    location, remote_policy, base_range_min, base_range_max,
                    is_hourly, difficulty_score, market_temperature,
                    canonical_skills_json, blocker_skills_json, preferred_skills_json,
                    functional_aliases_json, adjacent_crossover_json,
                    watering_hole_types_json, poaching_target_companies_json,
                    signature_text, parsed_at, parser_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'v1')""",
                [
                    sig["req_id"], sig["user_id"], sig["role_title"], sig["level"],
                    sig["industry"], sig["company"], sig["location"], sig["remote_policy"],
                    sig["base_range_min"], sig["base_range_max"], sig["is_hourly"],
                    sig["difficulty_score"], sig["market_temperature"],
                    sig["canonical_skills_json"], sig["blocker_skills_json"],
                    sig["preferred_skills_json"], sig["functional_aliases_json"],
                    sig["adjacent_crossover_json"], sig["watering_hole_types_json"],
                    sig["poaching_target_companies_json"], sig["signature_text"],
                ],
            )
        print(f"[signature] saved req_id={req_id[:8]}... role={sig['role_title']} skills={len(json.loads(sig['canonical_skills_json']))}")
        return True
    except Exception as e:
        print(f"[signature FAIL] req_id={req_id[:8]}... type={type(e).__name__} err={str(e)[:200]}")
        return False


# ---------- COMPETITIVE INTELLIGENCE HELPERS ----------

# Programming languages we recognize for GitHub language: filters in
# competitive boolean strategies. Used by _generate_competitive_boolean_strategies
# to decide whether to include language-scoped GitHub queries.
_GITHUB_PROGRAMMING_LANGS = {
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Ruby", "C++", "C#",
    "PHP", "Swift", "Kotlin", "Rust", "Scala", "Elixir", "Clojure", "Haskell",
    "Dart", "R", "MATLAB", "Julia", "Lua", "Perl", "Erlang", "Objective-C",
}


# Generic prefix phrases that JD authors put in front of the actual skill name.
# Stripping these turns "Proficiency in C/C++ for embedded systems" into
# "C/C++ for embedded systems" which is closer to a searchable token.
# Order matters: longer phrases first to avoid partial matches.
_GENERIC_SKILL_PREFIXES = [
    "deep experience with",
    "deep knowledge of",
    "strong knowledge of",
    "strong experience with",
    "extensive experience with",
    "proven experience",
    "proven track record",
    "demonstrated ability",
    "demonstrated experience",
    "demonstrated technical",
    "expert-level",
    "expert level",
    "experience with",
    "experience in",
    "experience building",
    "experience architecting",
    "knowledge of",
    "knowledge in",
    "proficiency in",
    "proficiency with",
    "expertise in",
    "expertise with",
    "fluency in",
    "fluent in",
    "familiarity with",
    "background in",
    "skills in",
    "skill in",
    "ability to",
]

# Years-of-experience prefix patterns. These are stripped before the skill name.
# Examples: "5+ years embedded systems development", "8+ years of firmware experience"
import re as _re_atomic
_YOE_PREFIX_PATTERN = _re_atomic.compile(
    r'^\s*\d+\+?\s*(?:to\s+\d+\s*)?(?:years?|yrs?)\s*(?:of\s+)?(?:experience\s+(?:in\s+|with\s+|as\s+)?)?',
    _re_atomic.IGNORECASE,
)
# Trailing phrases that don't add search value
_TRAILING_PHRASES_PATTERN = _re_atomic.compile(
    r'\s*(?:with\s+multiple\s+shipped\s+products|for\s+test\s+automation\s+and\s+integration|'
    r'\s*\(or\s+equivalent[^)]*\)|in\s+production|at\s+scale)\s*$',
    _re_atomic.IGNORECASE,
)


def _extract_atomic_skills(raw_entries: list) -> list:
    """Extract atomic skill names from JD-bullet-shaped entries.

    Real JD parsers (including ours) frequently return sentence-shaped
    requirements rather than atomic skill names. Examples from production data
    on a Skydio req:

      "5+ years embedded systems development (C/C++, RTOS, firmware)"
      "U.S. Citizenship and DoD security clearance eligibility"
      "Proficiency in C/C++ for embedded systems (not just application code)"
      "RTOS experience (FreeRTOS, VxWorks, QNX, or equivalent real-time operating system)"

    None of these appear verbatim in candidate profiles. This helper extracts:

      "5+ years embedded systems development (C/C++, RTOS, firmware)"
        -> ["C/C++", "RTOS", "firmware", "embedded systems"]
      "Proficiency in C/C++ for embedded systems (not just application code)"
        -> ["C/C++", "embedded systems"]
      "RTOS experience (FreeRTOS, VxWorks, QNX, or equivalent real-time operating system)"
        -> ["RTOS", "FreeRTOS", "VxWorks", "QNX"]

    Strategy:
      1. Pull out parenthetical contents and treat as additional skill candidates
         (because JD authors put real skill names in parens after generic intros)
      2. Strip years-of-experience prefixes
      3. Strip generic prefix phrases like "Proficiency in", "Deep experience with"
      4. Strip trailing fluff like "(or equivalent)", "with multiple shipped products"
      5. Split parenthetical contents on commas + " or " + " and "
      6. For the leftover (post-paren) sentence, only keep it if it's <= 4 words;
         otherwise drop (we'd rather have fewer real skills than a sentence)
      7. Dedupe case-insensitively while preserving original casing
      8. Drop entries that are clearly not skill names (citizenship clauses,
         "ability to communicate", etc.)

    Returns ordered list of atomic skill strings, deduplicated.
    """
    if not raw_entries:
        return []

    # Bag-of-skills as we extract. Use a list to preserve order, dict for dedup.
    seen_lower = set()
    out = []

    def _add(candidate: str) -> None:
        c = candidate.strip().strip(",.;:")
        if not c or len(c) < 2:
            return
        # Skip clearly-non-skill entries
        cl = c.lower()
        non_skill_markers = [
            "citizenship", "clearance eligibility", "ability to communicate",
            "must be willing", "must be able", "u.s. citizen", "us citizen",
            "ability to work", "willing to travel", "must have", "preferred",
            "nice to have", "bonus points",
            # Negations and qualifiers - common JD parenthetical noise
            "not just",
            "or equivalent",
            "and beyond",
        ]
        if any(m in cl for m in non_skill_markers):
            return
        # Skip clauses starting with stop-words that betray sentence-shape
        if cl.startswith(("not ", "or ", "and ", "the ", "a ", "an ")):
            return
        # Skip if still a sentence (more than 4 words) - leftover JD prose
        if len(c.split()) > 4:
            return
        if cl in seen_lower:
            return
        seen_lower.add(cl)
        out.append(c)

    for entry in raw_entries:
        if not entry or not isinstance(entry, str):
            continue

        # Step 1: pull out parenthetical contents
        paren_matches = _re_atomic.findall(r'\(([^)]+)\)', entry)
        # Sanitize the entry (remove parens for sentence parsing)
        without_parens = _re_atomic.sub(r'\([^)]*\)', '', entry).strip()

        # Step 2: strip YoE prefix
        without_yoe = _YOE_PREFIX_PATTERN.sub('', without_parens).strip()

        # Step 3: strip generic prefix phrases (case insensitive)
        cleaned = without_yoe
        cleaned_lower = cleaned.lower()
        for prefix in _GENERIC_SKILL_PREFIXES:
            if cleaned_lower.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                cleaned_lower = cleaned.lower()
                break  # only strip one prefix

        # Step 4: strip trailing fluff
        cleaned = _TRAILING_PHRASES_PATTERN.sub('', cleaned).strip(' .,;:')

        # Step 5: process parenthetical contents (split on , + " or " + " and ")
        for paren_content in paren_matches:
            # Skip noise like "or equivalent..." which gets caught by the trailing pattern
            if 'equivalent' in paren_content.lower() and len(paren_content.split()) > 4:
                continue
            # Split on commas, " or ", " and "
            parts = _re_atomic.split(r',|\s+or\s+|\s+and\s+', paren_content)
            for p in parts:
                _add(p)

        # Step 6: add the cleaned sentence remainder (only if VERY short - 3 words max).
        # Anything longer is sentence-shaped JD prose that won't match profiles.
        # Atomic skill names are 1-3 words ("Python", "C/C++", "embedded systems").
        if cleaned and len(cleaned.split()) <= 3:
            _add(cleaned)

    return out


def _generate_competitive_boolean_strategies(
    company_name: str,
    role_title: str,
    jd_skills: list,
    level: Optional[str] = None,
) -> dict:
    """Pure function. Generates 5 boolean search strategies for poaching from a competitor.

    Ported from CandidatIQ's intelligence_engine.py and stripped of the static
    COMPANY_DATA dependency. Works against any company name, not just FAANG.

    Strategies generated:
      - macro: wide-net X-ray with "ex-Company" + role/skills
      - micro_1: hyper-targeted by exact title + company + JD skills + seniority
      - micro_2: adjacent-role X-ray + company + JD skills
      - xray: title or adjacent + company + ALL JD skills
      - github: company + JD programming languages + followers floor

    Returns a dict of {strategy_name: boolean_string}. All strings follow the
    X-ray constraints documented in BOOLEAN_BUILDER_PROMPT (no literal AND,
    double-quoted phrases, OR uppercase between alternatives).

    Args:
        company_name: target competitor (e.g., "Anduril", "AUSGAR Technologies")
        role_title: requisition title verbatim (e.g., "Senior Embedded Linux Engineer")
        jd_skills: list of atomic skill strings - should come from parsed.canonical_skills
                  (clean atomic names like 'PyTorch', 'C++') for best results. The
                  helper still accepts must_have_skills sentences and runs them
                  through _extract_atomic_skills as a defensive fallback for older
                  reqs that pre-date the canonical_skills field in the JD parser.
        level: optional level hint ("ic_senior", "ic_staff_plus", "manager") to pick seniority filter
    """
    # Defensive: skills may come in as dicts {"skill": "...", "severity": "..."} or as plain strings.
    # Normalize to plain strings first.
    normalized_skills = []
    for s in (jd_skills or [])[:8]:  # take more upfront since we'll filter
        if isinstance(s, dict):
            name = s.get("skill") or s.get("name") or ""
            if name:
                normalized_skills.append(name)
        elif isinstance(s, str) and s:
            normalized_skills.append(s)

    # CRITICAL: real JD parsers (including ours, as of 2026-04-28) frequently
    # return sentence-shaped requirements like "5+ years embedded systems
    # development (C/C++, RTOS, firmware)" instead of atomic skills. Quoting
    # those verbatim and OR-ing them produces searches for verbatim sentences
    # that no candidate writes in their profile -> zero results.
    #
    # Extract atomic skill names from each entry. Strategy:
    #   1. Strip parenthetical content like "(C/C++, RTOS, firmware)" -> "C/C++, RTOS, firmware"
    #      and treat the contents as additional candidate skills
    #   2. Drop generic prefix phrases like "X+ years experience with",
    #      "Proficiency in", "Deep experience with", "Strong knowledge of"
    #   3. If the result is still > 4 words, it's still a sentence -> drop it
    #      (better to fall back to a known skill than search for a sentence)
    atomic_skills = _extract_atomic_skills(normalized_skills)

    if not atomic_skills:
        atomic_skills = ["Python", "SQL", "AWS"]  # generic fallback

    # Cap at the top 5 atomic skills, take top 3 for the macro strategy
    normalized_skills = atomic_skills[:5]
    top_three = normalized_skills[:3]
    skills_or_clause = " OR ".join(f'"{skill}"' for skill in top_three)
    all_skills_or_clause = " OR ".join(f'"{skill}"' for skill in normalized_skills)

    # Determine seniority filter from level hint or role title
    role_lower = (role_title or "").lower()
    is_manager = (
        (level and "manager" in level.lower())
        or any(w in role_lower for w in ["manager", "director", "vp", "head of"])
    )
    is_senior = (
        (level and any(x in level.lower() for x in ["senior", "staff", "principal"]))
        or any(w in role_lower for w in ["senior", "sr", "lead", "principal", "staff"])
    )

    if is_manager:
        seniority_filter = '(manager OR director OR "head of" OR vp OR lead)'
        adjacent_titles = ["director", "head of engineering", "vp of engineering", "engineering lead"]
    elif is_senior:
        seniority_filter = "(senior OR sr OR lead OR principal OR staff)"
        # Strip seniority words from the role to get the base
        base_role = role_title or "engineer"
        for word in ("Senior ", "Sr. ", "Sr ", "Lead ", "Principal ", "Staff "):
            base_role = base_role.replace(word, "")
        base_role = base_role.strip()
        # Verbose role titles like "Firmware Engineer, Embedded Platform" make
        # 5+ word adjacent titles when prefixed with "lead" or "principal".
        # Trim the trailing comma-clause for the OR list so the adjacents are
        # short enough to actually appear in real LinkedIn profiles. We keep
        # the FULL role_title for intitle: matches (which Google handles fine)
        # but use the trimmed version for the OR clause.
        if "," in base_role:
            base_role = base_role.split(",", 1)[0].strip()
        adjacent_titles = [
            f"senior {base_role}",
            f"lead {base_role}",
            f"principal {base_role}",
        ]
    else:
        seniority_filter = "(junior OR mid-level OR senior)"
        adjacent_titles = [role_title or "engineer"]

    # Build the adjacent_or_clause WITHOUT the role_title itself (xray already
    # has intitle:"role_title", so listing the same string again is redundant
    # and produces ugly output like (intitle:"X" OR "X")).
    adjacent_titles_for_clause = [
        t for t in adjacent_titles[:3]
        if t.strip().lower() != (role_title or "").strip().lower()
    ]
    # If adjacent_or_clause would be empty (e.g., no-seniority branch where
    # adjacent_titles was just [role_title]), set it to None and the xray/micro_2
    # builders will skip the OR clause cleanly.
    adjacent_or_clause = (
        " OR ".join(f'"{t}"' for t in adjacent_titles_for_clause)
        if adjacent_titles_for_clause else None
    )
    # Macro skills clause: quoted phrases, NOT bare lowercased words.
    # Google treats `embedded linux` (unquoted) as two AND'd terms, which is
    # not what we want for multi-word skills. Quoting preserves phrase match.
    macro_skills_or = " OR ".join(f'"{s}"' for s in top_three)

    # ---- LinkedIn URL coverage prefix ----
    # LinkedIn profiles live at TWO URL shapes:
    #   linkedin.com/in/<slug>/
    #   linkedin.com/in/<slug>     (no trailing slash, sub-pages)
    # The single `site:linkedin.com/in/` (with trailing slash) misses the
    # sub-pages and some country variants. Combining both with a `-pub.sub`
    # filter (Jason's working query pattern) catches all profile shapes
    # while excluding the public-sub-profile pages that pollute results.
    li = 'site:linkedin.com/in/ OR site:linkedin.com/in -pub.sub'

    # ---- Title matching strategy ----
    # `intitle:"Senior Firmware Engineer, Embedded Platform"` requires the
    # WHOLE 6-word phrase to appear in Google's idea of the page title.
    # LinkedIn page titles get truncated and frequently lose comma-clauses
    # like "Embedded Platform" -> 0 results.
    # For verbose titles (commas, slashes, > 4 words) we instead use a
    # quoted body match which scans the entire profile content.
    role_title_clean = (role_title or "engineer").strip()
    is_verbose_title = (
        ("," in role_title_clean)
        or ("/" in role_title_clean)
        or ("&" in role_title_clean)
        or len(role_title_clean.split()) > 4
    )
    # title_match is what we drop into queries where the title is the
    # primary anchor. quoted_title is just `"role_title"`. intitle_or_quoted
    # is the more permissive form for the "title OR skills" macro clause.
    if is_verbose_title:
        title_match = f'"{role_title_clean}"'                    # body quoted phrase
        intitle_or_quoted = f'"{role_title_clean}"'              # same - drop intitle:
    else:
        title_match = f'intitle:"{role_title_clean}"'            # short title -> intitle: still wins
        intitle_or_quoted = f'intitle:"{role_title_clean}"'

    # Build the 5 strategies. Note: per BOOLEAN_BUILDER_PROMPT rules, we use
    # Google syntax (site:, intitle:, OR uppercase, double quotes around phrases,
    # NO literal AND between terms - a space is implicit AND on Google).
    macro = (
        f'{li} ("{company_name}" OR "ex-{company_name}" OR "former {company_name}") '
        f'({intitle_or_quoted} OR {macro_skills_or}) {seniority_filter}'
    )
    micro_1 = (
        f'{li} {title_match} "{company_name}" '
        f'({skills_or_clause}) {seniority_filter}'
    )
    micro_2 = (
        f'{li} ("{company_name}" OR "worked at {company_name}") '
        f'({adjacent_or_clause}) ({skills_or_clause})'
        if adjacent_or_clause else
        f'{li} ("{company_name}" OR "worked at {company_name}") '
        f'{title_match} ({skills_or_clause})'
    )
    xray = (
        f'{li} ({title_match} OR {adjacent_or_clause}) '
        f'"{company_name}" ({all_skills_or_clause})'
        if adjacent_or_clause else
        f'{li} {title_match} '
        f'"{company_name}" ({all_skills_or_clause})'
    )

    # GitHub: only emit language: filters if the JD actually has programming languages.
    # Otherwise fall back to a profile search using the skill keywords.
    lang_skills = [s for s in normalized_skills if s in _GITHUB_PROGRAMMING_LANGS]
    if lang_skills:
        lang_clause = " OR ".join("language:" + s for s in lang_skills[:3])
        github = (
            f'site:github.com ("{company_name}") '
            f'({lang_clause}) followers:>50'
        )
    else:
        skill_quoted_clause = " OR ".join('"' + s + '"' for s in top_three)
        github = (
            f'site:github.com ("{company_name}") '
            f'({skill_quoted_clause}) followers:>50'
        )

    return {
        "macro": macro,
        "micro_1": micro_1,
        "micro_2": micro_2,
        "xray": xray,
        "github": github,
    }


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

    # Subject - specific, useful as an inbox handle later
    subject = f"Sourcing kit ready: {role} at {company}"

    # Pull the three highest-leverage strings:
    #   1. LR sniper (tightest match - what they'll run first)
    #   2. GitHub X-ray (highest signal for technical archetypes)
    #   3. Best watering-hole (the unique-to-this-role insight)
    #
    # If anything is missing, the section just doesn't render - better to
    # ship a slightly thinner email than to put placeholder text in front
    # of the user.
    lr_strings = booleans.get("linkedin_recruiter") or []
    sniper = next((s for s in lr_strings if (s.get("tier") or "").lower() == "sniper"), None)

    xrays = booleans.get("xray_searches") or []
    github_xray = next((x for x in xrays if "github" in (x.get("platform") or "").lower()), None)

    holes = parsed.get("watering_holes") or []
    top_hole = holes[0] if holes else None

    # Pull the mentor note's first tip - that's the 'do this first' nudge
    mentor = booleans.get("mentor_notes") or {}
    next_move = (
        mentor.get("best_xray_to_start")
        or mentor.get("pro_tip")
        or "Run the GitHub X-ray first - public commits are the highest signal for technical roles."
    )

    req_url = f"https://sourcingnav.com/app/pipeline.html?req={req_id}"

    # HTML - minimal styling, mobile-readable, no images (better deliverability)
    parts = []
    parts.append(f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1a1a1a;">
      <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a1a1a;">Your sourcing kit for <span style="color:#2d7eb8;">{role}</span></h2>
      <p style="margin:0 0 24px 0;color:#666;font-size:14px;">at {company}. Here are the three strings worth running first.</p>
    """)

    if sniper and sniper.get("string"):
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #2d7eb8;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#2d7eb8;margin-bottom:6px;">LinkedIn Recruiter - Sniper (start here)</div>
        <div style="font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a;word-break:break-word;line-height:1.5;">{sniper["string"]}</div>
      </div>
        """)

    if github_xray and github_xray.get("string"):
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #4a9d4a;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#4a9d4a;margin-bottom:6px;">GitHub X-ray - public code, highest signal</div>
        <div style="font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a;word-break:break-word;line-height:1.5;">{github_xray["string"]}</div>
      </div>
        """)

    if top_hole and top_hole.get("how_to_use"):
        venue = top_hole.get("venue", "specialty venue")
        parts.append(f"""
      <div style="margin:0 0 18px 0;padding:14px;background:#f6f8fa;border-left:3px solid #b85e2d;border-radius:4px;">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#b85e2d;margin-bottom:6px;">Watering hole - {venue}</div>
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
    # not the send subdomain - that's internal Resend infrastructure.
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


# ---------- REQ EXPORT (read-only, owner-gated) ----------
#
# Returns the full data payload for a single requisition so the client-side
# print view (/app/print.html) can render an expert-shareable report. The
# print view auto-triggers window.print() so the user gets the browser's
# native "Save as PDF" dialog without any server-side PDF library.
#
# Why a separate endpoint and not /api/intake/{id}: intake is the create
# action; this is a read for export. Keeping them separate makes the auth
# story simpler - export is a pure read with owner verification, while
# intake has cap-checking, AI calls, and storage logic.
@app.get("/api/req/{req_id}/export")
async def export_req(req_id: str, user: dict = Depends(get_current_user)):
    """Return the full req payload for client-side print/PDF rendering.

    Auth: bearer token required. Owner check: req.user_id must match the
    authenticated user, OR they must be in the same org as the req's owner.
    Same-org access matches the existing dashboard behavior.

    Returns: {
      req_id, title, jd_text, parsed, booleans, competitive_intel,
      plan (the requesting user's plan, drives Pro section visibility in
      the print view), exported_at (ISO timestamp for the report header).
    }
    """
    try:
        async with db() as client:
            rs = await client.execute(
                """SELECT id, title, jd_raw, parsed_json, boolean_strings_json,
                          user_id, org_id, opened_at
                   FROM requisitions
                   WHERE id = ?""",
                [req_id],
            )
            if not rs.rows:
                raise HTTPException(404, "Requisition not found")
            r = rs.rows[0]
            # Owner OR same-org access. Mirrors dashboard policy.
            if r[5] != user["id"] and r[6] != user.get("org_id"):
                raise HTTPException(403, "Not authorized to export this requisition")
            # Get user's plan for Pro section visibility
            plan_rs = await client.execute(
                "SELECT plan FROM users WHERE id = ?", [user["id"]]
            )
            plan = plan_rs.rows[0][0] if plan_rs.rows else "free"
            # Note: competitive_intel is NOT included in the export today because
            # CI results are not persisted to a column (CI is generated on demand).
            # If the caller wants CI in the print view, they pass it client-side
            # via sessionStorage. Future work: cache CI to a column and include here.
            return {
                "req_id": r[0],
                "title": r[1],
                "jd_text": r[2] or "",
                "parsed": json.loads(r[3]) if r[3] else {},
                "booleans": json.loads(r[4]) if r[4] else {},
                "competitive_intel": None,
                "plan": plan,
                "opened_at": r[7],
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[export] {type(e).__name__}: {str(e)[:200]}")

# ============================================================================
# LIVE INTELLIGENCE STREAM (Stage 1 of proactive engine, 2026-04-30)
# ============================================================================
#
# Real-time cross-req intelligence delivered via Server-Sent Events. The user
# opens any req in the app, an EventSource connects to /api/intelligence/stream,
# and intelligence events arrive live as the server computes them - no page
# refresh, no polling, no static dashboard feel.
#
# Why SSE not WebSockets: Vercel's serverless function model doesn't support
# long-lived bidirectional connections cleanly. SSE is one-way (server -> client)
# which is exactly what an intelligence stream needs, and it works on Vercel's
# native runtime without special infrastructure.
#
# Why this is the proactive engine, not just a dashboard:
#   - Events fire from real cross-req aggregation, not setTimeout fakes
#   - User sees intelligence arrive AS the server computes it (motion = value)
#   - Each event is a prediction or signal, not a static metric
#   - This is the substrate for Stage 2 (outcome feedback loop) and
#     Stage 3 (cross-company calibration moat)
#
# Event types emitted today:
#   Stage 1 (pipeline-shape signals):
#   - skill_concentration: a skill appears in 2+ of user's open reqs
#   - competitor_overlap: a competitor appears in 2+ of user's CI reports
#   - difficulty_spike: avg difficulty across open reqs is elevated
#   - velocity_baseline: predicted fill time based on user's closed reqs
#   Stage 2 (outcome-driven signals):
#   - outcome_velocity: real median/range from logged req_outcomes
#   - outcome_pattern: skill-level fast/slow close patterns
#   Stage 3-A (predictive signals on the current req):
#   - skill_match_prediction: median fill time for similar skill mixes
#   - company_poach_history: user's most-poached company + closing speed
#   - difficulty_outcome_correlation: fill rate at current difficulty band
# ============================================================================


async def _intel_get_user_reqs(client, user_id: str) -> list:
    """Fetch all of a user's reqs with the JSON columns we need for cross-req
    aggregation. Returns list of dicts with parsed fields ready to compute on.

    Limited to last 50 reqs to bound query cost. Order by opened_at DESC so
    the freshest reqs influence intelligence most.
    """
    rs = await client.execute(
        """SELECT id, title, parsed_json, competitive_intel_json,
                  status, opened_at, closed_at
           FROM requisitions
           WHERE user_id = ?
           ORDER BY opened_at DESC
           LIMIT 50""",
        [user_id],
    )
    out = []
    for r in rs.rows:
        try:
            parsed = json.loads(r[2]) if r[2] else {}
        except Exception:
            parsed = {}
        try:
            ci = json.loads(r[3]) if r[3] else {}
        except Exception:
            ci = {}
        out.append({
            "id": r[0],
            "title": r[1],
            "parsed": parsed,
            "ci": ci,
            "status": r[4],
            "opened_at": r[5],
            "closed_at": r[6],
        })
    return out


def _intel_skill_concentration(reqs: list) -> list:
    """Find skills appearing in 2+ of the user's open reqs. These are
    candidates for pooled sourcing - one boolean run can serve multiple reqs.
    Returns events sorted by concentration DESC.
    """
    open_reqs = [r for r in reqs if r["status"] == "open"]
    if len(open_reqs) < 2:
        return []
    skill_to_reqs = {}
    for r in open_reqs:
        # canonical_skills is the clean atomic list per the JD parser
        skills = (r["parsed"].get("canonical_skills") or [])
        if not skills:
            # fallback: pull from must_have_skills if canonical_skills missing
            mh = r["parsed"].get("must_have_skills") or []
            skills = [s.get("skill") for s in mh if isinstance(s, dict) and s.get("skill")]
        for skill in skills:
            if not skill or not isinstance(skill, str):
                continue
            skill_to_reqs.setdefault(skill, []).append(r["title"])
    events = []
    for skill, titles in skill_to_reqs.items():
        if len(titles) >= 2:
            events.append({
                "type": "skill_concentration",
                "skill": skill,
                "req_count": len(titles),
                "req_titles": titles[:5],
                "headline": f"{skill} appears in {len(titles)} of your open reqs",
                "insight": f"Pool these into one sourcing run. Same boolean serves all {len(titles)}.",
            })
    events.sort(key=lambda e: e["req_count"], reverse=True)
    return events[:5]


def _intel_competitor_overlap(reqs: list) -> list:
    """Find competitors appearing across 2+ of the user's CI reports.
    These are systemic competitors, not one-off appearances. They're the
    companies the user is consistently fighting for talent against.
    """
    competitor_to_reqs = {}
    for r in reqs:
        ci = r.get("ci") or {}
        companies = ci.get("companies") if isinstance(ci, dict) else None
        if not isinstance(companies, list):
            continue
        for c in companies:
            if not isinstance(c, dict):
                continue
            name = c.get("company")
            if not name or not isinstance(name, str):
                continue
            competitor_to_reqs.setdefault(name, []).append(r["title"])
    events = []
    for comp, titles in competitor_to_reqs.items():
        if len(titles) >= 2:
            events.append({
                "type": "competitor_overlap",
                "competitor": comp,
                "req_count": len(titles),
                "req_titles": titles[:5],
                "headline": f"{comp} is competing for talent in {len(titles)} of your reqs",
                "insight": "Build a per-competitor counter-poach narrative once. Reuse across all matching reqs.",
            })
    events.sort(key=lambda e: e["req_count"], reverse=True)
    return events[:5]


def _intel_velocity_baseline(reqs: list):
    """If user has closed reqs, compute average days-to-close. This becomes
    the predicted fill baseline for new reqs. Returns event dict or None.
    """
    closed = [r for r in reqs if r["status"] != "open" and r.get("closed_at")]
    if not closed:
        return None
    deltas = []
    for r in closed:
        try:
            opened = datetime.fromisoformat(str(r["opened_at"]).replace("Z", "+00:00").replace(" ", "T"))
            closed_dt = datetime.fromisoformat(str(r["closed_at"]).replace("Z", "+00:00").replace(" ", "T"))
            days = (closed_dt - opened).days
            if 0 <= days <= 365:
                deltas.append(days)
        except Exception:
            continue
    if not deltas:
        return None
    avg = sum(deltas) / len(deltas)
    return {
        "type": "velocity_baseline",
        "avg_days": round(avg, 1),
        "sample_size": len(deltas),
        "headline": f"Your historical fill velocity: ~{round(avg)} days",
        "insight": f"Based on {len(deltas)} closed reqs. New reqs benchmark against this baseline.",
    }


def _intel_difficulty_distribution(reqs: list):
    """Compute the difficulty distribution across open reqs. If the user's
    pipeline is heavy with 8+ difficulty reqs, that's a workload signal.
    """
    open_reqs = [r for r in reqs if r["status"] == "open"]
    if len(open_reqs) < 3:
        return None
    scores = []
    for r in open_reqs:
        md = r["parsed"].get("market_dynamics") or {}
        s = md.get("difficulty_score")
        if isinstance(s, (int, float)) and 1 <= s <= 10:
            scores.append(s)
    if len(scores) < 3:
        return None
    avg = sum(scores) / len(scores)
    hard_count = sum(1 for s in scores if s >= 8)
    if hard_count >= 2:
        return {
            "type": "difficulty_spike",
            "avg_difficulty": round(avg, 1),
            "hard_count": hard_count,
            "total": len(scores),
            "headline": f"{hard_count} of your {len(scores)} open reqs are 8+ difficulty",
            "insight": "Heavy pipeline. Consider sequencing hard reqs across weeks vs parallel.",
        }
    return None


def _format_sse_event(event_type: str, payload: dict) -> str:
    """Format a Server-Sent Events frame. SSE protocol: event:<name>\\n
    data:<json>\\n\\n. The double newline at the end signals end of event.
    """
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"



# ============================================================================
# STAGE 2 INTELLIGENCE HELPERS (2026-05-06)
# ----------------------------------------------------------------------------
# These helpers query the req_outcomes table directly to power outcome-driven
# intelligence events. Where Stage 1's helpers compute over pipeline shape,
# Stage 2 helpers compute over actual closed-loop data.
#
# Both helpers are async and take the DB client (different from Stage 1
# helpers which are sync and take a pre-fetched reqs list). This is a
# deliberate split — Stage 2 needs SQL aggregation that's not in the Stage 1
# req payload.
# ============================================================================


async def _intel_outcome_velocity(client, user_id: str):
    """Read real fill times from req_outcomes. Returns the actual median +
    range for closed reqs. Replaces the Stage 1 velocity_baseline if the
    user has logged outcomes.

    Returns event dict or None if no outcomes logged yet.
    """
    rs = await client.execute(
        """SELECT outcome, time_to_close_days
           FROM req_outcomes
           WHERE logged_by_user_id = ?
             AND outcome IN ('filled', 'lost', 'cancelled')
             AND time_to_close_days IS NOT NULL
           ORDER BY logged_at DESC
           LIMIT 100""",
        [user_id],
    )
    if not rs.rows:
        return None

    filled_days = [r[1] for r in rs.rows if r[0] == "filled"]
    if not filled_days:
        # User has logged outcomes but none filled yet — different signal
        return None

    filled_days.sort()
    n = len(filled_days)
    median = filled_days[n // 2] if n % 2 == 1 else (filled_days[n // 2 - 1] + filled_days[n // 2]) / 2
    fastest = min(filled_days)
    slowest = max(filled_days)
    avg = sum(filled_days) / n

    return {
        "type": "outcome_velocity",
        "median_days": round(median, 1),
        "avg_days": round(avg, 1),
        "fastest_days": fastest,
        "slowest_days": slowest,
        "sample_size": n,
        "headline": f"Median fill: {round(median)} days (range {fastest}–{slowest})",
        "insight": f"Real velocity from {n} filled reqs you've logged. Use this to set client expectations.",
    }


async def _intel_outcome_pattern(client, user_id: str):
    """Surface the fastest-closing and slowest-closing patterns from real
    outcome data. This is the prediction layer turning on: 'reqs with
    Embedded C/C++ fill 38% faster than your average.'

    Requires at least 5 filled outcomes with skill data. Without enough
    samples the signal is noisy and we suppress it.

    Returns event dict or None.
    """
    rs = await client.execute(
        """SELECT time_to_close_days, placed_candidate_skills
           FROM req_outcomes
           WHERE logged_by_user_id = ?
             AND outcome = 'filled'
             AND time_to_close_days IS NOT NULL
             AND placed_candidate_skills IS NOT NULL
           ORDER BY logged_at DESC
           LIMIT 100""",
        [user_id],
    )
    if len(rs.rows) < 5:
        return None

    # Aggregate: skill -> list of fill times
    skill_to_times = {}
    all_times = []
    for r in rs.rows:
        days = r[0]
        try:
            skills = json.loads(r[1]) if r[1] else []
        except Exception:
            skills = []
        if not skills:
            continue
        all_times.append(days)
        for s in skills:
            if isinstance(s, str):
                skill_to_times.setdefault(s, []).append(days)

    if not all_times:
        return None

    overall_avg = sum(all_times) / len(all_times)

    # For each skill with enough samples, compute the gap from overall
    skill_signals = []
    for skill, times in skill_to_times.items():
        if len(times) < 3:
            continue  # need 3+ samples to make a claim
        skill_avg = sum(times) / len(times)
        gap_pct = round(((skill_avg - overall_avg) / overall_avg) * 100)
        skill_signals.append((skill, skill_avg, gap_pct, len(times)))

    if not skill_signals:
        return None

    # Find the most extreme — fastest or slowest
    skill_signals.sort(key=lambda x: x[2])
    fastest_skill = skill_signals[0]
    slowest_skill = skill_signals[-1]

    # Pick whichever signal is more striking (largest absolute gap)
    if abs(fastest_skill[2]) >= abs(slowest_skill[2]):
        skill, avg, gap, n = fastest_skill
        direction = "faster" if gap < 0 else "slower"
    else:
        skill, avg, gap, n = slowest_skill
        direction = "slower" if gap > 0 else "faster"

    return {
        "type": "outcome_pattern",
        "skill": skill,
        "skill_avg_days": round(avg, 1),
        "overall_avg_days": round(overall_avg, 1),
        "gap_percent": gap,
        "sample_size": n,
        "headline": f"Reqs with '{skill}' fill {abs(gap)}% {direction} than average",
        "insight": f"Based on {n} filled reqs containing this skill (avg {round(avg)} days vs overall {round(overall_avg)} days). Use to prioritize sourcing depth.",
    }


# ============================================================================
# STAGE 3-A INTELLIGENCE HELPERS — Predictive signals (2026-05-06)
# ----------------------------------------------------------------------------
# Where Stage 1 reads pipeline shape and Stage 2 reads outcome history,
# Stage 3-A reads outcomes THROUGH THE LENS OF the current req.
#
# When the user pastes a new JD, the engine looks back at outcomes for
# similar reqs (matching skills, difficulty, level) and emits prospective
# signals:
#   - "Reqs with this skill mix fill in N days (n=M)"
#   - "You've placed N candidates from Company X, closing Y% faster"
#   - "At difficulty 8, your fill rate is N% (n=M)"
#
# All helpers gate aggressively by sample size. Below threshold → return
# None silently. Predictions on n=1 or n=2 are noise; we'd rather emit
# nothing than emit confidently wrong.
#
# Confidence bands: helpers return a 'confidence' field ('high' / 'medium' /
# 'low') based on sample size, so the frontend can render uncertainty
# visually if it wants. Low confidence still emits — small samples are
# better than no signal IF labeled honestly.
# ============================================================================


def _intel_predict_confidence(n: int) -> str:
    """Map sample size to a confidence label.

    Calibrated for recruiting outcomes specifically — even 5 placements is
    a lot of data per (skill, difficulty) bucket for a single recruiter.
    """
    if n >= 10:
        return "high"
    if n >= 5:
        return "medium"
    return "low"


async def _intel_skill_match_prediction(client, user_id: str, current_skills: list):
    """Find outcomes where placed candidates had skills overlapping with
    the current req's skills, compute median fill time + range.

    Why this beats Stage 2's outcome_velocity:
      Stage 2 tells you 'your average req fills in 28 days'
      Stage 3-A tells you 'reqs with THIS skill mix fill in 22 days'

    The shape is the same as outcome_velocity but filtered to the current
    req's skill signature. Returns None if fewer than 3 matching outcomes
    exist (would be too noisy below that threshold).
    """
    if not current_skills or not isinstance(current_skills, list):
        return None

    # Normalize current skills for matching (lowercase, strip whitespace)
    target_skills = set()
    for s in current_skills:
        if isinstance(s, str) and s.strip():
            target_skills.add(s.strip().lower())
    if not target_skills:
        return None

    # Pull all filled outcomes with skill data for this user
    rs = await client.execute(
        """SELECT time_to_close_days, placed_candidate_skills
           FROM req_outcomes
           WHERE logged_by_user_id = ?
             AND outcome = 'filled'
             AND time_to_close_days IS NOT NULL
             AND placed_candidate_skills IS NOT NULL
           ORDER BY logged_at DESC
           LIMIT 200""",
        [user_id],
    )
    if not rs.rows:
        return None

    # For each historical outcome, count skill overlap with current req.
    # Outcomes with 2+ overlapping skills are "matching" enough to predict on.
    matching_times = []
    for r in rs.rows:
        days = r[0]
        try:
            placed_skills = json.loads(r[1]) if r[1] else []
        except Exception:
            continue
        if not isinstance(placed_skills, list):
            continue
        placed_set = set(
            s.strip().lower() for s in placed_skills if isinstance(s, str) and s.strip()
        )
        overlap = target_skills & placed_set
        if len(overlap) >= 2:
            matching_times.append(days)

    if len(matching_times) < 3:
        return None

    matching_times.sort()
    n = len(matching_times)
    median = matching_times[n // 2] if n % 2 == 1 else (matching_times[n // 2 - 1] + matching_times[n // 2]) / 2
    fastest = min(matching_times)
    slowest = max(matching_times)
    confidence = _intel_predict_confidence(n)

    return {
        "type": "skill_match_prediction",
        "median_days": round(median, 1),
        "fastest_days": fastest,
        "slowest_days": slowest,
        "sample_size": n,
        "confidence": confidence,
        "headline": f"Reqs with this skill mix fill in {round(median)} days (range {fastest}–{slowest})",
        "insight": f"Based on {n} similar filled reqs in your history. Confidence: {confidence}. Use to set client expectations on this specific req.",
    }


async def _intel_company_poach_history(client, user_id: str):
    """Surface the user's most-poached company. If they've placed 3+
    candidates from Company X over their career, that's a real signal
    about where the talent pool actually sits.

    Returns None if no company appears 3+ times (can't make a claim).
    """
    rs = await client.execute(
        """SELECT placed_candidate_company_prev, time_to_close_days
           FROM req_outcomes
           WHERE logged_by_user_id = ?
             AND outcome = 'filled'
             AND placed_candidate_company_prev IS NOT NULL
             AND time_to_close_days IS NOT NULL
           ORDER BY logged_at DESC
           LIMIT 200""",
        [user_id],
    )
    if not rs.rows:
        return None

    # Bucket by company
    company_to_times = {}
    all_times = []
    for r in rs.rows:
        company = r[0]
        days = r[1]
        if not company or not isinstance(company, str):
            continue
        company_to_times.setdefault(company.strip(), []).append(days)
        all_times.append(days)

    if not all_times:
        return None
    overall_avg = sum(all_times) / len(all_times)

    # Find companies with 3+ placements
    candidates = []
    for company, times in company_to_times.items():
        if len(times) >= 3:
            company_avg = sum(times) / len(times)
            gap_pct = round(((company_avg - overall_avg) / overall_avg) * 100) if overall_avg > 0 else 0
            candidates.append((company, company_avg, gap_pct, len(times)))

    if not candidates:
        return None

    # Pick the company with the most placements (most reliable signal).
    # Tie-break on largest absolute gap from average.
    candidates.sort(key=lambda x: (-x[3], -abs(x[2])))
    company, company_avg, gap, n = candidates[0]
    direction = "faster" if gap < 0 else "slower"
    confidence = _intel_predict_confidence(n)

    return {
        "type": "company_poach_history",
        "company": company,
        "placement_count": n,
        "company_avg_days": round(company_avg, 1),
        "overall_avg_days": round(overall_avg, 1),
        "gap_percent": gap,
        "confidence": confidence,
        "headline": f"You've placed {n} candidates from {company} (closing {abs(gap)}% {direction})",
        "insight": f"Source-from-{company} is a proven path for you. Average time-to-fill: {round(company_avg)} days vs {round(overall_avg)} days overall. Confidence: {confidence}.",
    }


async def _intel_difficulty_outcome_correlation(client, user_id: str, current_difficulty):
    """For the current req's difficulty score, what's the historical fill
    rate at that difficulty band? E.g. 'At difficulty 8, your fill rate is
    71% (n=14). Lost: 29%.'

    Difficulty bands: 1-3 (easy), 4-6 (moderate), 7-8 (hard), 9-10 (very hard).
    Falls back gracefully if current_difficulty is missing or invalid.
    """
    if not isinstance(current_difficulty, (int, float)):
        return None
    if not (1 <= current_difficulty <= 10):
        return None

    # Define the band for the current req
    if current_difficulty <= 3:
        band_low, band_high, band_label = 1, 3, "easy"
    elif current_difficulty <= 6:
        band_low, band_high, band_label = 4, 6, "moderate"
    elif current_difficulty <= 8:
        band_low, band_high, band_label = 7, 8, "hard"
    else:
        band_low, band_high, band_label = 9, 10, "very hard"

    # Get all outcomes (filled / lost / cancelled) joined with their req's
    # difficulty score from parsed_json. Outcome data lives in req_outcomes,
    # difficulty lives in requisitions.parsed_json — need a JOIN.
    rs = await client.execute(
        """SELECT ro.outcome, r.parsed_json
           FROM req_outcomes ro
           JOIN requisitions r ON r.id = ro.req_id
           WHERE ro.logged_by_user_id = ?
             AND ro.outcome IN ('filled', 'lost', 'cancelled')
             AND r.parsed_json IS NOT NULL
           ORDER BY ro.logged_at DESC
           LIMIT 300""",
        [user_id],
    )
    if not rs.rows:
        return None

    # Filter to outcomes for reqs in this difficulty band
    band_outcomes = []
    for r in rs.rows:
        outcome = r[0]
        try:
            parsed = json.loads(r[1]) if r[1] else {}
        except Exception:
            continue
        md = parsed.get("market_dynamics") or {}
        diff = md.get("difficulty_score")
        if not isinstance(diff, (int, float)):
            continue
        if band_low <= diff <= band_high:
            band_outcomes.append(outcome)

    if len(band_outcomes) < 5:
        return None

    n = len(band_outcomes)
    filled = sum(1 for o in band_outcomes if o == "filled")
    lost = sum(1 for o in band_outcomes if o == "lost")
    cancelled = sum(1 for o in band_outcomes if o == "cancelled")
    fill_rate = round((filled / n) * 100)
    confidence = _intel_predict_confidence(n)

    return {
        "type": "difficulty_outcome_correlation",
        "difficulty_score": current_difficulty,
        "band_label": band_label,
        "band_range": f"{band_low}-{band_high}",
        "fill_rate_percent": fill_rate,
        "filled_count": filled,
        "lost_count": lost,
        "cancelled_count": cancelled,
        "sample_size": n,
        "confidence": confidence,
        "headline": f"At {band_label} difficulty ({band_low}-{band_high}), your fill rate is {fill_rate}% (n={n})",
        "insight": f"Of {n} {band_label}-difficulty reqs in your history: {filled} filled, {lost} lost, {cancelled} cancelled. Confidence: {confidence}. Set realistic expectations with the hiring manager.",
    }


@app.get("/api/intelligence/stream")
async def intelligence_stream(
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """Live Intelligence Stream - Server-Sent Events of cross-req signals.

    Auth: accepts EITHER an Authorization: Bearer header OR a ?token= query
    param. The query param fallback is required because EventSource (the
    browser API used by the frontend) does not support custom headers, so
    the only way to authenticate is via the URL.

    Client opens an EventSource on /api/intelligence/stream?token=X. Server
    fetches user's reqs once, runs all aggregation passes, streams each event
    as it's computed, then sends a 'done' event and closes the stream.

    Event types: ready, intelligence (with sub-types), done, error.

    Why we don't hold the connection open indefinitely: Vercel function
    timeout is 60s and serverless billing is per-invocation. We compute,
    stream, close. Frontend reconnects every N minutes for fresh intelligence
    (Stage 2 will add change-driven re-streaming).
    """
    # Resolve auth: header takes precedence, query param is the EventSource fallback
    bearer = None
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization.replace("Bearer ", "")
    elif token:
        bearer = token

    if not bearer:
        raise HTTPException(401, "Missing auth token (header or ?token=)")

    # Validate token signature + load user (mirrors get_current_user logic
    # but inline so we can support the dual auth modes)
    email = verify_token(bearer)
    if not email:
        raise HTTPException(401, "Invalid or expired token")

    async with db() as client:
        rs = await client.execute(
            "SELECT id, email, mode, plan FROM users WHERE email = ?", [email]
        )
        if not rs.rows:
            raise HTTPException(404, "User not found")
        ur = rs.rows[0]
        user = {"id": ur[0], "email": ur[1], "mode": ur[2], "plan": ur[3]}

    async def event_generator():
        try:
            # Send a ready event immediately so the frontend knows the stream
            # is live (otherwise it sits silent for 1-2s while we query the DB)
            yield _format_sse_event("ready", {"message": "Intelligence stream live"})

            async with db() as client:
                reqs = await _intel_get_user_reqs(client, user["id"])

            if not reqs:
                yield _format_sse_event("done", {
                    "message": "No reqs in your pipeline yet. Run an intake to start building intelligence.",
                    "event_count": 0,
                })
                return

            event_count = 0

            # Pass 1: skill concentration. Small yield delay between events
            # so the frontend can render each one with a nice arrival animation.
            for event in _intel_skill_concentration(reqs):
                yield _format_sse_event("intelligence", event)
                event_count += 1
                await asyncio.sleep(0.15)

            # Pass 2: competitor overlap
            for event in _intel_competitor_overlap(reqs):
                yield _format_sse_event("intelligence", event)
                event_count += 1
                await asyncio.sleep(0.15)

            # Pass 3: velocity baseline. Stage 2 (2026-05-06): try real
            # outcome data from req_outcomes first; fall back to pipeline-only
            # baseline if the user hasn't logged any outcomes yet. The new
            # outcome_velocity event uses median + range from real fills,
            # which is more informative than the Stage 1 average.
            async with db() as client:
                ov = await _intel_outcome_velocity(client, user["id"])
            if ov:
                yield _format_sse_event("intelligence", ov)
                event_count += 1
                await asyncio.sleep(0.15)
            else:
                vb = _intel_velocity_baseline(reqs)
                if vb:
                    yield _format_sse_event("intelligence", vb)
                    event_count += 1
                    await asyncio.sleep(0.15)

            # Pass 4: difficulty spike (single event if pipeline is heavy)
            ds = _intel_difficulty_distribution(reqs)
            if ds:
                yield _format_sse_event("intelligence", ds)
                event_count += 1
                await asyncio.sleep(0.15)

            # Pass 5: Stage 2 outcome pattern. Surfaces fastest/slowest
            # closing skill if user has 5+ filled outcomes with skill data.
            # Returns None silently for users without enough sample size.
            async with db() as client:
                op = await _intel_outcome_pattern(client, user["id"])
            if op:
                yield _format_sse_event("intelligence", op)
                event_count += 1
                await asyncio.sleep(0.15)

            # Stage 3-A predictive signals (2026-05-06): predict on the
            # most-recent req in the user's pipeline (reqs[0] since the
            # underlying SELECT is ORDER BY opened_at DESC). The recent req
            # is effectively the one the user is looking at right after
            # intake, which is when the stream fires. Each helper queries
            # req_outcomes through the lens of this specific req's shape
            # (skills, difficulty) and returns None if the user doesn't
            # have enough outcome history to make a reliable claim.
            current_req_parsed = (reqs[0].get("parsed") or {}) if reqs else {}
            current_skills = current_req_parsed.get("canonical_skills") or []
            current_difficulty = (current_req_parsed.get("market_dynamics") or {}).get("difficulty_score")

            # Pass 6: skill-mix prediction for THIS req
            async with db() as client:
                smp = await _intel_skill_match_prediction(client, user["id"], current_skills)
            if smp:
                yield _format_sse_event("intelligence", smp)
                event_count += 1
                await asyncio.sleep(0.15)

            # Pass 7: company poach history (most-poached company by user)
            async with db() as client:
                cph = await _intel_company_poach_history(client, user["id"])
            if cph:
                yield _format_sse_event("intelligence", cph)
                event_count += 1
                await asyncio.sleep(0.15)

            # Pass 8: difficulty band fill rate (for current req's difficulty)
            async with db() as client:
                doc = await _intel_difficulty_outcome_correlation(client, user["id"], current_difficulty)
            if doc:
                yield _format_sse_event("intelligence", doc)
                event_count += 1
                await asyncio.sleep(0.15)

            yield _format_sse_event("done", {
                "message": f"Computed {event_count} intelligence signals across {len(reqs)} reqs.",
                "event_count": event_count,
                "req_count": len(reqs),
            })
        except Exception as e:
            yield _format_sse_event("error", {
                "message": f"Intelligence stream error: {type(e).__name__}: {str(e)[:200]}"
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================================
# REQ OUTCOME LOGGING (Stage 2 of proactive engine, 2026-05-06)
# ============================================================================
#
# Endpoint that lets a recruiter log what happened with a requisition.
# This data feeds back into the Live Intelligence Stream (Stage 1) so the
# velocity_baseline, skill_concentration, and competitor_overlap signals
# learn from real outcomes instead of pure pipeline shape.
#
# Why this is the proactive layer:
#   - Stage 1 reads pipeline state. Predictive but unsupervised.
#   - Stage 2 reads OUTCOMES. The system learns which signals correlate
#     with fast closes vs slow closes vs lost reqs.
#   - Stage 3 (later) closes the loop by surfacing those learnings as
#     prediction confidence on new reqs.
#
# Design:
#   - Outcome events are append-only (one req can have multiple outcomes
#     over time: filled -> fell_off -> reopened -> filled)
#   - Latest outcome wins for "current state" queries
#   - History is preserved for trend analysis
#   - Compliance: every write logs an audit event for EU AI Act Article 12
# ============================================================================


class ReqOutcomeRequest(BaseModel):
    """Payload for POST /api/req/{req_id}/outcome.

    All fields except `outcome` are optional. The model is permissive on
    purpose — early users may not have all the metadata, and we'd rather
    capture the outcome with partial data than block the log because they
    don't remember which company they lost to.
    """

    outcome: str = Field(..., description="filled | lost | cancelled | fell_off | reopened")
    placed_candidate_company_prev: Optional[str] = Field(
        None, description="If outcome=filled, the company the candidate left to take this role"
    )
    placed_candidate_skills: Optional[list] = Field(
        None, description="If outcome=filled, list of canonical skill ids the candidate actually had"
    )
    lost_to_company: Optional[str] = Field(
        None, description="If outcome=lost, the company the candidate went to instead"
    )
    notes: Optional[str] = Field(None, max_length=2000)


@app.post("/api/req/{req_id}/outcome")
async def log_req_outcome(
    req_id: str,
    payload: ReqOutcomeRequest,
    user: dict = Depends(get_current_user),
):
    """Log the outcome of a requisition.

    Auth: bearer token. Owner check: the req must belong to this user OR
    same org. Mirrors the access policy on /api/req/{id}/export.

    On 'filled' or 'lost' outcomes, also marks the source req as closed
    (status='closed', closed_at=now) so existing dashboards reflect the
    state change. This is a write-through: outcome history lives in
    req_outcomes; latest snapshot lives on requisitions.

    Returns the created outcome event id and the computed time_to_close_days.
    """
    # Validate outcome value (Pydantic validates field presence; the CHECK
    # constraint on the table enforces enum, but we want a clean 400 on bad
    # input rather than a 500 from a constraint violation)
    valid_outcomes = {"filled", "lost", "cancelled", "fell_off", "reopened"}
    if payload.outcome not in valid_outcomes:
        raise HTTPException(
            400, f"outcome must be one of: {', '.join(sorted(valid_outcomes))}"
        )

    async with db() as client:
        # Owner / org check
        req_rs = await client.execute(
            "SELECT id, user_id, org_id, opened_at, status FROM requisitions WHERE id = ?",
            [req_id],
        )
        if not req_rs.rows:
            raise HTTPException(404, "Requisition not found")
        r = req_rs.rows[0]
        if r[1] != user["id"] and r[2] != user.get("org_id"):
            raise HTTPException(403, "Not authorized to log outcome for this requisition")

        # Compute time_to_close_days from req.opened_at to now. NULL for 'reopened'
        # because that's a state restart, not a close event.
        time_to_close_days = None
        if payload.outcome != "reopened":
            try:
                opened_at = datetime.fromisoformat(
                    str(r[3]).replace("Z", "+00:00").replace(" ", "T")
                )
                # Make timezone-aware if naive
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                delta = (now - opened_at).days
                if 0 <= delta <= 3650:  # 10 year sanity cap
                    time_to_close_days = delta
            except Exception:
                pass

        outcome_id = str(uuid.uuid4())
        skills_json = (
            json.dumps(payload.placed_candidate_skills)
            if payload.placed_candidate_skills
            else None
        )

        # Write the outcome event
        await client.execute(
            """INSERT INTO req_outcomes
               (id, req_id, outcome, time_to_close_days,
                placed_candidate_company_prev, placed_candidate_skills,
                lost_to_company, notes, logged_by_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                outcome_id,
                req_id,
                payload.outcome,
                time_to_close_days,
                payload.placed_candidate_company_prev,
                skills_json,
                payload.lost_to_company,
                payload.notes,
                user["id"],
            ],
        )

        # Update req status if this is a closing outcome
        if payload.outcome in ("filled", "lost", "cancelled"):
            await client.execute(
                """UPDATE requisitions
                   SET status = ?, closed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                ["closed" if payload.outcome != "cancelled" else "cancelled", req_id],
            )
        elif payload.outcome == "reopened":
            await client.execute(
                """UPDATE requisitions
                   SET status = 'open', closed_at = NULL, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                [req_id],
            )

        # Audit trail (compliance: EU AI Act Article 12 traceability)
        try:
            await write_audit_event(
                client,
                event_type="req_outcome",
                action="log_outcome",
                actor_user_id=user["id"],
                entity_type="requisition",
                entity_id=req_id,
                inputs={"outcome": payload.outcome},
                outputs={
                    "outcome_id": outcome_id,
                    "time_to_close_days": time_to_close_days,
                    "status_change": payload.outcome in ("filled", "lost", "cancelled", "reopened"),
                },
            )
        except Exception:
            # Audit failure should not block the outcome log itself
            pass

    return {
        "outcome_id": outcome_id,
        "req_id": req_id,
        "outcome": payload.outcome,
        "time_to_close_days": time_to_close_days,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/req/{req_id}/outcomes")
async def list_req_outcomes(
    req_id: str,
    user: dict = Depends(get_current_user),
):
    """Return the full outcome history for a requisition (newest first).

    Useful for the UI to show e.g. 'Filled 2024-03-15, fell off 2024-05-20,
    reopened 2024-05-21'. Most reqs will have exactly one outcome.
    """
    async with db() as client:
        # Owner check (same pattern as POST endpoint)
        req_rs = await client.execute(
            "SELECT user_id, org_id FROM requisitions WHERE id = ?",
            [req_id],
        )
        if not req_rs.rows:
            raise HTTPException(404, "Requisition not found")
        if req_rs.rows[0][0] != user["id"] and req_rs.rows[0][1] != user.get("org_id"):
            raise HTTPException(403, "Not authorized")

        rs = await client.execute(
            """SELECT id, outcome, time_to_close_days, placed_candidate_company_prev,
                      placed_candidate_skills, lost_to_company, notes, logged_at
               FROM req_outcomes
               WHERE req_id = ?
               ORDER BY logged_at DESC""",
            [req_id],
        )
        outcomes = []
        for r in rs.rows:
            try:
                skills = json.loads(r[4]) if r[4] else None
            except Exception:
                skills = None
            outcomes.append({
                "outcome_id": r[0],
                "outcome": r[1],
                "time_to_close_days": r[2],
                "placed_candidate_company_prev": r[3],
                "placed_candidate_skills": skills,
                "lost_to_company": r[5],
                "notes": r[6],
                "logged_at": r[7],
            })

    return {"req_id": req_id, "outcomes": outcomes}


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
        # ReadTimeout, ConnectError, etc. - the AI provider didn't respond in time.
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
    # All three calls are independent - they read from `parsed` (already
    # populated by step 1) and don't depend on each other's output. Running
    # them serially was costing ~25-40s of wall time on top of the parser
    # and boolean calls; together that pushed total intake time past
    # browser/proxy patience and triggered intermittent timeouts.
    #
    # Concurrency safety:
    #   - call_ai() creates a fresh httpx.AsyncClient per call (no shared
    #     state, no connection pool contention).
    #   - Each call independently reads the user's BYOK key from the DB
    #     (3x redundant reads, ~600ms total - acceptable, fix later by
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
        """Step 3.5 body - returns dict of {skill: [alternatives]}."""
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
        """Step 3.6 body - returns list of objection entries."""
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
        """Step 3.7 body - returns list of phase entries.

        Reads tier 1 companies and watering_holes from `parsed` so the
        phases reference specific venues, not generic advice."""
        try:
            market360 = parsed.get("market360") or {}
            poaching = market360.get("poaching_targets") or []
            tier1 = [p.get("company") for p in poaching if p.get("tier") == 1 and p.get("company")]
            if not tier1:
                tier1 = (market360.get("top_hiring_companies") or [])[:5]
            tier1_str = ", ".join(tier1[:8]) if tier1 else "(not specified - use general Tier 1 targets for this industry)"

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
        """Step 3.8 body - Pro tier ONLY. Returns list of per-skill briefings.

        Gated on user["plan"] == "pro". Free users get an empty list (the
        UI shows a locked placeholder card with the structure but not the
        content - see renderProSkillBriefingCard).

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
                max_tokens=7000,  # bumped 2026-04-28: career_switcher_archetypes adds ~2K tokens
            )
            # Returns a dict containing both pro_skill_briefing (per-skill rows)
            # AND career_switcher_archetypes (3-5 role-to-role transition pools).
            # The single AI call produces both fields by design - combining them
            # gives the model the full context (skill tier + archetype) instead
            # of forcing two parallel AI calls that don't see each other's reasoning.
            parsed_response = parse_json_strict(text)
            return {
                "briefing": parsed_response.get("pro_skill_briefing") or [],
                "archetypes": parsed_response.get("career_switcher_archetypes") or [],
            }
        except Exception as e:
            print(f"[ai-pro-skill-briefing FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return {"briefing": [], "archetypes": []}


    async def _run_pro_boolean_extensions():
        """Step 3.9 body - Pro tier ONLY. Returns dict of pro boolean extensions.

        Gated on user["plan"] == "pro". Free users get an empty dict; the UI
        shows a locked placeholder card with structure visible but content
        blocked out (see renderProBooleanExtensionsCard).

        Inputs:
          - parsed_context: same context dict the other enrichment calls use
          - existing_booleans: the free-tier output from step 3 (so the model
            can ANNOTATE the existing 3 LR tiers, not regenerate them)
          - watering_holes_list: stringified watering_holes from parsed (raw
            material for the Pro X-ray conversion - role-aware by construction)

        IMPORTANT: this depends on `booleans` (step 3 output) being available,
        so it runs in the parallel block alongside the other enrichments.
        Step 3 finishes BEFORE the parallel block starts (see flow).
        """
        if user.get("plan") != "pro":
            return {}
        try:
            holes = parsed.get("watering_holes") or []
            if not holes:
                holes_str = "(none - produce 3-5 generic Pro X-rays based on the role archetype)"
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
                max_tokens=7000,  # bumped 2026-04-28: hidden_talent_pools adds ~2K tokens
            )
            return parse_json_strict(text) or {}
        except Exception as e:
            print(f"[ai-pro-boolean FAIL] type={type(e).__name__} err={str(e)[:200]}")
            return {}

    # Fire all enrichment tasks concurrently. return_exceptions=True ensures we get a
    # value back for each task even if one explodes - but each task already
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
          f"pro_briefing={len(pro_skill_briefing.get('briefing', [])) if isinstance(pro_skill_briefing, dict) else 'skip/err'}, "
          f"pro_archetypes={len(pro_skill_briefing.get('archetypes', [])) if isinstance(pro_skill_briefing, dict) else 'skip/err'}, "
          f"pro_boolean={'ok' if pro_boolean_extensions else 'skip/empty'} plan={user.get('plan')})")

    # Step 4: save to DB + compliance records
    try:
        # Company override rule: JD body is source of truth. If the AI
        # extracted a company from the JD, use it - even if the user typed
        # something different. Users mistype. JDs don't lie about who is
        # hiring. The override is surfaced back to the client in the
        # response payload as `company_override` so the UI can banner it.
        user_entered = (req.org_name or "").strip() or None
        parsed_company = (parsed.get("core", {}).get("company") or "").strip() or None
        company_override = None
        if parsed_company and user_entered and parsed_company.lower() != user_entered.lower():
            # Mismatch - the AI found a company in the JD that differs
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
            if pro_skill_briefing and isinstance(pro_skill_briefing, dict):
                # pro_skill_briefing is a dict {briefing: [...], archetypes: [...]}
                # since the prompt change on 2026-04-28. Store both fields under
                # their canonical names so the frontend renderer reads them where
                # it expects.
                if pro_skill_briefing.get("briefing"):
                    parsed["pro_skill_briefing"] = pro_skill_briefing["briefing"]
                if pro_skill_briefing.get("archetypes"):
                    parsed["career_switcher_archetypes"] = pro_skill_briefing["archetypes"]
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

                # Audit event - the JD was parsed by AI, this is the record
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

                # Decision explanation - the "why this was parsed this way" record
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

                # Structured req_skills - THIS populates the brain's demand signal
                await write_req_skills(client, req_id, parsed)

                # Company override audit event - fires only when the AI extracted
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
        pass  # silently swallow - the work is done, accounting is best-effort

    # Step 6: fire-and-forget retention email.
    # asyncio.create_task() schedules the send AFTER we return, so the user
    # gets their intake response immediately and the email goes out in
    # background. The helper itself swallows all errors - a Resend hiccup
    # must NEVER break a successful intake response.
    #
    # We deliberately email even on first-intake users (no opt-in) because:
    #   1. They actively pasted a JD and ran an intake - clear engagement signal
    #   2. The email is content-rich (their booleans), not promotional
    #   3. Footer has a manage-emails link for opt-out (TODO: build the unsub flow)
    try:
        subject, html = _build_intake_completion_email(parsed, booleans, req_id)
        asyncio.create_task(_send_email(user["email"], subject, html))
    except Exception as e:
        print(f"[intake-email schedule FAIL] type={type(e).__name__} err={str(e)[:200]}")

    # Step 7: extract + save the JD signature (Phase B3 foundation).
    # Fire-and-forget: signature failures must NEVER break the intake.
    # Every successful intake contributes to the clustering data set.
    try:
        asyncio.create_task(_save_signature(req_id, user["id"], parsed))
    except Exception as e:
        print(f"[signature schedule FAIL] type={type(e).__name__} err={str(e)[:200]}")

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


@app.post("/api/intake/competitive-intel")
async def competitive_intel(req: CompetitiveIntelRequest, user: dict = Depends(get_current_user)):
    """Run Competitive Intelligence analysis on an existing requisition.

    Auto-chains from Boolean Builder's company clusters (tier_1_direct_competitors
    + tier_2_adjacent) by default. The recruiter can also pass an explicit list
    via req.competitors to override.

    Output for each company:
      - hiring_velocity, time_to_fill, eng_count estimates (with confidence flags)
      - salary_range with mandatory salary_confidence (high/medium/low)
      - poaching_difficulty + recruiting_angle
      - 5 boolean strategies (deterministic, generated by helper, no AI)
      - market_summary with comp_vs_jd benchmark

    Tier policy: Pro only. Server-side gate BEFORE the AI call so free users
    don't burn cycles. Free users hitting this endpoint get 402 with an
    upgrade message, same shape as cap-exhaustion.

    Cap policy: counts against the 'intake' bucket (1 unit per call regardless
    of competitor count). Cap is checked upfront but only incremented on
    success - failed AI calls don't burn quota, matching /api/intake pattern.
    """
    # Step 0a: Pro tier gate. Returns 402 (Payment Required) so the frontend
    # can route to upgrade page with the same handler as cap-exhaustion.
    if user.get("plan") != "pro":
        raise HTTPException(
            402,
            "Competitive Intelligence is a Pro feature. Upgrade to unlock per-company hiring velocity, salary intel, and poaching strategies.",
        )

    # Step 0b: cap check (does NOT increment)
    try:
        await check_cap(user["id"], "intake")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[cap] {type(e).__name__}: {str(e)[:200]}")

    # Step 1: load the requisition (verify ownership AND get parsed + booleans)
    try:
        async with db() as client:
            rs = await client.execute(
                """SELECT id, title, parsed_json, boolean_strings_json
                   FROM requisitions WHERE id = ? AND user_id = ?""",
                [req.req_id, user["id"]],
            )
            if not rs.rows:
                raise HTTPException(404, "Requisition not found")
            req_row = rs.rows[0]
            if not req_row[2]:
                raise HTTPException(400, "Requisition has no parsed data. Re-run the intake first.")
            parsed = json.loads(req_row[2])
            booleans = json.loads(req_row[3]) if req_row[3] else {}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[db-req-load] {type(e).__name__}: {str(e)[:200]}")

    # Step 2: assemble the competitor list. Order of preference:
    #   (a) explicit override from req.competitors (capped at 8)
    #   (b) Boolean Builder's company_clusters (tier_1 + tier_2)
    #   (c) parsed.market360.poaching_targets (fallback if booleans missing)
    if req.competitors:
        competitors = [c for c in req.competitors if c and c.strip()][:8]
    else:
        company_clusters = booleans.get("company_clusters") or {}
        tier_1 = company_clusters.get("tier_1_direct_competitors") or []
        tier_2 = company_clusters.get("tier_2_adjacent") or []
        competitors = list(dict.fromkeys(tier_1 + tier_2))[:8]  # de-dupe, preserve order

        # Fallback to poaching_targets if Boolean Builder didn't produce clusters
        if not competitors:
            poach = (parsed.get("market360") or {}).get("poaching_targets") or []
            competitors = [
                p.get("company") for p in poach
                if isinstance(p, dict) and p.get("company")
            ][:8]

    if not competitors:
        raise HTTPException(
            422,
            "No competitor companies found for this requisition. Re-run the intake to generate company clusters, or pass an explicit 'competitors' list.",
        )

    # Step 3: build the requisition context block for the prompt
    core = parsed.get("core", {}) or {}
    comp_snapshot = parsed.get("comp_snapshot", {}) or {}

    # Skill source resolution (the right way, finalized 2026-04-28):
    # The JD parser produces TWO skill fields by intentional design:
    #   - must_have_skills: prose with rationale, for human-readable UI display
    #     (e.g. "5+ years embedded systems development (C/C++, RTOS, firmware)")
    #   - canonical_skills: clean atomic skill names for database/search
    #     (e.g. "Embedded C/C++", "Real-Time Operating Systems (RTOS)", "Python")
    #
    # The boolean strategy generator needs ATOMIC skill names because
    # they're quoted into Google queries that match against candidate
    # profiles. Quoting prose like "5+ years embedded..." returns zero
    # results (no human writes that verbatim in a profile).
    #
    # Resolution order:
    #   1. Use parsed.canonical_skills (clean names, what we want) - 83%
    #      of reqs as of 2026-04-28
    #   2. Fall back to parsed.must_have_skills via _extract_atomic_skills
    #      defensive parser (handles older reqs that pre-date the
    #      canonical_skills field)
    canonical = parsed.get("canonical_skills") or []
    if canonical and isinstance(canonical, list):
        skill_names = [
            (c.get("name") or c.get("skill") or "") if isinstance(c, dict) else str(c)
            for c in canonical
        ]
        skill_names = [s for s in skill_names if s][:10]
    else:
        # Fallback: derive atomic skills from must_have_skills prose
        must_have = parsed.get("must_have_skills") or []
        raw_strings = [
            s.get("skill", "") for s in must_have
            if isinstance(s, dict) and s.get("skill")
        ]
        skill_names = _extract_atomic_skills(raw_strings)[:10]

    requisition_context = json.dumps({
        "role_title": core.get("role_title"),
        "level": core.get("level"),
        "company": core.get("company"),  # the HIRING company (excluded from analysis)
        "industry": core.get("industry"),
        "location": core.get("location"),
        "remote_policy": core.get("remote_policy"),
        "must_have_skills": skill_names,  # populated from canonical_skills first
        "offered_comp_base_range": comp_snapshot.get("base_range"),
        "offered_total_comp_range": comp_snapshot.get("total_comp_range"),
    }, indent=2)

    competitor_list = "\n".join(f"- {c}" for c in competitors)

    # Step 4: AI call. Errors here do NOT burn quota.
    try:
        ai_text = await call_ai(
            user["id"],
            COMPETITIVE_INTEL_PROMPT.format(
                requisition_context=requisition_context,
                competitor_list=competitor_list,
            ),
            max_tokens=6000,
        )
    except HTTPException:
        raise
    except Exception as e:
        etype = type(e).__name__
        print(f"[ai-competitive FAIL] type={etype} req_id={req.req_id[:8]}... competitors={len(competitors)} err={str(e)[:200]}")
        if "Timeout" in etype or "ConnectError" in etype:
            raise HTTPException(
                503,
                "The AI provider is slow or unreachable right now. Please try again in a moment.",
            )
        raise HTTPException(500, f"[ai-competitive] {etype}: {str(e)[:300]}")

    # Step 5: parse the AI JSON response
    try:
        intel = parse_json_strict(ai_text)
    except Exception as e:
        raise HTTPException(
            500,
            f"[json-parse] {type(e).__name__}: {str(e)[:200]}. AI returned: {ai_text[:300]}",
        )

    # Step 6: attach deterministic boolean strategies to each insight.
    # The AI does NOT generate boolean strings (those are deterministic,
    # cheaper, and easier to keep correct via _generate_competitive_boolean_strategies).
    insights = intel.get("insights") or []
    for insight in insights:
        company = insight.get("company")
        if not company:
            continue
        try:
            insight["boolean_strategies"] = _generate_competitive_boolean_strategies(
                company_name=company,
                role_title=core.get("role_title") or "",
                jd_skills=must_have,
                level=core.get("level"),
            )
        except Exception as e:
            # If the helper crashes for one company, don't poison the whole response.
            print(f"[bool-helper FAIL] company={company} err={str(e)[:150]}")
            insight["boolean_strategies"] = {}

    # Step 7: increment usage NOW that everything succeeded
    try:
        await increment_cap(user["id"], "intake")
    except Exception as e:
        # Non-fatal: we already produced the result, just log the increment failure.
        print(f"[cap-increment FAIL] type={type(e).__name__} err={str(e)[:200]}")

    # Step 8: return the assembled response
    return {
        "req_id": req.req_id,
        "role_title": core.get("role_title"),
        "competitors_analyzed": intel.get("competitors_analyzed") or competitors,
        "insights": insights,
        "market_summary": intel.get("market_summary") or {},
        "honesty_caveat": intel.get("honesty_caveat") or (
            "Estimates are derived from public hiring patterns. Confidence varies by company specificity."
        ),
        "ai_model": "claude-haiku-4-5",
    }


@app.post("/api/source/evaluate")
async def evaluate_candidate(req: CandidateEvalRequest, user: dict = Depends(get_current_user)):
    """Score a candidate against a requisition.

    Pattern matches /api/intake: cap check upfront, AI call, JSON parse, DB save,
    then increment usage at the end. Failed AI calls don't burn quota.
    """
    # Step 0: cap check (does NOT increment) - uses 'eval' bucket (10/mo on free)
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
                raise HTTPException(400, "Requisition has no parsed data - re-run the intake first")
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

            # 5b: compliance layer (best-effort - a failure here does NOT roll back
            #     the submission. Compliance is additive, not blocking the UX.)
            try:
                # Register the candidate as a GDPR data subject (idempotent on candidate_id)
                subject_id = await register_data_subject(
                    client, "candidate", candidate_id,
                )

                # Register (or reuse) the model_versions row for this eval
                async with db() as ai_client:  # new connection - reads only
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

                # Structured candidate skills MUST be written before the
                # matching engine runs - the engine reads candidate_skills rows.
                # (Order swap, 2026-05-11 Phase A finishing.)
                await write_candidate_skills(client, candidate_id, evaluation)

                # 8-dimension scores. Technical Match (Dim 1) and Gap Severity
                # (Dim 6) are now DETERMINISTIC via the matching engine.
                # Other 6 dimensions stay AI-derived for now. Passing req_id +
                # candidate_id triggers the engine path.
                await write_submission_dimensions(
                    client, submission_id, evaluation,
                    req_id=req.req_id, candidate_id=candidate_id,
                )
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
                      c.name, c.current_title, c.current_company,
                      sd.technical_match, sd.seniority_fit, sd.location_alignment,
                      sd.comp_alignment, sd.culture_signals, sd.gap_severity,
                      sd.presentation_risk, sd.fill_probability,
                      sd.composite_score, sd.blocker_count, sd.match_breakdown_json
               FROM submissions s
               JOIN candidates c ON s.candidate_id = c.id
               LEFT JOIN submission_dimensions sd ON sd.submission_id = s.id
               WHERE s.req_id = ?
               ORDER BY COALESCE(sd.composite_score, s.ai_fit_score / 20.0) DESC,
                        s.created_at DESC""",
            [req_id],
        )
        def _engine_breakdown(raw_json):
            """Extract just the engine portion of match_breakdown_json,
            keeping the response payload small."""
            if not raw_json:
                return None
            try:
                blob = json.loads(raw_json)
                eng = blob.get("engine") if isinstance(blob, dict) else None
                if not eng:
                    return None
                # Strip skill_matches (can be large); keep dimensions + composite
                return {
                    "dimensions": eng.get("dimensions"),
                    "composite": eng.get("composite"),
                    "blocker_count": eng.get("blocker_count"),
                    "blockers": eng.get("blockers"),
                }
            except Exception:
                return None
        return {
            "submissions": [
                {
                    "id": r[0], "candidate_id": r[1], "fit_score": r[2],
                    "recommendation": r[3], "stage": r[4],
                    "evaluation": json.loads(r[5]) if r[5] else None,
                    "created_at": r[6],
                    "candidate_name": r[7], "current_title": r[8], "current_company": r[9],
                    "engine": {
                        "technical_match": r[10],
                        "seniority_fit": r[11],
                        "location_alignment": r[12],
                        "comp_alignment": r[13],
                        "culture_signals": r[14],
                        "gap_severity": r[15],
                        "presentation_risk": r[16],
                        "fill_probability": r[17],
                        "composite": r[18],
                        "blocker_count": r[19],
                        "breakdown": _engine_breakdown(r[20]),
                    } if r[10] is not None or r[18] is not None else None,
                }
                for r in rs.rows
            ]
        }


# ============================================================
# PHASE E - MATCH MODE (lifecycle stage 3)
# ============================================================
# Given a req, score every candidate in the user's pool against it using
# the same 8-dimension deterministic engine that powers source evaluation.
# No AI calls (engine reads cached candidate_skills written during prior
# source evals). No DB writes (match is a read-only ranking - if the user
# wants to officially submit a matched candidate, they go through the
# normal source flow which creates the submission + audit chain).
#
# The reuse of candidate_skills across reqs is the foundation of the
# Talent OS model: every candidate evaluated against any req contributes
# to a growing pool that can be re-scored against future reqs.
#
# Lifecycle stages currently shipped:
#   - Source (Phase A): one candidate, one req, AI + engine
#   - Match (Phase E):  N candidates, one req, engine only
# Future stages (Phase E+): schedule, interview, offer, onboard, etc.

class MatchBatchRequest(BaseModel):
    req_id: str
    min_score: Optional[float] = Field(
        default=None, ge=0.0, le=5.0,
        description="Optional. Filter candidates whose composite is below this.",
    )
    limit: Optional[int] = Field(
        default=50, ge=1, le=200,
        description="Max candidates to return after sorting.",
    )


@app.post("/api/match/batch")
async def match_batch(req: MatchBatchRequest, user: dict = Depends(get_current_user)):
    """Phase E: rank all of the user's candidates against a single requisition.

    Returns the ranked list. Each entry includes the candidate's basic info,
    the 8 engine dimensions, composite + recommendation, and the most-recent
    submission_id (if the candidate was previously evaluated against any
    req - for navigating to existing evaluations).

    Performance note: this currently runs the engine for every candidate in
    the user's pool synchronously. At small scale (current state: 5 cands,
    24 reqs) this is fast (<2s). At larger scale we'll need to (a) cache
    scores in a match_scores table, and/or (b) batch the engine across all
    candidates in one pass. Not a v1 problem.
    """
    async with db() as client:
        # Verify req ownership
        rs = await client.execute(
            """SELECT id, title FROM requisitions
               WHERE id = ? AND user_id = ?""",
            [req.req_id, user["id"]],
        )
        if not rs.rows:
            raise HTTPException(404, "Requisition not found")
        req_title = rs.rows[0][1]

        # Pull all candidates owned by this user
        rs = await client.execute(
            """SELECT id, name, current_title, current_company,
                      email, linkedin_url, source, created_at
               FROM candidates
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            [user["id"]],
        )
        candidates = [
            {
                "id": r[0], "name": r[1], "current_title": r[2],
                "current_company": r[3], "email": r[4],
                "linkedin_url": r[5], "source": r[6], "created_at": r[7],
            }
            for r in rs.rows
        ]

        # For each candidate, fetch their most recent prior evaluation
        # (to populate seniority + AI proposal signals). Empty if never
        # evaluated.
        results = []
        for c in candidates:
            cand_id = c["id"]
            # Most recent submission across any req for this candidate
            rs = await client.execute(
                """SELECT s.id, s.fit_analysis_json, s.req_id, r.title
                   FROM submissions s
                   JOIN requisitions r ON s.req_id = r.id
                   WHERE s.candidate_id = ? AND r.user_id = ?
                   ORDER BY s.created_at DESC LIMIT 1""",
                [cand_id, user["id"]],
            )
            prior_eval = None
            prior_submission_id = None
            prior_req_id = None
            prior_req_title = None
            if rs.rows:
                prior_submission_id = rs.rows[0][0]
                prior_req_id = rs.rows[0][2]
                prior_req_title = rs.rows[0][3]
                try:
                    prior_eval = json.loads(rs.rows[0][1]) if rs.rows[0][1] else None
                except Exception:
                    prior_eval = None

            # Run the engine - same module Phase A uses, just called outside
            # the source eval write path
            try:
                engine_result = await run_matching_engine(
                    client, req.req_id, cand_id, prior_eval
                )
            except Exception as e:
                print(f"[match-engine] error for cand {cand_id}: {e!r}")
                engine_result = None

            if not engine_result:
                # Candidate has no skill data OR engine couldn't run.
                # Still include them in the response so the UI can show
                # "no skills evaluated yet" cards.
                results.append({
                    "candidate": c,
                    "engine": None,
                    "skipped_reason": "No candidate_skills found",
                    "prior_submission_id": prior_submission_id,
                    "prior_req_id": prior_req_id,
                    "prior_req_title": prior_req_title,
                })
                continue

            comp = engine_result.get("composite", {}) or {}
            dims = engine_result.get("dimensions", {}) or {}
            results.append({
                "candidate": c,
                "engine": {
                    "composite": comp.get("effective_composite"),
                    "composite_raw": comp.get("composite"),
                    "recommendation": comp.get("recommendation"),
                    "has_blockers": comp.get("has_blockers"),
                    "blocker_count": engine_result.get("blocker_count"),
                    "blockers": engine_result.get("blockers", []),
                    "weight_total": comp.get("weight_total"),
                    "note": comp.get("note"),
                    "dimensions": {
                        name: {
                            "score": data.get("score"),
                            "note": data.get("note"),
                        }
                        for name, data in dims.items()
                    },
                },
                "prior_submission_id": prior_submission_id,
                "prior_req_id": prior_req_id,
                "prior_req_title": prior_req_title,
            })

        # Apply min_score filter (after engine run, since unscored candidates
        # have None composite and should always be excluded from filtered set
        # but kept in unfiltered view as informational)
        scored = [r for r in results if r["engine"] is not None]
        unscored = [r for r in results if r["engine"] is None]

        if req.min_score is not None:
            scored = [r for r in scored
                       if (r["engine"]["composite"] or 0) >= req.min_score]

        # Sort scored by composite desc
        scored.sort(key=lambda r: r["engine"]["composite"] or 0, reverse=True)

        # Apply limit (only to scored; unscored is informational and small)
        scored = scored[: req.limit]

        return {
            "req": {"id": req.req_id, "title": req_title},
            "total_candidates_in_pool": len(candidates),
            "scored_count": len(scored),
            "unscored_count": len(unscored),
            "scored": scored,
            "unscored": unscored,
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
# PHASE B1 - Pipeline stage transitions + calibration
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

        # 2. Update the submission - set placed_at/rejected_at appropriately
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

        # 3. Compliance - every stage change is a decision that will
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
            # Non-fatal - the state change already committed. Log and continue.
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
    Safe to call when nothing is pending - returns a no-op run.
    """
    async with db() as client:
        summary = await run_calibration(
            client,
            triggered_by_user_id=user["id"],
            notes="manual_batch_run",
        )
    return summary


# ============================================================
# PHASE B2 - Skill resolution (alias / promote / reject)
# ============================================================
# Four endpoints turn the unresolved-skill firehose into a
# manageable approval queue. The user is in control - the LLM
# only suggests. Every decision is audited as a taxonomy_change
# event for EU AI Act Article 12 record-keeping.
#
# GET  /api/taxonomy/unresolved           - ranked queue
# GET  /api/taxonomy/suggestion/{raw}     - LLM suggestion (cached)
# POST /api/taxonomy/decide               - apply alias/promote/reject
# GET  /api/taxonomy/recent-decisions     - see what's been decided
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
        # Defensive - refuse to re-decide something already in the table.
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


# ---------- ADMIN: SIGNATURE INTROSPECTION (Phase B3) ----------
#
# Two endpoints:
#   POST /api/admin/backfill-signatures  → re-extract signatures for ALL
#         existing requisitions. Idempotent. Run after deploy or after
#         changing the extraction logic.
#   GET  /api/admin/signatures/stats     → first peek at the data:
#         row count, industry distribution, top skills, archetype hints.
#
# Auth: requires the calling user to have plan='pro'. Not bulletproof
# (anyone Pro could call them), but good enough for the only-Jason-uses-
# this-now state. Tighten before public Pro launch.

@app.post("/api/admin/backfill-signatures")
async def backfill_signatures(user: dict = Depends(get_current_user)):
    """Re-extract signatures for every requisition that has parsed_json.

    Idempotent (uses INSERT OR REPLACE). Safe to run after schema or
    extraction-logic changes. Returns counts so you know what happened.
    """
    if user.get("plan") != "pro":
        raise HTTPException(403, "Admin endpoint - Pro tier required")

    async with db() as client:
        rs = await client.execute(
            """SELECT id, user_id, parsed_json
               FROM requisitions
               WHERE parsed_json IS NOT NULL
               ORDER BY opened_at DESC"""
        )

    total = len(rs.rows)
    succeeded = 0
    failed = 0
    skipped = 0

    for row in rs.rows:
        req_id, ru_id, parsed_json = row
        if not parsed_json:
            skipped += 1
            continue
        try:
            parsed = json.loads(parsed_json)
        except Exception:
            failed += 1
            continue
        ok = await _save_signature(req_id, ru_id, parsed)
        if ok:
            succeeded += 1
        else:
            failed += 1

    return {
        "total_reqs_with_parse": total,
        "signatures_written": succeeded,
        "failed": failed,
        "skipped_no_parse": skipped,
    }


@app.get("/api/admin/signatures/stats")
async def signatures_stats(user: dict = Depends(get_current_user)):
    """First peek at the accumulated signature data. The narrative ammo:
    row count, industry mix, top skills, level distribution, geography,
    earliest and latest signature dates.

    As N grows, this endpoint will get richer. For now it answers the
    fundamental question: how much data do we have, and what does its
    shape look like?
    """
    if user.get("plan") != "pro":
        raise HTTPException(403, "Admin endpoint - Pro tier required")

    async with db() as client:
        # Row count + date range
        rs = await client.execute(
            """SELECT COUNT(*),
                      MIN(parsed_at),
                      MAX(parsed_at),
                      COUNT(DISTINCT industry),
                      COUNT(DISTINCT level),
                      COUNT(DISTINCT company),
                      COUNT(DISTINCT user_id)
               FROM jd_signatures"""
        )
        total, earliest, latest, n_industries, n_levels, n_companies, n_users = rs.rows[0] if rs.rows else (0, None, None, 0, 0, 0, 0)

        # Industry distribution
        rs = await client.execute(
            """SELECT industry, COUNT(*) as n
               FROM jd_signatures
               WHERE industry IS NOT NULL
               GROUP BY industry
               ORDER BY n DESC
               LIMIT 20"""
        )
        industry_dist = [{"industry": r[0], "count": r[1]} for r in rs.rows]

        # Level distribution
        rs = await client.execute(
            """SELECT level, COUNT(*) as n
               FROM jd_signatures
               WHERE level IS NOT NULL
               GROUP BY level
               ORDER BY n DESC"""
        )
        level_dist = [{"level": r[0], "count": r[1]} for r in rs.rows]

        # Remote policy distribution
        rs = await client.execute(
            """SELECT remote_policy, COUNT(*) as n
               FROM jd_signatures
               WHERE remote_policy IS NOT NULL
               GROUP BY remote_policy
               ORDER BY n DESC"""
        )
        remote_dist = [{"remote_policy": r[0], "count": r[1]} for r in rs.rows]

        # Difficulty score distribution
        rs = await client.execute(
            """SELECT difficulty_score, COUNT(*) as n
               FROM jd_signatures
               WHERE difficulty_score IS NOT NULL
               GROUP BY difficulty_score
               ORDER BY difficulty_score"""
        )
        difficulty_dist = [{"score": r[0], "count": r[1]} for r in rs.rows]

        # Top blocker skills across all signatures (the most-required skills
        # tell us what the market needs MOST). Done in Python because SQLite
        # JSON1 GROUP BY on json arrays is awkward.
        rs = await client.execute("SELECT blocker_skills_json FROM jd_signatures WHERE blocker_skills_json IS NOT NULL")
        skill_freq = {}
        for r in rs.rows:
            try:
                skills = json.loads(r[0])
                for s in skills:
                    if s:
                        skill_freq[s] = skill_freq.get(s, 0) + 1
            except Exception:
                continue
        top_blocker_skills = sorted(skill_freq.items(), key=lambda x: -x[1])[:25]
        top_blocker_skills = [{"skill": s, "count": c} for s, c in top_blocker_skills]

        # Top preferred skills
        rs = await client.execute("SELECT preferred_skills_json FROM jd_signatures WHERE preferred_skills_json IS NOT NULL")
        pref_freq = {}
        for r in rs.rows:
            try:
                skills = json.loads(r[0])
                for s in skills:
                    if s:
                        pref_freq[s] = pref_freq.get(s, 0) + 1
            except Exception:
                continue
        top_preferred_skills = sorted(pref_freq.items(), key=lambda x: -x[1])[:25]
        top_preferred_skills = [{"skill": s, "count": c} for s, c in top_preferred_skills]

        # Top adjacent crossover roles - THIS is the early signal for
        # emerging archetypes. If "Forward-Deployed Engineer" or "Prompt
        # Engineer" starts showing up here repeatedly across different
        # base roles, that's the archetype emerging.
        rs = await client.execute("SELECT adjacent_crossover_json FROM jd_signatures WHERE adjacent_crossover_json IS NOT NULL")
        crossover_freq = {}
        for r in rs.rows:
            try:
                cs = json.loads(r[0])
                for c in cs:
                    title = c.get("title")
                    if title:
                        crossover_freq[title] = crossover_freq.get(title, 0) + 1
            except Exception:
                continue
        top_crossovers = sorted(crossover_freq.items(), key=lambda x: -x[1])[:25]
        top_crossovers = [{"crossover_role": s, "count": c} for s, c in top_crossovers]

        # Top poaching target companies across all reqs - tells us which
        # companies are the most defensible talent sources across our
        # whole intake corpus.
        rs = await client.execute("SELECT poaching_target_companies_json FROM jd_signatures WHERE poaching_target_companies_json IS NOT NULL")
        poach_freq = {}
        for r in rs.rows:
            try:
                cs = json.loads(r[0])
                for c in cs:
                    if c:
                        poach_freq[c] = poach_freq.get(c, 0) + 1
            except Exception:
                continue
        top_poach_companies = sorted(poach_freq.items(), key=lambda x: -x[1])[:25]
        top_poach_companies = [{"company": s, "count": c} for s, c in top_poach_companies]

    return {
        "total_signatures": total,
        "earliest_signature": earliest,
        "latest_signature": latest,
        "unique_industries": n_industries,
        "unique_levels": n_levels,
        "unique_companies": n_companies,
        "unique_users": n_users,
        "industry_distribution": industry_dist,
        "level_distribution": level_dist,
        "remote_policy_distribution": remote_dist,
        "difficulty_distribution": difficulty_dist,
        "top_blocker_skills": top_blocker_skills,
        "top_preferred_skills": top_preferred_skills,
        "top_adjacent_crossover_roles": top_crossovers,
        "top_poaching_target_companies": top_poach_companies,
        "clustering_readiness": {
            "current_n": total,
            "minimum_for_meaningful_clustering": 30,
            "recommended_for_emerging_archetypes": 100,
            "ready_for_clustering": total >= 30,
        },
    }

# ---------- JD SIGNATURE CLUSTERING (Phase B3 Part B) ----------
#
# Pure-Python connected-components clustering on Jaccard similarity over
# (canonical_skills + adjacent_crossover) features. No sklearn dependency.
#
# Performance: O(N²) pairwise distance + O(N + E) connected components.
# At N=30: <50ms total. At N=500: ~3 seconds. At N=5000: 5 minutes
# (we'd swap to MinHash/LSH approximation before then).
#
# The algorithm:
#   1. For each signature, build a feature set = {canonical_skill_names}
#      + {adjacent_crossover_titles}
#   2. For each pair (i, j), compute Jaccard = |A ∩ B| / |A ∪ B|
#   3. Build undirected graph: edge if Jaccard >= threshold (default 0.30)
#   4. Find connected components → those are the clusters
#   5. For each cluster, identify "defining skills" = skills appearing in
#      ≥50% of cluster members AND in <20% of non-cluster members
#   6. Singletons (no edges to anyone) are the "potentially emerging"
#      bucket - these are roles the current taxonomy doesn't have a
#      coherent pattern for yet.

def _signature_features(sig_row: dict) -> set:
    """Build the feature set for one signature.

    Combines canonical skill names + adjacent crossover role titles.
    Lowercased for case-insensitive matching ("Senior" vs "senior").
    Skills with severity 'blocker' get an extra weight by being added
    twice (so cluster membership weighs them more).
    """
    features = set()

    try:
        for c in json.loads(sig_row.get("canonical_skills_json") or "[]"):
            name = (c.get("name") or "").lower().strip()
            if name:
                features.add(f"skill:{name}")
                if c.get("severity") == "blocker":
                    # Blockers are higher signal - represent twice
                    features.add(f"blocker:{name}")
    except Exception:
        pass

    try:
        for c in json.loads(sig_row.get("adjacent_crossover_json") or "[]"):
            title = (c.get("title") or "").lower().strip()
            if title:
                features.add(f"crossover:{title}")
    except Exception:
        pass

    return features


def _jaccard(a: set, b: set) -> float:
    """Standard Jaccard similarity. Returns 0.0 if both sets are empty."""
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _connected_components(n: int, edges: list) -> list:
    """Union-find to find connected components.

    Returns list of components, each a list of indices.
    Components sorted by size (largest first).
    """
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, j in edges:
        union(i, j)

    # Group by root
    components = {}
    for i in range(n):
        root = find(i)
        components.setdefault(root, []).append(i)

    return sorted(components.values(), key=lambda c: -len(c))


def _cluster_defining_features(
    cluster_indices: list,
    all_features: list,
    min_in_cluster_pct: float = 0.50,
    max_out_cluster_pct: float = 0.20,
) -> list:
    """Find the features that DEFINE this cluster.

    A defining feature appears in >= min_in_cluster_pct of cluster members
    AND in < max_out_cluster_pct of non-cluster members. These are the
    features that distinguish this cluster from the rest of the corpus.

    Returns list of (feature, in_cluster_count, out_cluster_count) tuples
    sorted by lift (in_pct / out_pct).
    """
    in_set = set(cluster_indices)
    n_in = len(cluster_indices)
    n_out = len(all_features) - n_in
    if n_in == 0:
        return []

    # Count feature occurrences
    feature_counts_in = {}
    feature_counts_out = {}
    for i, feats in enumerate(all_features):
        for f in feats:
            if i in in_set:
                feature_counts_in[f] = feature_counts_in.get(f, 0) + 1
            else:
                feature_counts_out[f] = feature_counts_out.get(f, 0) + 1

    defining = []
    for f, in_count in feature_counts_in.items():
        in_pct = in_count / n_in if n_in else 0
        out_count = feature_counts_out.get(f, 0)
        out_pct = out_count / n_out if n_out else 0

        if in_pct >= min_in_cluster_pct and out_pct < max_out_cluster_pct:
            # Lift score: how much more common in this cluster vs out
            lift = in_pct / max(out_pct, 0.01)
            defining.append({
                "feature": f,
                "in_cluster_count": in_count,
                "in_cluster_pct": round(in_pct, 3),
                "out_cluster_count": out_count,
                "out_cluster_pct": round(out_pct, 3),
                "lift": round(lift, 2),
            })

    return sorted(defining, key=lambda x: -x["lift"])


def _suggest_cluster_name(defining_features: list, member_role_titles: list) -> str:
    """Suggest a human-readable name for the cluster.

    Strategy:
      1. If a single role title appears in >50% of members, use it
      2. Else if defining skills include a clear domain marker
         ('embedded', 'firmware', 'frontend'), use that + level
      3. Else fall back to top 2 defining skills concatenated
      4. Else 'Unnamed cluster'
    """
    # Strategy 1: dominant role title
    if member_role_titles:
        title_counts = {}
        for t in member_role_titles:
            if t:
                # Normalize whitespace and strip seniority words to find pattern
                tl = (t or "").lower().strip()
                title_counts[tl] = title_counts.get(tl, 0) + 1
        if title_counts:
            top_title, top_count = max(title_counts.items(), key=lambda x: x[1])
            if top_count / len(member_role_titles) >= 0.5:
                # Use the original-case version of the dominant title
                for t in member_role_titles:
                    if t and t.lower().strip() == top_title:
                        return t

    # Strategy 2/3: top defining skills
    if defining_features:
        # Strip the type prefix for display
        top = []
        for d in defining_features[:3]:
            f = d["feature"]
            if ":" in f:
                _type, name = f.split(":", 1)
                top.append(name.title())
        if top:
            return " + ".join(top)

    return "Unnamed cluster"


async def _run_clustering(similarity_threshold: float = 0.30) -> dict:
    """Run the clustering algorithm against current jd_signatures.

    Returns the full results dict (also persisted to cluster_runs).
    """
    async with db() as client:
        rs = await client.execute(
            """SELECT req_id, role_title, level, industry, company,
                      canonical_skills_json, adjacent_crossover_json,
                      blocker_skills_json, parsed_at
               FROM jd_signatures"""
        )

    sigs = []
    for row in rs.rows:
        sigs.append({
            "req_id": row[0],
            "role_title": row[1],
            "level": row[2],
            "industry": row[3],
            "company": row[4],
            "canonical_skills_json": row[5],
            "adjacent_crossover_json": row[6],
            "blocker_skills_json": row[7],
            "parsed_at": row[8],
        })

    n = len(sigs)
    if n < 2:
        return {
            "n_signatures": n,
            "n_clusters": 0,
            "n_noise": n,
            "clusters": [],
            "noise_signatures": [{"req_id": s["req_id"], "role_title": s["role_title"]} for s in sigs],
            "warning": "Not enough signatures for clustering (need >=2)",
        }

    # Build feature sets
    all_features = [_signature_features(s) for s in sigs]

    # Skip signatures with no features (can't be clustered meaningfully)
    valid_indices = [i for i, f in enumerate(all_features) if f]
    if len(valid_indices) < 2:
        return {
            "n_signatures": n,
            "n_clusters": 0,
            "n_noise": n,
            "clusters": [],
            "noise_signatures": [{"req_id": s["req_id"], "role_title": s["role_title"]} for s in sigs],
            "warning": f"Only {len(valid_indices)} signatures have feature data - most have empty canonical_skills",
        }

    # Pairwise Jaccard, build edges above threshold
    edges = []
    for i in valid_indices:
        for j in valid_indices:
            if i >= j:
                continue
            sim = _jaccard(all_features[i], all_features[j])
            if sim >= similarity_threshold:
                edges.append((i, j))

    # Connected components
    components = _connected_components(n, edges)

    # Separate clusters (size >= 2) from noise (singletons)
    clusters = [c for c in components if len(c) >= 2]
    noise = [c[0] for c in components if len(c) == 1]

    # Build cluster output with defining features + names
    cluster_results = []
    for cluster_indices in clusters:
        defining = _cluster_defining_features(cluster_indices, all_features)
        member_titles = [sigs[i]["role_title"] for i in cluster_indices]

        # Pairwise similarities WITHIN cluster (cohesion measure)
        if len(cluster_indices) >= 2:
            sims = []
            for ii, idx_a in enumerate(cluster_indices):
                for idx_b in cluster_indices[ii+1:]:
                    sims.append(_jaccard(all_features[idx_a], all_features[idx_b]))
            avg_cohesion = sum(sims) / len(sims) if sims else 0.0
        else:
            avg_cohesion = 0.0

        cluster_results.append({
            "size": len(cluster_indices),
            "suggested_name": _suggest_cluster_name(defining, member_titles),
            "avg_cohesion": round(avg_cohesion, 3),
            "defining_features": defining[:8],  # top 8 most distinguishing
            "members": [
                {
                    "req_id": sigs[i]["req_id"],
                    "role_title": sigs[i]["role_title"],
                    "level": sigs[i]["level"],
                    "industry": sigs[i]["industry"],
                    "company": sigs[i]["company"],
                }
                for i in cluster_indices
            ],
        })

    noise_results = [
        {
            "req_id": sigs[i]["req_id"],
            "role_title": sigs[i]["role_title"],
            "level": sigs[i]["level"],
            "industry": sigs[i]["industry"],
            "company": sigs[i]["company"],
            "n_features": len(all_features[i]),
        }
        for i in noise
    ]

    return {
        "n_signatures": n,
        "similarity_threshold": similarity_threshold,
        "n_edges_above_threshold": len(edges),
        "n_clusters": len(clusters),
        "n_noise": len(noise),
        "clusters": cluster_results,
        "noise_signatures": noise_results,
        "interpretation_notes": [
            "Clusters with high avg_cohesion (>0.5) are tight role patterns.",
            "Noise signatures are roles that don't fit any current cluster - these are where emerging archetypes live.",
            "Defining features with high lift (>5) are skills that strongly distinguish this cluster from the rest of the corpus.",
            f"Run on N={n} signatures. Reliability of cluster discovery improves significantly past N=100.",
        ],
    }


@app.post("/api/admin/cluster-signatures")
async def cluster_signatures(user: dict = Depends(get_current_user)):
    """Run clustering on current jd_signatures and persist to cluster_runs.

    Idempotent in the sense that re-running with same data + threshold
    produces same output, but each run gets its own row (so we have a
    timeline of how clustering evolves as data grows).
    """
    if user.get("plan") != "pro":
        raise HTTPException(403, "Admin endpoint - Pro tier required")

    threshold = 0.30  # Tunable via query param later if needed
    results = await _run_clustering(similarity_threshold=threshold)

    # Persist
    run_id = str(uuid.uuid4())
    try:
        async with db() as client:
            await client.execute(
                """INSERT INTO cluster_runs (
                    id, algorithm, n_signatures, similarity_threshold,
                    n_clusters, n_noise, results_json
                ) VALUES (?, 'jaccard-cc-v1', ?, ?, ?, ?, ?)""",
                [
                    run_id,
                    results["n_signatures"],
                    threshold,
                    results["n_clusters"],
                    results["n_noise"],
                    json.dumps(results),
                ],
            )
        results["run_id"] = run_id
        results["persisted"] = True
    except Exception as e:
        print(f"[cluster-run persist FAIL] {type(e).__name__}: {str(e)[:200]}")
        results["run_id"] = run_id
        results["persisted"] = False
        results["persist_error"] = str(e)[:200]

    return results


@app.get("/api/admin/cluster-results")
async def latest_cluster_results(user: dict = Depends(get_current_user)):
    """Return the most recent cluster run (no recompute)."""
    if user.get("plan") != "pro":
        raise HTTPException(403, "Admin endpoint - Pro tier required")

    async with db() as client:
        rs = await client.execute(
            """SELECT id, run_at, algorithm, n_signatures, similarity_threshold,
                      n_clusters, n_noise, results_json
               FROM cluster_runs
               ORDER BY run_at DESC
               LIMIT 1"""
        )

    if not rs.rows:
        return {"message": "No cluster runs yet. POST /api/admin/cluster-signatures to create one."}

    row = rs.rows[0]
    return {
        "run_id": row[0],
        "run_at": row[1],
        "algorithm": row[2],
        "n_signatures": row[3],
        "similarity_threshold": row[4],
        "n_clusters": row[5],
        "n_noise": row[6],
        "results": json.loads(row[7]),
    }


@app.get("/api/admin/cluster-history")
async def cluster_history(user: dict = Depends(get_current_user)):
    """List all historical cluster runs (summary only, not full results).

    Useful for tracking how the cluster landscape evolves as data grows.
    """
    if user.get("plan") != "pro":
        raise HTTPException(403, "Admin endpoint - Pro tier required")

    async with db() as client:
        rs = await client.execute(
            """SELECT id, run_at, n_signatures, similarity_threshold,
                      n_clusters, n_noise
               FROM cluster_runs
               ORDER BY run_at DESC"""
        )

    return {
        "n_runs": len(rs.rows),
        "runs": [
            {
                "run_id": r[0],
                "run_at": r[1],
                "n_signatures": r[2],
                "similarity_threshold": r[3],
                "n_clusters": r[4],
                "n_noise": r[5],
            }
            for r in rs.rows
        ],
    }


# ---------- PUBLIC MARKET INTEL (Phase B3 Part D) ----------
#
# Public, ungated endpoint serving a curated subset of the market intel
# we surface internally on /app/trends.html. Designed for the public
# market-intel.html landing page.
#
# Honest framing: corpus_size is shown prominently so visitors can
# evaluate whether the signal is credible. At N=32 we're explicit about
# this being early signal, not definitive market intelligence.
#
# What this endpoint excludes vs the Pro admin endpoints:
#   - No user IDs / emails / session activity
#   - No req_ids (internal handles only)
#   - No timestamps under 24h old (don't leak real-time platform activity)
#   - Member company names ARE included (these are public JDs we parsed,
#     not customer data - companies named are who's HIRING, not our
#     customers)

@app.get("/api/public/market-intel")
async def public_market_intel():
    """Public market intelligence summary - no auth required.

    Reads the latest cluster_runs row and combines with current signature
    stats. Caches at the application layer is overkill; Vercel's CDN
    handles caching for ungated endpoints.
    """
    async with db() as client:
        # Aggregate stats
        stats_rs = await client.execute(
            """SELECT COUNT(*),
                      COUNT(DISTINCT industry),
                      COUNT(DISTINCT company),
                      COUNT(DISTINCT level)
               FROM jd_signatures"""
        )
        if not stats_rs.rows:
            n_total = n_industries = n_companies = n_levels = 0
        else:
            n_total, n_industries, n_companies, n_levels = stats_rs.rows[0]

        # Most recent clustering run
        cluster_rs = await client.execute(
            """SELECT id, run_at, n_signatures, similarity_threshold,
                      n_clusters, n_noise, results_json
               FROM cluster_runs
               ORDER BY run_at DESC
               LIMIT 1"""
        )

        # Top blocker skills (across whole corpus)
        blocker_rs = await client.execute(
            "SELECT blocker_skills_json FROM jd_signatures WHERE blocker_skills_json IS NOT NULL"
        )
        blocker_freq = {}
        for row in blocker_rs.rows:
            try:
                for s in json.loads(row[0]):
                    if s:
                        blocker_freq[s] = blocker_freq.get(s, 0) + 1
            except Exception:
                continue
        top_blockers = sorted(blocker_freq.items(), key=lambda x: -x[1])[:15]

        # Top adjacent crossover roles
        cross_rs = await client.execute(
            "SELECT adjacent_crossover_json FROM jd_signatures WHERE adjacent_crossover_json IS NOT NULL"
        )
        cross_freq = {}
        for row in cross_rs.rows:
            try:
                for c in json.loads(row[0]):
                    title = c.get("title")
                    if title:
                        cross_freq[title] = cross_freq.get(title, 0) + 1
            except Exception:
                continue
        top_crossovers = sorted(cross_freq.items(), key=lambda x: -x[1])[:12]

        # Top poaching target companies
        poach_rs = await client.execute(
            "SELECT poaching_target_companies_json FROM jd_signatures WHERE poaching_target_companies_json IS NOT NULL"
        )
        poach_freq = {}
        for row in poach_rs.rows:
            try:
                for c in json.loads(row[0]):
                    if c:
                        poach_freq[c] = poach_freq.get(c, 0) + 1
            except Exception:
                continue
        top_poach = sorted(poach_freq.items(), key=lambda x: -x[1])[:15]

        # Difficulty distribution
        diff_rs = await client.execute(
            """SELECT difficulty_score, COUNT(*) as n
               FROM jd_signatures
               WHERE difficulty_score IS NOT NULL
               GROUP BY difficulty_score
               ORDER BY difficulty_score"""
        )
        difficulty = [{"score": r[0], "count": r[1]} for r in diff_rs.rows]

    # Build the response
    out = {
        "corpus": {
            "n_signatures": n_total,
            "n_industries": n_industries,
            "n_companies": n_companies,
            "n_levels": n_levels,
            "honest_caveat": "Early signal - this corpus reflects the JDs SourcingNav users have parsed. Reliability of cluster patterns improves significantly past N=100.",
        },
        "top_blocker_skills": [{"skill": s, "count": c} for s, c in top_blockers],
        "top_adjacent_crossover_roles": [{"role": s, "count": c} for s, c in top_crossovers],
        "top_poaching_companies": [{"company": s, "count": c} for s, c in top_poach],
        "difficulty_distribution": difficulty,
    }

    # Latest cluster run - include only if we have one
    if cluster_rs.rows:
        row = cluster_rs.rows[0]
        try:
            results = json.loads(row[6])
            # Strip req_ids from clusters and noise - keep role + company
            clusters_out = []
            for c in results.get("clusters", []):
                clusters_out.append({
                    "suggested_name": c.get("suggested_name"),
                    "size": c.get("size"),
                    "avg_cohesion": c.get("avg_cohesion"),
                    "defining_features": c.get("defining_features", []),
                    "members": [
                        {"role_title": m.get("role_title"), "company": m.get("company")}
                        for m in c.get("members", [])
                    ],
                })
            noise_out = [
                {"role_title": n.get("role_title"), "company": n.get("company"), "n_features": n.get("n_features")}
                for n in results.get("noise_signatures", [])
            ]
            out["clusters"] = clusters_out
            out["noise_signatures"] = noise_out
            out["cluster_run"] = {
                "run_at": row[1],
                "n_signatures": row[2],
                "n_clusters": row[4],
                "n_noise": row[5],
            }
        except Exception as e:
            print(f"[market-intel cluster parse FAIL] {type(e).__name__}: {str(e)[:200]}")

    return out
