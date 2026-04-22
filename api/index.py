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
- Be specific. Generic strings like "engineer AND python" are useless.
"""


# ---------- MODELS ----------

class IntakeRequest(BaseModel):
    jd_text: str = Field(..., min_length=50)
    org_name: Optional[str] = None
    req_title: Optional[str] = None


class ByokRequest(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|together)$")
    api_key: str = Field(..., min_length=10)


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

    # Step 4: save to DB
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


@app.get("/api/reqs")
async def list_reqs(user: dict = Depends(get_current_user), status: Optional[str] = None):
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
