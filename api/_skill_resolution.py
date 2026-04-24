"""
Phase B2: Skill resolution decisions.

When the JD parser extracts a skill that doesn't match the existing
taxonomy, it lands in req_skills with skill_id NULL and the original
raw_skill_text preserved. This module is the loop that turns those
unresolved entries into one of three things:

    alias   — the raw text refers to an existing skill, just by a
              different name. Add it as an alias and back-populate.
    promote — the raw text is a real skill not yet in the taxonomy.
              Create a new skill row + adjacencies + back-populate.
    reject  — the raw text isn't a skill (multi-clause requirement
              text, vague phrase, etc.). Mark it so it stops appearing
              in the queue.

All three actions write an audit_events row tagged
event_type='taxonomy_change' so a regulator can trace every change
to the taxonomy back to a specific user decision.

Design rules:
  - Pure manual approval. The system suggests, the user decides.
  - Suggestions are LLM-generated and CACHED in
    skill_promotion_suggestions to avoid repeated AI cost.
  - Decisions back-populate ALL existing req_skills + candidate_skills
    rows that share the normalized raw_text. Resolution rate jumps
    immediately, not just on future intakes.
  - Junk-pattern detection: the LLM is prompted to flag
    multi-clause/non-skill strings as 'reject', so the user's
    queue surfaces real skills first.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------

def normalize_raw_text(raw: str) -> str:
    """Lowercase + strip whitespace. Used as the dedup key everywhere."""
    if raw is None:
        return ""
    return raw.strip().lower()


# ----------------------------------------------------------------------
# Discovery — what unresolved skills should the user see?
# ----------------------------------------------------------------------

async def list_unresolved_candidates(
    client,
    min_count: int = 1,
    limit: int = 50,
) -> list[dict]:
    """Return ranked list of unresolved raw_skill_text strings.

    Aggregates req_skills + candidate_skills with skill_id IS NULL,
    excludes anything already decided in skill_resolution_decisions.
    Ranks by total occurrence count (req side weighted equally with
    candidate side; both are signal).

    Returns:
        [
            {
                "raw_text_normalized": "python",
                "raw_text_examples": ["Python", "python", "PYTHON"],
                "req_count": 6,
                "candidate_count": 0,
                "total_count": 6,
                "importances": ["blocker", "preferred"],
                "has_suggestion": True,
            },
            ...
        ]
    """
    # Pull all unresolved with grouping. Using two CTEs because Turso
    # SQLite handles them well and the alternative (UNION ALL with GROUP)
    # is harder to read.
    rs = await client.execute(
        """
        WITH req_unresolved AS (
            SELECT
                LOWER(TRIM(raw_skill_text)) AS norm,
                raw_skill_text AS example,
                importance,
                COUNT(*) AS cnt
            FROM req_skills
            WHERE skill_id IS NULL
            GROUP BY LOWER(TRIM(raw_skill_text)), raw_skill_text, importance
        ),
        cand_unresolved AS (
            SELECT
                LOWER(TRIM(raw_skill_text)) AS norm,
                raw_skill_text AS example,
                COUNT(*) AS cnt
            FROM candidate_skills
            WHERE skill_id IS NULL
            GROUP BY LOWER(TRIM(raw_skill_text)), raw_skill_text
        ),
        combined AS (
            SELECT norm,
                   GROUP_CONCAT(DISTINCT example) AS examples,
                   GROUP_CONCAT(DISTINCT importance) AS importances,
                   SUM(cnt) AS req_cnt,
                   0 AS cand_cnt
            FROM req_unresolved
            GROUP BY norm
            UNION ALL
            SELECT norm,
                   GROUP_CONCAT(DISTINCT example) AS examples,
                   NULL AS importances,
                   0 AS req_cnt,
                   SUM(cnt) AS cand_cnt
            FROM cand_unresolved
            GROUP BY norm
        )
        SELECT
            norm,
            GROUP_CONCAT(DISTINCT examples) AS all_examples,
            GROUP_CONCAT(DISTINCT importances) AS all_importances,
            SUM(req_cnt) AS total_req,
            SUM(cand_cnt) AS total_cand
        FROM combined
        GROUP BY norm
        HAVING SUM(req_cnt) + SUM(cand_cnt) >= ?
        ORDER BY (SUM(req_cnt) + SUM(cand_cnt)) DESC, norm
        """,
        [min_count],
    )

    # Filter out anything already decided
    decided_rs = await client.execute(
        "SELECT raw_text_normalized FROM skill_resolution_decisions"
    )
    decided = {r[0] for r in (decided_rs.rows or [])}

    # Check which have cached suggestions
    sugg_rs = await client.execute(
        "SELECT raw_text_normalized FROM skill_promotion_suggestions"
    )
    has_suggestion = {r[0] for r in (sugg_rs.rows or [])}

    out = []
    for row in (rs.rows or []):
        norm, examples, importances, total_req, total_cand = row
        if norm in decided:
            continue
        # Parse the comma-joined string. Filter blanks.
        ex_list = [e.strip() for e in (examples or "").split(",") if e and e.strip()]
        imp_list = [i.strip() for i in (importances or "").split(",") if i and i.strip()]
        out.append({
            "raw_text_normalized": norm,
            "raw_text_examples": list(dict.fromkeys(ex_list))[:5],  # dedup, cap
            "importances": list(dict.fromkeys(imp_list)),
            "req_count": int(total_req or 0),
            "candidate_count": int(total_cand or 0),
            "total_count": int((total_req or 0) + (total_cand or 0)),
            "has_suggestion": norm in has_suggestion,
        })
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------
# LLM-generated suggestion
# ----------------------------------------------------------------------

# Prompt for the suggestion LLM call. Designed to:
#   - return strict JSON
#   - flag junk multi-clause text as 'reject' explicitly
#   - find the BEST existing skill to alias to, if any
#   - suggest category + adjacencies for genuine new skills
SKILL_SUGGESTION_PROMPT = """You are a taxonomy curator for a recruiting AI.

