"""
Backfill req_skills + compliance records for existing requisitions.

Context:
    Before Phase A3, /api/intake saved parsed_json but didn't populate the
    new structured tables (req_skills, audit_events, decision_explanations,
    model_versions). This script walks every existing requisition and:

    1. Reads its existing parsed_json
    2. Writes req_skills rows (resolving skill names against the taxonomy)
    3. Writes a retroactive audit_event + decision_explanation
    4. Ensures the jd_parser model_version exists

    Idempotent in the usual case: if a req already has req_skills rows,
    we skip it. Run as many times as you want.

Usage:
    export TURSO_URL='libsql://sourcingnav-prod-shotwellj.aws-us-west-2.turso.io'
    export TURSO_AUTH_TOKEN='<token from turso db tokens create>'
    python3 scripts/backfill_req_skills.py

    Pass --dry-run to see what would happen without writing anything.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------
# Turso client (same pattern as seed_taxonomy.py — sync httpx)
# ----------------------------------------------------------------------

def turso_url() -> str:
    url = os.environ.get("TURSO_URL", "").strip()
    if not url:
        sys.exit("TURSO_URL not set")
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/")


def turso_token() -> str:
    tok = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not tok:
        sys.exit("TURSO_AUTH_TOKEN not set")
    return tok


def to_arg(p):
    if p is None:
        return {"type": "null"}
    if isinstance(p, bool):
        return {"type": "integer", "value": "1" if p else "0"}
    if isinstance(p, int):
        return {"type": "integer", "value": str(p)}
    if isinstance(p, float):
        return {"type": "float", "value": p}
    return {"type": "text", "value": str(p)}


class Turso:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url
        self._token = token
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def execute(self, sql: str, params: list | None = None) -> list[list]:
        """Returns list of rows (each row is a list of python values)."""
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [to_arg(p) for p in params]
        r = self._http.post(
            f"{self._base}/v2/pipeline",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        )
        r.raise_for_status()
        body = r.json()
        first = (body.get("results") or [{}])[0]
        if first.get("type") == "error":
            err = first.get("error", {})
            raise RuntimeError(f"Turso error: {err.get('message')}\nSQL: {sql}")
        result = first.get("response", {}).get("result", {})
        rows = []
        for raw_row in (result or {}).get("rows", []):
            row = []
            for cell in raw_row:
                t = cell.get("type")
                v = cell.get("value")
                if t == "null":
                    row.append(None)
                elif t == "integer":
                    row.append(int(v) if v is not None else None)
                elif t == "float":
                    row.append(float(v) if v is not None else None)
                else:
                    row.append(v)
            rows.append(row)
        return rows


# ----------------------------------------------------------------------
# Compliance helpers (sync, standalone copies of api/_compliance.py)
# We can't easily import the async versions because the whole API module
# pulls in FastAPI and env vars. Copy the logic we need.
# ----------------------------------------------------------------------

AUDIT_HMAC_KEY = os.environ.get("AUDIT_HMAC_KEY") or os.environ.get("MAGIC_LINK_SECRET") or ""


def hash_payload(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compute_hmac(seq: int, inputs_hash: str, outputs_hash: str, prev_hmac: str) -> str:
    if not AUDIT_HMAC_KEY:
        return "no_key:" + hash_payload(
            {"seq": seq, "in": inputs_hash, "out": outputs_hash, "prev": prev_hmac}
        )[:32]
    message = f"{seq}|{inputs_hash}|{outputs_hash}|{prev_hmac}".encode()
    return hmac.new(AUDIT_HMAC_KEY.encode(), message, hashlib.sha256).hexdigest()


def resolve_skill_id(db: Turso, raw_name: str, skill_cache: dict) -> Optional[str]:
    """Cached skill resolver. skill_cache maps normalized name -> skill_id or None."""
    if not raw_name or not raw_name.strip():
        return None
    norm = " ".join(raw_name.strip().lower().split())
    if norm in skill_cache:
        return skill_cache[norm]
    # Exact canonical
    rows = db.execute(
        "SELECT id FROM skills WHERE LOWER(canonical_name) = ?", [norm]
    )
    if rows:
        skill_cache[norm] = rows[0][0]
        return rows[0][0]
    # Alias lookup
    rows = db.execute(
        "SELECT id, aliases_json FROM skills WHERE aliases_json IS NOT NULL"
    )
    for row in rows:
        sid, aj = row[0], row[1]
        if not aj:
            continue
        try:
            aliases = json.loads(aj)
            for a in aliases:
                if " ".join(str(a).strip().lower().split()) == norm:
                    skill_cache[norm] = sid
                    return sid
        except Exception:
            continue
    skill_cache[norm] = None
    return None


def ensure_model_version(db: Turso, dry_run: bool) -> Optional[str]:
    """Create (or reuse) the jd_parser model_version for this backfill batch.
    Unlike the live endpoint we can't read the actual prompt text, so we use
    a stable backfill version tag."""
    version_tag = "jd_parser@backfill_v1"
    rows = db.execute(
        "SELECT id FROM model_versions WHERE version_tag = ?", [version_tag]
    )
    if rows:
        return rows[0][0]
    if dry_run:
        print(f"  [dry-run] would insert model_version {version_tag}")
        return None
    mv_id = "mv_" + uuid.uuid4().hex[:16]
    db.execute(
        """INSERT INTO model_versions
           (id, version_tag, prompt_name, prompt_hash, model_provider,
            model_name, active)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        [mv_id, version_tag, "jd_parser", "backfill_no_hash",
         "unknown", "unknown"],
    )
    print(f"  inserted model_version {version_tag}")
    return mv_id


