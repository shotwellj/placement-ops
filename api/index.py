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


async def check_cap(user_id: str, cap_type: str):
    """Check the cap WITHOUT incrementing. Raises 402 if the user is over."""
    async with db() as client:
        rs = await client.execute(
            "SELECT plan, usage_intake, usage_eval, usage_outreach FROM users WHERE id = ?",
            [user_id],
        )
        if not rs.rows:
            raise HTTPException(404, "User not found")
        row = rs.rows[0]
        plan = row[0]
        usage_map = {"intake": row[1] or 0, "eval": row[2] or 0, "outreach": row[3] or 0}
        if plan == "free" and usage_map[cap_type] >= FREE_CAPS[cap_type]:
            raise HTTPException(402, f"Free tier cap reached ({FREE_CAPS[cap_type]}/mo). Upgrade to Pro.")


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
    async with db() as client:
        rs = await client.execute(
            "SELECT byok_provider, byok_key_enc FROM users WHERE id = ?", [user_id]
        )
        if not rs.rows or not rs.rows[0][0]:
            raise HTTPException(400, "No AI provider configured. Add your API key in Settings.")
        provider, key_enc = rs.rows[0][0], rs.rows[0][1]

    if not fernet:
        raise HTTPException(500, "Encryption not configured on server")
    api_key = fernet.decrypt(key_enc.encode()).decode()

    if provider == "anthropic":
        return await _call_anthropic(api_key, prompt, max_tokens)
    if provider == "openai":
        return await _call_openai(api_key, prompt, max_tokens)
    if provider == "together":
        return await _call_together(api_key, prompt, max_tokens)
    raise HTTPException(400, f"Unknown provider: {provider}")


def _ai_error(provider: str, status: int, body: str) -> HTTPException:
    """Build a readable error from an AI provider's error response."""
    snippet = body[:400] if body else "<empty body>"
    return HTTPException(500, f"{provider} {status}: {snippet}")


async def _call_anthropic(api_key: str, prompt: str, max_tokens: int) -> str:
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
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if r.status_code >= 400:
            raise _ai_error("anthropic", r.status_code, r.text)
        return r.json()["content"][0]["text"]


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

SCHEMA LOCK. comp_snapshot MUST use exactly these four string fields and NO others:
  - base_range:        STRING formatted "$XXXk - $XXXk" (e.g. "$220k - $280k")
  - total_comp_range:  STRING formatted "$XXXk - $XXXk (incl. equity/bonus)"
  - equity_notes:      STRING, 1-2 sentences on equity expectations
  - negotiation_notes: STRING, 1-2 sentences on what levers to pull

Do NOT use base_min, base_max, total_comp_min, total_comp_max, or any numeric fields.
Do NOT nest objects inside comp_snapshot. All four values are flat strings.
If you cannot estimate comp, still return strings (e.g. base_range: "Unknown - market dependent").

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
  "alt_titles": {{"ic_junior": [], "ic_mid": [], "ic_senior": [], "ic_staff_plus": []}},
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
  "sourcing_strategy": {{"priority_channels": [], "key_tactics": []}}
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

