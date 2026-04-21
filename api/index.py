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


async def check_and_increment_cap(user_id: str, cap_type: str):
    async with db() as client:
        rs = await client.execute(
            "SELECT plan, usage_intake, usage_eval, usage_outreach FROM users WHERE id = ?",
            [user_id],
        )
        if not rs.rows:
            raise HTTPException(404, "User not found")
        row = rs.rows[0]
        plan = row[0]
        usage_map = {"intake": row[1], "eval": row[2], "outreach": row[3]}
        if plan == "free" and usage_map[cap_type] >= FREE_CAPS[cap_type]:
            raise HTTPException(402, f"Free tier cap reached ({FREE_CAPS[cap_type]}/mo). Upgrade to Pro.")
        col = f"usage_{cap_type}"
        await client.execute(
            f"UPDATE users SET {col} = {col} + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [user_id],
        )


# ---------- AUTH ----------

class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyTokenRequest(BaseModel):
    token: str


def sign_token(email: str, exp_minutes: int = 15) -> str:
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)).timestamp())
    payload = f"{email}|{exp}"
    sig = hashlib.sha256(f"{payload}|{MAGIC_LINK_SECRET}".encode()).hexdigest()[:32]
    return f"{email}|{exp}|{sig}"


def verify_token(token: str) -> Optional[str]:
    try:
        email, exp_str, sig = token.split("|")
        exp = int(exp_str)
        if datetime.now(timezone.utc).timestamp() > exp:
            return None
        expected = hashlib.sha256(f"{email}|{exp}|{MAGIC_LINK_SECRET}".encode()).hexdigest()[:32]
        if sig != expected:
            return None
        return email
    except Exception:
        return None


async def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    email = verify_token(token)
    if not email:
        raise HTTPException(401, "Invalid or expired token")
    async with db() as client:
        rs = await client.execute("SELECT id, email, mode, plan FROM users WHERE email = ?", [email])
        if not rs.rows:
            raise HTTPException(404, "User not found")
        r = rs.rows[0]
        return {"id": r[0], "email": r[1], "mode": r[2], "plan": r[3]}


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
        r.raise_for_status()
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
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _call_together(api_key: str, prompt: str, max_tokens: int) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.together.xyz/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "Qwen/Qwen3-235B-A22B-Instruct-2507",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "Respond with valid JSON only. No markdown code fences."},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        r.raise_for_status()
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
  "must_have_skills": [{{"skill": "...", "rationale": "...", "severity": "blocker"}}],
  "nice_to_have_skills": [{{"skill": "...", "rationale": "..."}}],
  "transferable_skill_clusters": [{{"cluster_name": "...", "variants": [], "adjacent_skills": []}}],
  "alt_titles": {{"ic_junior": [], "ic_mid": [], "ic_senior": [], "ic_staff_plus": []}},
  "comp_snapshot": {{"base_range": "...", "total_comp_range": "...", "negotiation_notes": "..."}},
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

BOOLEAN_BUILDER_PROMPT = """You are an expert sourcer. Based on this parsed JD, generate Boolean search strings.

PARSED JD:
{parsed_jd}

Return ONLY valid JSON:
{{
  "linkedin_sniper": "...", "linkedin_precision": "...",
  "linkedin_expanded": "...", "linkedin_dragnet": "...",
  "google_xray": "site:linkedin.com/in/ ...",
  "github_xray": "site:github.com ...", "company_targeted": "...",
  "company_clusters": {{
    "tier_1_direct_competitors": [],
    "tier_2_adjacent": []
  }},
  "mentor_notes": {{"sourcing_strategy": "...", "keyword_reasoning": "..."}}
}}

Rules: No em dashes. Boolean strings must be valid LinkedIn Recruiter syntax.
Tier 1 = same product/market. Tier 2 = adjacent industry/skill overlap.
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
async def send_magic_link(req: MagicLinkRequest):
    if not RESEND_API_KEY:
        raise HTTPException(500, "Email service not configured")
    if not MAGIC_LINK_SECRET:
        raise HTTPException(500, "Magic link secret not configured")

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

    # Step 3: send email via Resend
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
            # Surface Resend error details so we can debug
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

    return {"ok": True, "message": "Check your email for the login link"}


@app.post("/api/auth/verify")
async def verify(req: VerifyTokenRequest):
    email = verify_token(req.token)
    if not email:
        raise HTTPException(401, "Invalid or expired token")
    async with db() as client:
        rs = await client.execute("SELECT id, mode, plan FROM users WHERE email = ?", [email])
        if not rs.rows:
            raise HTTPException(404, "User not found")
        return {
            "access_token": req.token,
            "user": {"id": rs.rows[0][0], "email": email, "mode": rs.rows[0][1], "plan": rs.rows[0][2]},
        }


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
    """Paste JD → parsed analysis + Boolean strings. Saves as a requisition."""
    # Step 0: rate limit
    try:
        await check_and_increment_cap(user["id"], "intake")
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