Given a raw skill text extracted from a job description, decide ONE of:
  1. ALIAS — the text refers to a skill that ALREADY EXISTS in the taxonomy
     (just by a different name). Pick the best match.
  2. PROMOTE — the text is a real, distinct skill not yet in the taxonomy.
  3. REJECT — the text is not a skill at all. Reject if the text is:
     - A multi-clause requirement ("5+ years in X, Y, or Z")
     - A vague generality ("cross-functional collaboration", "clear communication")
     - A combined description ("end-to-end systems delivery in scale-critical environments")
     - A measurement ("3+ years experience")
     - Anything that combines multiple skills with "or" / "and" / commas

CONTEXT — current taxonomy categories:
{categories}

CONTEXT — existing skills (canonical_name :: category :: 3 closest aliases):
{existing_skills}

RAW SKILL TEXT TO CLASSIFY:
"{raw_text}"

Return STRICT JSON in this shape:

For ALIAS:
{{
  "decision": "alias",
  "alias_target_canonical": "Name of existing skill",
  "rationale": "1-sentence reason",
  "confidence": 0.0-1.0
}}

For PROMOTE:
{{
  "decision": "promote",
  "canonical_name": "Proper Capitalized Name",
  "category": "must match one of the listed categories",
  "aliases": ["alt name 1", "alt name 2"],
  "adjacent_canonicals": ["Existing Skill 1", "Existing Skill 2", "Existing Skill 3"],
  "weight": "high" or "medium" or "low",
  "rationale": "1-sentence reason",
  "confidence": 0.0-1.0
}}

For REJECT:
{{
  "decision": "reject",
  "rationale": "why this is not a single skill",
  "confidence": 0.0-1.0
}}