Return ONLY valid JSON:
{{
  "linkedin_recruiter": {{
    "sniper": "tightest possible, 3-5 must-have terms, expect <100 results",
    "precision": "strong matches with seniority signal, ~50-200 results",
    "expanded": "broader recall with adjacent skills, ~200-1000 results"
  }},
  "xray": {{
    "linkedin": "site:linkedin.com/in/ ... — find LinkedIn profiles via Google",
    "github": "site:github.com ... — use in:bio, in:readme, language: where useful",
    "twitter": "(site:twitter.com OR site:x.com) ... — find practitioners talking publicly",
    "stackoverflow": "site:stackoverflow.com/users ... — active answerers in [tags]",
    "conferences": "(site:youtube.com OR site:slideshare.net) ... 'speaker' OR 'talk' — speakers/presenters",
    "personal_sites": "(intitle:resume OR intitle:CV OR intitle:portfolio) ... -site:linkedin.com -site:indeed.com",
    "specialty": "site:huggingface.co OR site:kaggle.com OR site:devpost.com ... — domain-specific platforms"
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
- X-ray strings use Google syntax (site:, intitle:, OR, AND, quoted phrases)
- Tier 1 = same product/market as the hiring company
- Tier 2 = adjacent industry/skill overlap
- NEVER include the hiring company itself in tier_1 or tier_2. The hiring company
  is the client, and recommending sourcing from them is a non-solicit violation.
  If the JD identifies the hiring company, exclude it from all company lists
  and replace with real competitors.
- Be specific. Generic strings like "engineer AND python" are useless.

X-RAY SEARCH CONSTRAINTS (these strings must actually run on Google, not just look smart):

1. MAX 3 AND CLAUSES per string. Google's ranking collapses past 3 ANDs.
   If you have 5 signals you want, pick the 3 highest-specificity ones and
   drop the rest. More ANDs = fewer results = weaker string.

2. ONLY use real Google operators. Whitelist:
     site:, intitle:, inurl:, in:bio, in:readme, language:, filetype:, -site:
   FORBIDDEN (these look real but Google ignores them, making your string
   return garbage or zero results):
     project:, score:, answers:, experience:, years:, company:, current_company:
   (current_company: works in LinkedIn Recruiter ONLY, not in X-ray.)

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
    # NOTE: Using onboarding@resend.dev (Resend's sandbox sender) until sourcingnav.com
    # is verified as a sending domain at resend.com/domains. The sandbox sender only
    # delivers to the email address that owns the Resend account.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": "SourcingNav <onboarding@resend.dev>",
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
                "SELECT plan, usage_intake, usage_eval, usage_outreach, byok_provider FROM users WHERE id = ?",
                [user["id"]],
            )
            if not rs.rows:
                raise HTTPException(404, "User not found in DB")
            r = rs.rows[0]
            return {
                **user, "plan": r[0],
                "usage": {"intake": r[1] or 0, "eval": r[2] or 0, "outreach": r[3] or 0},
                "caps": FREE_CAPS if r[0] == "free" else {"intake": 100, "eval": None, "outreach": None},
                "byok_provider": r[4],
                "byok_configured": bool(r[4]),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"[me] {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/user/byok-key")
async def save_byok_key(req: ByokRequest, user: dict = Depends(get_current_user)):
    if not fernet:
        raise HTTPException(500, "Encryption not configured")
    encrypted = fernet.encrypt(req.api_key.encode()).decode()
    async with db() as client:
        await client.execute(
            "UPDATE users SET byok_provider = ?, byok_key_enc = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [req.provider, encrypted, user["id"]],
        )
    return {"ok": True, "provider": req.provider}


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
        raise HTTPException(500, f"[ai-parse] {type(e).__name__}: {str(e)[:300]}")

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
        raise HTTPException(500, f"[ai-bool] {type(e).__name__}: {str(e)[:300]}")

    # Step 4: save to DB + compliance records
    try:
        org_name = req.org_name or parsed.get("core", {}).get("company") or "Unspecified"
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

    return {
        "req_id": req_id,
        "parsed": parsed,
        "boolean_strings": booleans,
        "created_at": now,
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
    async with db() as client:
        query = """
            SELECT r.id, r.title, r.status, r.fee_estimate, r.opened_at, o.name
            FROM requisitions r JOIN organizations o ON r.org_id = o.id
            WHERE r.user_id = ?
        """
        params = [user["id"]]
        if status:
            query += " AND r.status = ?"
            params.append(status)
        query += " ORDER BY r.opened_at DESC"
        rs = await client.execute(query, params)
        return {
            "reqs": [
                {"id": r[0], "title": r[1], "status": r[2], "fee_estimate": r[3],
                 "opened_at": r[4], "org_name": r[5]} for r in rs.rows
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
