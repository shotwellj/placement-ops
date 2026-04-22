"""
Seed the taxonomy tables from taxonomy/skills.yml and taxonomy/competencies.yml.

Usage:
    export TURSO_URL='libsql://sourcingnav-prod-shotwellj.aws-us-west-2.turso.io'
    export TURSO_AUTH_TOKEN='<token from `turso db tokens create sourcingnav-prod`>'
    python3 scripts/seed_taxonomy.py

Idempotent: uses INSERT OR REPLACE so re-running refreshes the taxonomy.
Adjacencies are seeded symmetrically (A→B implies B→A).

Compliance context:
    Seeding the taxonomy creates the canonical reference for every AI decision.
    Every inserted skill/competency has a stable UUID used by req_skills,
    candidate_skills, and submission_dimensions.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS_YAML = ROOT / "taxonomy" / "skills.yml"
COMP_YAML = ROOT / "taxonomy" / "competencies.yml"


def turso_url() -> str:
    url = os.environ.get("TURSO_URL", "").strip()
    if not url:
        sys.exit("TURSO_URL not set. Run: export TURSO_URL='libsql://...'")
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/")


def turso_token() -> str:
    tok = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not tok:
        sys.exit("TURSO_AUTH_TOKEN not set. Run: turso db tokens create sourcingnav-prod")
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
    """Small sync client using the same Hrana v2/pipeline endpoint the app uses."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url
        self._token = token
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def execute(self, sql: str, params: list | None = None) -> dict:
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
        return first.get("response", {}).get("result", {})


# ----------------------------------------------------------------------
# Skills
# ----------------------------------------------------------------------

def load_skills() -> list[dict]:
    """Load skills.yml and return a flat list of skill dicts with category."""
    with SKILLS_YAML.open() as f:
        raw = yaml.safe_load(f)
    out: list[dict] = []
    for category, skills in raw.items():
        if not isinstance(skills, list):
            continue  # skip top-level non-list keys (comments, metadata)
        for s in skills:
            if not isinstance(s, dict) or "name" not in s:
                continue
            out.append({
                "name": s["name"],
                "category": category,
                "aliases": s.get("aliases") or [],
                "adjacent": s.get("adjacent") or [],
                "weight": s.get("weight", "medium"),
            })
    return out


def norm(s: str) -> str:
    """Normalize a skill name/alias for lookup: lowercase, collapse whitespace."""
    return " ".join(s.strip().lower().split())


def seed_skills(db: Turso, skills: list[dict]) -> dict[str, str]:
    """Insert skills and return a lookup: normalized name/alias -> skill UUID."""
    import json as _json
    lookup: dict[str, str] = {}
    # First pass: assign UUIDs keyed on canonical name
    canonical_uuid: dict[str, str] = {}
    for s in skills:
        canonical_uuid[s["name"]] = "sk_" + uuid.uuid4().hex[:16]

    # Insert rows
    inserted = 0
    for s in skills:
        sid = canonical_uuid[s["name"]]
        db.execute(
            """INSERT OR REPLACE INTO skills
               (id, canonical_name, category, aliases_json, weight)
               VALUES (?, ?, ?, ?, ?)""",
            [sid, s["name"], s["category"], _json.dumps(s["aliases"]), s["weight"]],
        )
        lookup[norm(s["name"])] = sid
        for a in s["aliases"]:
            lookup[norm(a)] = sid
        inserted += 1
    print(f"  skills: {inserted} inserted")
    return lookup


# ----------------------------------------------------------------------
# Adjacencies (symmetric per user decision)
# ----------------------------------------------------------------------

def seed_adjacencies(db: Turso, skills: list[dict], lookup: dict[str, str]) -> None:
    """Insert adjacencies. Symmetric: if A lists B as adjacent, insert A→B AND B→A.
    Unresolvable names are logged and skipped (not an error)."""
    pairs: set[tuple[str, str]] = set()
    unresolved: set[str] = set()

    for s in skills:
        a_id = lookup.get(norm(s["name"]))
        if not a_id:
            continue
        for adj in s["adjacent"]:
            b_id = lookup.get(norm(adj))
            if not b_id:
                unresolved.add(adj)
                continue
            if a_id == b_id:
                continue  # no self-loops
            # Store both directions for symmetric lookup
            pairs.add((a_id, b_id))
            pairs.add((b_id, a_id))


    inserted = 0
    for a_id, b_id in pairs:
        db.execute(
            """INSERT OR IGNORE INTO skill_adjacencies
               (id, skill_id, adjacent_id, weight, source)
               VALUES (?, ?, ?, ?, ?)""",
            ["adj_" + uuid.uuid4().hex[:16], a_id, b_id, 0.6, "taxonomy"],
        )
        inserted += 1
    print(f"  adjacencies: {inserted} inserted ({len(pairs)//2} unique pairs, symmetric)")
    if unresolved:
        print(f"  WARN: {len(unresolved)} adjacent names not found in taxonomy:")
        for name in sorted(unresolved):
            print(f"    - {name}")
        print("  (These are candidates for future additions to skills.yml)")


# ----------------------------------------------------------------------
# Competencies
# ----------------------------------------------------------------------

def load_competencies() -> list[dict]:
    with COMP_YAML.open() as f:
        raw = yaml.safe_load(f)
    out: list[dict] = []
    for category, items in raw.items():
        if not isinstance(items, list):
            continue
        for c in items:
            if not isinstance(c, dict) or "name" not in c:
                continue
            out.append({
                "name": c["name"],
                "category": category,
                "archetypes": c.get("archetypes") or [],
                "signals": c.get("signals") or [],
                "levels": c.get("levels") or {},
                "weight": c.get("weight", "medium"),
            })
    return out



def seed_competencies(db: Turso, comps: list[dict]) -> None:
    import json as _json
    inserted = 0
    for c in comps:
        cid = "cp_" + uuid.uuid4().hex[:16]
        db.execute(
            """INSERT OR REPLACE INTO competencies
               (id, canonical_name, category, archetypes_json, signals_json,
                levels_json, weight)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                cid, c["name"], c["category"],
                _json.dumps(c["archetypes"]),
                _json.dumps(c["signals"]),
                _json.dumps(c["levels"]),
                c["weight"],
            ],
        )
        inserted += 1
    print(f"  competencies: {inserted} inserted")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    print("Seeding taxonomy into sourcingnav-prod...")
    print(f"  skills.yml:       {SKILLS_YAML}")
    print(f"  competencies.yml: {COMP_YAML}")

    if not SKILLS_YAML.exists():
        sys.exit(f"ERROR: {SKILLS_YAML} not found")
    if not COMP_YAML.exists():
        sys.exit(f"ERROR: {COMP_YAML} not found")

    skills = load_skills()
    comps = load_competencies()
    print(f"  parsed: {len(skills)} skills, {len(comps)} competencies")

    db = Turso(turso_url(), turso_token())
    try:
        lookup = seed_skills(db, skills)
        seed_adjacencies(db, skills, lookup)
        seed_competencies(db, comps)
    finally:
        db.close()

    print("Done.")


if __name__ == "__main__":
    main()