Return ONLY the JSON object, no markdown fencing or preamble.
"""


async def get_or_generate_suggestion(
    client,
    raw_text_normalized: str,
    call_ai_func,
    user_id: str,
    register_model_version_func,
) -> dict:
    """Return cached suggestion if present, else generate and cache.

    call_ai_func: the existing api.call_ai async function
    register_model_version_func: the existing api._compliance.register_model_version
    """
    # 1. Cache hit?
    cached = await client.execute(
        """SELECT suggestion_type, suggested_canonical, suggested_category,
                  suggested_adjacencies, suggested_alias_target, confidence,
                  llm_rationale
           FROM skill_promotion_suggestions
           WHERE raw_text_normalized = ?""",
        [raw_text_normalized],
    )
    if cached.rows and cached.rows[0]:
        r = cached.rows[0]
        return {
            "decision": r[0],
            "canonical_name": r[1],
            "category": r[2],
            "adjacencies_skill_ids": json.loads(r[3]) if r[3] else [],
            "alias_target_skill_id": r[4],
            "confidence": float(r[5] or 0.0),
            "rationale": r[6] or "",
            "from_cache": True,
        }

    # 2. Build prompt context
    cats_rs = await client.execute("SELECT DISTINCT category FROM skills ORDER BY category")
    categories = sorted({r[0] for r in (cats_rs.rows or []) if r and r[0]})

    skills_rs = await client.execute(
        "SELECT id, canonical_name, category, aliases_json FROM skills ORDER BY category, canonical_name"
    )
    skills_summary_lines = []
    skill_id_by_canonical = {}
    for sr in (skills_rs.rows or []):
        sid, canon, cat, aliases = sr
        skill_id_by_canonical[canon.lower()] = sid
        try:
            al = json.loads(aliases) if aliases else []
        except Exception:
            al = []
        al_short = al[:3]
        skills_summary_lines.append(f"  {canon} :: {cat} :: {', '.join(al_short) if al_short else '(no aliases)'}")
    existing_skills_text = "\n".join(skills_summary_lines)

    prompt = SKILL_SUGGESTION_PROMPT.format(
        categories=", ".join(categories),
        existing_skills=existing_skills_text,
        raw_text=raw_text_normalized,
    )

    # 3. Call LLM
    response_text = await call_ai_func(user_id, prompt, max_tokens=600)

    # 4. Parse — strip markdown fences if the model sneaks them in
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        suggestion = json.loads(cleaned)
    except json.JSONDecodeError:
        # Defensive — return a soft 'reject' so the UI can still render
        return {
            "decision": "reject",
            "rationale": f"LLM response was not valid JSON: {response_text[:200]}",
            "confidence": 0.0,
            "from_cache": False,
            "parse_error": True,
        }

    decision = suggestion.get("decision", "").lower()
    alias_target_id = None
    adjacency_ids = []
    canonical_for_cache = None

    if decision == "alias":
        target_name = (suggestion.get("alias_target_canonical") or "").strip()
        alias_target_id = skill_id_by_canonical.get(target_name.lower())
        canonical_for_cache = target_name
    elif decision == "promote":
        canonical_for_cache = suggestion.get("canonical_name") or ""
        for adj_name in (suggestion.get("adjacent_canonicals") or []):
            sid = skill_id_by_canonical.get((adj_name or "").strip().lower())
            if sid:
                adjacency_ids.append(sid)

    # 5. Register the model version (compliance) + cache
    try:
        provider_rs = await client.execute(
            "SELECT byok_provider FROM users WHERE id = ?", [user_id]
        )
        provider = provider_rs.rows[0][0] if provider_rs.rows and provider_rs.rows[0][0] else "unknown"
        mv_id = await register_model_version_func(
            client,
            prompt_name="skill_suggester",
            prompt_text=SKILL_SUGGESTION_PROMPT,
            model_provider=provider,
            model_name=provider,
        )
    except Exception:
        mv_id = None

    cache_id = "sps_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO skill_promotion_suggestions
           (id, raw_text_normalized, suggestion_type, suggested_canonical,
            suggested_category, suggested_adjacencies, suggested_alias_target,
            confidence, llm_rationale, model_version_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            cache_id, raw_text_normalized,
            decision if decision in ("alias", "promote", "reject") else "reject",
            canonical_for_cache,
            suggestion.get("category"),
            json.dumps(adjacency_ids),
            alias_target_id,
            float(suggestion.get("confidence", 0.5)),
            suggestion.get("rationale", ""),
            mv_id,
        ],
    )

    return {
        "decision": decision,
        "canonical_name": canonical_for_cache,
        "category": suggestion.get("category"),
        "aliases": suggestion.get("aliases", []),
        "adjacencies_skill_ids": adjacency_ids,
        "adjacent_canonicals": suggestion.get("adjacent_canonicals", []),
        "alias_target_skill_id": alias_target_id,
        "alias_target_canonical": suggestion.get("alias_target_canonical"),
        "weight": suggestion.get("weight", "medium"),
        "confidence": float(suggestion.get("confidence", 0.5)),
        "rationale": suggestion.get("rationale", ""),
        "from_cache": False,
    }


# ----------------------------------------------------------------------
# Decision actions (alias / promote / reject)
# ----------------------------------------------------------------------

async def _backpopulate_existing(client, raw_text_normalized: str, skill_id: str) -> int:
    """When a skill is aliased or promoted, retroactively set skill_id
    on all existing req_skills and candidate_skills rows that share
    the normalized raw_text. Returns total rows updated.
    """
    r1 = await client.execute(
        """UPDATE req_skills
           SET skill_id = ?
           WHERE skill_id IS NULL
             AND LOWER(TRIM(raw_skill_text)) = ?""",
        [skill_id, raw_text_normalized],
    )
    r2 = await client.execute(
        """UPDATE candidate_skills
           SET skill_id = ?
           WHERE skill_id IS NULL
             AND LOWER(TRIM(raw_skill_text)) = ?""",
        [skill_id, raw_text_normalized],
    )
    # Count is best-effort — Turso doesn't always populate affected_rows
    # in a useful way over HTTP. We re-query to confirm.
    cnt_rs = await client.execute(
        """SELECT
              (SELECT COUNT(*) FROM req_skills WHERE skill_id = ?
                 AND LOWER(TRIM(raw_skill_text)) = ?) +
              (SELECT COUNT(*) FROM candidate_skills WHERE skill_id = ?
                 AND LOWER(TRIM(raw_skill_text)) = ?)
        """,
        [skill_id, raw_text_normalized, skill_id, raw_text_normalized],
    )
    return int(cnt_rs.rows[0][0] or 0) if cnt_rs.rows else 0


async def apply_alias(
    client,
    raw_text_normalized: str,
    target_skill_id: str,
    user_id: str,
    notes: Optional[str] = None,
    write_audit_event_func=None,
) -> dict:
    """Add raw_text as an alias of an existing skill, back-populate."""
    # Verify target skill exists + load its current aliases
    skill_rs = await client.execute(
        "SELECT id, canonical_name, aliases_json FROM skills WHERE id = ?",
        [target_skill_id],
    )
    if not skill_rs.rows:
        raise ValueError(f"Target skill {target_skill_id} not found")
    sid, canonical, aliases_json = skill_rs.rows[0]

    try:
        aliases = json.loads(aliases_json) if aliases_json else []
    except Exception:
        aliases = []

    # Add the raw text as a new alias if not already present
    if raw_text_normalized not in [a.lower() for a in aliases]:
        aliases.append(raw_text_normalized)
        await client.execute(
            "UPDATE skills SET aliases_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [json.dumps(aliases), sid],
        )

    # Back-populate existing rows
    rows_updated = await _backpopulate_existing(client, raw_text_normalized, sid)

    # Audit event
    ae_id = None
    if write_audit_event_func:
        try:
            ae_id = await write_audit_event_func(
                client,
                event_type="taxonomy_change",
                action="alias_skill",
                actor_user_id=user_id,
                entity_type="skill",
                entity_id=sid,
                inputs={"raw_text": raw_text_normalized},
                outputs={"canonical_name": canonical, "rows_backpopulated": rows_updated},
                model_version_id=None,
            )
        except Exception as e:
            print(f"[skill_resolution] audit failed: {e!r}")

    # Persist the decision
    decision_id = "srd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO skill_resolution_decisions
           (id, raw_text_normalized, decision, resolved_skill_id,
            decided_by_user_id, notes, audit_event_id)
           VALUES (?, ?, 'alias', ?, ?, ?, ?)""",
        [decision_id, raw_text_normalized, sid, user_id, notes, ae_id],
    )

    # Bust the suggestion cache for this raw_text
    await client.execute(
        "DELETE FROM skill_promotion_suggestions WHERE raw_text_normalized = ?",
        [raw_text_normalized],
    )

    return {
        "decision": "alias",
        "raw_text": raw_text_normalized,
        "skill_id": sid,
        "canonical_name": canonical,
        "rows_backpopulated": rows_updated,
        "audit_event_id": ae_id,
    }