def write_audit_event_sync(
    db: Turso,
    event_type: str,
    action: str,
    actor_user_id: str,
    entity_type: str,
    entity_id: str,
    inputs: dict,
    outputs: dict,
    model_version_id: Optional[str],
    dry_run: bool,
) -> Optional[str]:
    """Sync version of write_audit_event. Returns the audit_events.id."""
    rows = db.execute(
        "SELECT seq, hmac_chain FROM audit_events ORDER BY seq DESC LIMIT 1"
    )
    if rows:
        seq = (rows[0][0] or 0) + 1
        prev_hmac = rows[0][1]
    else:
        seq = 1
        prev_hmac = "genesis"
    inputs_hash = hash_payload(inputs)
    outputs_hash = hash_payload(outputs)
    hmac_val = compute_hmac(seq, inputs_hash, outputs_hash, prev_hmac)

    if dry_run:
        print(f"  [dry-run] would insert audit_event seq={seq}")
        return None

    ae_id = "ae_" + uuid.uuid4().hex[:16]
    db.execute(
        """INSERT INTO audit_events
           (id, seq, event_type, actor_user_id, entity_type, entity_id,
            action, inputs_hash, outputs_hash, model_version_id,
            hmac_chain, prev_hmac)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [ae_id, seq, event_type, actor_user_id, entity_type, entity_id,
         action, inputs_hash, outputs_hash, model_version_id,
         hmac_val, prev_hmac],
    )
    return ae_id


def backfill_req(
    db: Turso,
    req_row: list,
    mv_id: Optional[str],
    skill_cache: dict,
    dry_run: bool,
) -> tuple[int, int]:
    """Returns (req_skills_inserted, already_had_rows)."""
    req_id, user_id, title, parsed_json = req_row

    # Skip if we've already backfilled this one
    existing = db.execute(
        "SELECT COUNT(*) FROM req_skills WHERE req_id = ?", [req_id]
    )
    if existing and existing[0][0] and existing[0][0] > 0:
        return 0, existing[0][0]

    try:
        parsed = json.loads(parsed_json)
    except Exception as e:
        print(f"  SKIP {req_id[:8]} ({title[:40]}): bad JSON: {e}")
        return 0, 0

    # Extract skills the same way write_req_skills does
    candidates = []
    for s in (parsed.get("must_have_skills") or []):
        if isinstance(s, dict) and s.get("skill"):
            sev = s.get("severity", "preferred")
            importance = "blocker" if sev == "blocker" else "preferred"
            candidates.append((s["skill"], importance, s.get("rationale")))
    for s in (parsed.get("nice_to_have_skills") or []):
        if isinstance(s, dict) and s.get("skill"):
            candidates.append((s["skill"], "nice_to_have", s.get("rationale")))

    if not candidates:
        print(f"  {req_id[:8]} ({title[:40]}): no skills found in parsed_json")
        return 0, 0

    inserted = 0
    for skill_name, importance, rationale in candidates:
        skill_id = resolve_skill_id(db, skill_name, skill_cache)
        if dry_run:
            marker = "->" if skill_id else "??"
            print(f"    [dry] {importance:10} {marker} {skill_name}")
            inserted += 1
            continue
        row_id = "rs_" + uuid.uuid4().hex[:16]
        db.execute(
            """INSERT INTO req_skills
               (id, req_id, skill_id, raw_skill_text, importance, rationale)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [row_id, req_id, skill_id, skill_name, importance, rationale],
        )
        inserted += 1

    # Write retroactive audit event + decision explanation
    must_have = parsed.get("must_have_skills") or []
    top_factors = [
        {"factor": s.get("skill", ""), "severity": s.get("severity", "preferred"),
         "rationale": (s.get("rationale") or "")[:200]}
        for s in must_have[:5]
    ]
    plain_english = (parsed.get("executive_brief", {}).get("summary") or "")[:500]

    ae_id = write_audit_event_sync(
        db,
        event_type="ai_decision_backfill",
        action="parse_jd_backfill",
        actor_user_id=user_id,
        entity_type="requisition",
        entity_id=req_id,
        inputs={"backfill": True, "source": "existing_parsed_json"},
        outputs={
            "role_title": parsed.get("core", {}).get("role_title"),
            "must_have_count": len(must_have),
        },
        model_version_id=mv_id,
        dry_run=dry_run,
    )

    if ae_id and not dry_run:
        de_id = "de_" + uuid.uuid4().hex[:16]
        db.execute(
            """INSERT INTO decision_explanations
               (id, audit_event_id, decision_type, decision_outcome,
                top_factors_json, plain_english, human_review_status)
               VALUES (?, ?, ?, ?, ?, ?, 'not_requested')""",
            [de_id, ae_id, "jd_parse_backfill", "parsed",
             json.dumps(top_factors), plain_english],
        )

    return inserted, 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without writing")
    args = parser.parse_args()

    db = Turso(turso_url(), turso_token())
    print(f"Backfilling req_skills (dry_run={args.dry_run})...")
    print()

    skill_cache: dict = {}

    # Ensure model_version exists (one-time setup)
    mv_id = ensure_model_version(db, args.dry_run)

    # Fetch all reqs with parsed_json
    reqs = db.execute(
        """SELECT id, user_id, title, parsed_json
           FROM requisitions
           WHERE parsed_json IS NOT NULL AND parsed_json != ''
           ORDER BY opened_at ASC"""
    )
    print(f"Found {len(reqs)} requisitions with parsed_json")
    print()

    total_inserted = 0
    total_skipped = 0
    for req_row in reqs:
        req_id, user_id, title, parsed_json = req_row
        print(f"[{req_id[:8]}] {title[:60]}")
        inserted, already = backfill_req(db, req_row, mv_id, skill_cache, args.dry_run)
        if already:
            print(f"  already backfilled ({already} req_skills rows exist)")
            total_skipped += 1
        else:
            print(f"  {inserted} skill(s) {'(dry-run)' if args.dry_run else 'inserted'}")
            total_inserted += inserted
        print()

    print("=" * 60)
    print(f"Done. {total_inserted} total skills {'would be ' if args.dry_run else ''}inserted across {len(reqs) - total_skipped} reqs.")
    print(f"{total_skipped} reqs already had req_skills and were skipped.")
    if args.dry_run:
        print()
        print("This was a dry run. Rerun without --dry-run to actually write.")

    db.close()


if __name__ == "__main__":
    main()