async def apply_promote(
    client,
    raw_text_normalized: str,
    canonical_name: str,
    category: str,
    aliases: list[str],
    adjacent_skill_ids: list[str],
    weight: str,
    user_id: str,
    notes: Optional[str] = None,
    write_audit_event_func=None,
) -> dict:
    """Create a new skill row, link adjacencies, back-populate."""
    if not canonical_name or not category:
        raise ValueError("canonical_name and category are required for promote")

    # Check if the canonical name already exists — promote to alias instead
    # if so. Defensive — UI should have caught this but belt and suspenders.
    existing = await client.execute(
        "SELECT id FROM skills WHERE LOWER(canonical_name) = ?",
        [canonical_name.lower()],
    )
    if existing.rows:
        # Defer to apply_alias to avoid duplicate skills
        return await apply_alias(
            client,
            raw_text_normalized=raw_text_normalized,
            target_skill_id=existing.rows[0][0],
            user_id=user_id,
            notes=(notes or "") + " (auto-aliased: canonical exists)",
            write_audit_event_func=write_audit_event_func,
        )

    # Create the new skill row
    new_skill_id = "sk_" + uuid.uuid4().hex[:16]
    # Normalize aliases — include the raw text as an alias automatically
    alias_set = {a.strip().lower() for a in (aliases or []) if a and a.strip()}
    alias_set.add(raw_text_normalized)
    aliases_final = sorted(alias_set)

    await client.execute(
        """INSERT INTO skills (id, canonical_name, category, aliases_json, weight)
           VALUES (?, ?, ?, ?, ?)""",
        [new_skill_id, canonical_name.strip(), category.strip(),
         json.dumps(aliases_final), (weight or "medium").lower()],
    )

    # Symmetric adjacencies
    adj_count = 0
    for adj_id in (adjacent_skill_ids or []):
        if adj_id == new_skill_id:
            continue
        for a, b in [(new_skill_id, adj_id), (adj_id, new_skill_id)]:
            row_id = "sa_" + uuid.uuid4().hex[:16]
            try:
                await client.execute(
                    """INSERT OR IGNORE INTO skill_adjacencies
                       (id, skill_id, adjacent_id, weight, source)
                       VALUES (?, ?, ?, 0.6, 'taxonomy')""",
                    [row_id, a, b],
                )
            except Exception as e:
                print(f"[skill_resolution] adjacency insert failed: {e!r}")
        adj_count += 1

    # Back-populate
    rows_updated = await _backpopulate_existing(client, raw_text_normalized, new_skill_id)

    # Audit event
    ae_id = None
    if write_audit_event_func:
        try:
            ae_id = await write_audit_event_func(
                client,
                event_type="taxonomy_change",
                action="promote_skill",
                actor_user_id=user_id,
                entity_type="skill",
                entity_id=new_skill_id,
                inputs={"raw_text": raw_text_normalized,
                        "category": category,
                        "adj_count": adj_count},
                outputs={"canonical_name": canonical_name,
                         "skill_id": new_skill_id,
                         "rows_backpopulated": rows_updated,
                         "alias_count": len(aliases_final)},
                model_version_id=None,
            )
        except Exception as e:
            print(f"[skill_resolution] audit failed: {e!r}")

    # Persist decision + bust cache
    decision_id = "srd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO skill_resolution_decisions
           (id, raw_text_normalized, decision, resolved_skill_id,
            decided_by_user_id, notes, audit_event_id)
           VALUES (?, ?, 'promote', ?, ?, ?, ?)""",
        [decision_id, raw_text_normalized, new_skill_id, user_id, notes, ae_id],
    )
    await client.execute(
        "DELETE FROM skill_promotion_suggestions WHERE raw_text_normalized = ?",
        [raw_text_normalized],
    )

    return {
        "decision": "promote",
        "raw_text": raw_text_normalized,
        "skill_id": new_skill_id,
        "canonical_name": canonical_name,
        "category": category,
        "alias_count": len(aliases_final),
        "adjacency_count": adj_count,
        "rows_backpopulated": rows_updated,
        "audit_event_id": ae_id,
    }


async def apply_reject(
    client,
    raw_text_normalized: str,
    user_id: str,
    notes: Optional[str] = None,
    write_audit_event_func=None,
) -> dict:
    """Mark raw_text as not-a-skill so it stops appearing in the queue.
    Does NOT delete the existing req_skills/candidate_skills rows —
    those keep their NULL skill_id (the data was extracted, just not
    promotable). What this does is record the decision so the
    discovery query filters it out.
    """
    ae_id = None
    if write_audit_event_func:
        try:
            ae_id = await write_audit_event_func(
                client,
                event_type="taxonomy_change",
                action="reject_skill",
                actor_user_id=user_id,
                entity_type="raw_skill_text",
                entity_id=raw_text_normalized[:64],
                inputs={"raw_text": raw_text_normalized},
                outputs={"reason": notes or "user rejected as not-a-skill"},
                model_version_id=None,
            )
        except Exception as e:
            print(f"[skill_resolution] audit failed: {e!r}")

    decision_id = "srd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO skill_resolution_decisions
           (id, raw_text_normalized, decision, resolved_skill_id,
            decided_by_user_id, notes, audit_event_id)
           VALUES (?, ?, 'reject', NULL, ?, ?, ?)""",
        [decision_id, raw_text_normalized, user_id, notes, ae_id],
    )
    await client.execute(
        "DELETE FROM skill_promotion_suggestions WHERE raw_text_normalized = ?",
        [raw_text_normalized],
    )
    return {"decision": "reject", "raw_text": raw_text_normalized, "audit_event_id": ae_id}
