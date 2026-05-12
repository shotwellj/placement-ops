"""
Compliance helpers for SourcingNav.

Shared utilities for GDPR / EU AI Act / CCPA / EEOC / SOC 2 coverage.

Every automated decision in the app should call `write_audit_event` plus
`write_decision_explanation`. Every time we capture personal data about
a natural person, call `register_data_subject`. Every time we call an AI
model, reference a `model_version` row.

Defaults are pre-decided by Jason (see userMemories Phase A notes):
  - Retention: active indefinite, closed 2yr anonymize, placed 7yr
  - Consent:   legitimate interest (GDPR Art 6(1)(f))
  - Bias:      voluntary self-ID only (protected_attributes)
  - Deletion:  anonymize (GDPR Recital 26)

Import pattern in api/index.py:
    from api._compliance import (
        register_data_subject, register_model_version,
        write_audit_event, write_decision_explanation,
    )
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Optional

# HMAC key for audit chain. Separate from MAGIC_LINK_SECRET in production -
# but fall back to it so the deploy works without a new env var right away.
AUDIT_HMAC_KEY = os.environ.get("AUDIT_HMAC_KEY") or os.environ.get("MAGIC_LINK_SECRET") or ""

RETENTION_POLICY_DEFAULT = "active_indefinite_closed_2yr_placed_7yr"
LEGAL_BASIS_DEFAULT = "legitimate_interest"


def _hash_payload(payload: dict) -> str:
    """Stable SHA-256 hash of a JSON-serializable payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _compute_hmac(seq: int, inputs_hash: str, outputs_hash: str, prev_hmac: str) -> str:
    """Compute the HMAC link for an audit event.

    Chain definition: HMAC(key, seq || inputs_hash || outputs_hash || prev_hmac)
    Breaking any one of these makes the chain invalid from that point forward.
    This is the EU AI Act Article 12 tamper-evidence pattern.
    """
    if not AUDIT_HMAC_KEY:
        # Degraded mode: no key, store a marker. Still logs the event.
        return "no_key:" + _hash_payload(
            {"seq": seq, "in": inputs_hash, "out": outputs_hash, "prev": prev_hmac}
        )[:32]
    message = f"{seq}|{inputs_hash}|{outputs_hash}|{prev_hmac}".encode()
    return hmac.new(AUDIT_HMAC_KEY.encode(), message, hashlib.sha256).hexdigest()


async def register_data_subject(
    client,
    subject_type: str,
    linked_entity_id: str,
    jurisdiction: Optional[str] = None,
) -> str:
    """Create or fetch a data_subjects row for a natural person.

    subject_type: 'candidate' | 'employee' | 'recruiter' | 'hiring_manager'
    linked_entity_id: the candidates.id, employees.id, users.id, etc.

    Returns the data_subjects.id (new or existing). Idempotent:
    if a row for this (subject_type, linked_entity_id) already exists, returns it.
    """
    rs = await client.execute(
        """SELECT id FROM data_subjects
           WHERE subject_type = ? AND linked_entity_id = ?""",
        [subject_type, linked_entity_id],
    )
    if rs.rows:
        return rs.rows[0][0]

    subject_id = "ds_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO data_subjects
           (id, subject_type, linked_entity_id, legal_basis, jurisdiction, retention_policy)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [subject_id, subject_type, linked_entity_id, LEGAL_BASIS_DEFAULT,
         jurisdiction, RETENTION_POLICY_DEFAULT],
    )
    return subject_id


async def register_model_version(
    client,
    prompt_name: str,
    prompt_text: str,
    model_provider: str,
    model_name: str,
    taxonomy_snapshot: Optional[str] = None,
    git_commit_sha: Optional[str] = None,
) -> str:
    """Register (or reuse) a model_versions row for a given prompt+model combo.

    Version tag is derived from content hash, so the same prompt+model+taxonomy
    always maps to the same version row. If the prompt text changes, a new
    row is created and the old one is retired_at set by the caller if desired.

    Used for EU AI Act Article 11 (technical documentation) - every score
    is traceable to an exact prompt + exact model version.
    """
    prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
    # Version tag: short hash suffix makes the UUID-ish id readable in logs
    version_tag = f"{prompt_name}@{prompt_hash[:12]}"

    rs = await client.execute(
        "SELECT id FROM model_versions WHERE version_tag = ?",
        [version_tag],
    )
    if rs.rows:
        return rs.rows[0][0]

    mv_id = "mv_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO model_versions
           (id, version_tag, prompt_name, prompt_hash, model_provider, model_name,
            taxonomy_snapshot, git_commit_sha, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        [mv_id, version_tag, prompt_name, prompt_hash, model_provider, model_name,
         taxonomy_snapshot, git_commit_sha],
    )
    return mv_id


async def write_audit_event(
    client,
    event_type: str,
    action: str,
    actor_user_id: Optional[str] = None,
    actor_ip: Optional[str] = None,
    subject_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    inputs: Optional[dict] = None,
    outputs: Optional[dict] = None,
    model_version_id: Optional[str] = None,
    confidence_score: Optional[float] = None,
) -> str:
    """Write an audit_events row with tamper-evident HMAC chain.

    Returns the audit_events.id.

    Call this on every automated decision + any sensitive user action:
      - event_type: 'ai_decision' | 'data_access' | 'consent_change' | 'deletion'
      - action:     human-readable verb ('evaluate_candidate', 'export_subject_data')

    The inputs/outputs dicts are hashed, not stored. If you need the raw payload
    for SOC 2 or dispute-investigation reasons, save it to a separate encrypted
    log in a non-queryable table. For now: hashes only (privacy-preserving).
    """
    # Fetch the previous seq + hmac to link the chain
    rs = await client.execute(
        "SELECT seq, hmac_chain FROM audit_events ORDER BY seq DESC LIMIT 1"
    )
    if rs.rows:
        prev_seq, prev_hmac = rs.rows[0][0], rs.rows[0][1]
        seq = (prev_seq or 0) + 1
    else:
        prev_hmac = "genesis"
        seq = 1

    inputs_hash = _hash_payload(inputs) if inputs else ""
    outputs_hash = _hash_payload(outputs) if outputs else ""
    hmac_val = _compute_hmac(seq, inputs_hash, outputs_hash, prev_hmac)

    event_id = "ae_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO audit_events
           (id, seq, event_type, actor_user_id, actor_ip, subject_id,
            entity_type, entity_id, action, inputs_hash, outputs_hash,
            model_version_id, confidence_score, hmac_chain, prev_hmac)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [event_id, seq, event_type, actor_user_id, actor_ip, subject_id,
         entity_type, entity_id, action, inputs_hash, outputs_hash,
         model_version_id, confidence_score, hmac_val, prev_hmac],
    )
    return event_id


async def write_decision_explanation(
    client,
    audit_event_id: str,
    decision_type: str,
    decision_outcome: str,
    top_factors: list[dict],
    plain_english: str,
    subject_id: Optional[str] = None,
) -> str:
    """Write a human-readable explanation tied to an audit event.

    EU AI Act Article 13 requires that any person affected by an automated
    decision can get an explanation in plain language. NYC Local Law 144
    and Colorado AI Act have similar requirements for employment decisions.

    top_factors: [{"factor": "PyTorch experience", "weight": 0.28, "signal": "present"}, ...]
    plain_english: 1-2 sentences a non-technical person can understand.
    """
    exp_id = "de_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO decision_explanations
           (id, audit_event_id, subject_id, decision_type, decision_outcome,
            top_factors_json, plain_english, human_review_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'not_requested')""",
        [exp_id, audit_event_id, subject_id, decision_type, decision_outcome,
         json.dumps(top_factors), plain_english],
    )
    return exp_id


async def write_submission_dimensions(
    client,
    submission_id: str,
    evaluation: dict,
    req_id: str = None,
    candidate_id: str = None,
) -> None:
    """Write 8-dimension scores. Two dimensions (Technical Match, Gap Severity)
    are now DETERMINISTIC and come from the matching engine. The other 6
    dimensions stay AI-derived for now (Phase A finishing, 2026-05-11).

    Phase A finishing: matching engine in code, not prompt.
    - Dim 1 (Technical Match): deterministic via _matching_engine.evaluate_skills
    - Dim 6 (Gap Severity): deterministic via _matching_engine.evaluate_skills
    - Dims 2-5, 7-8: still AI-derived (mapped from fit_score for now)

    req_id and candidate_id are optional - if not provided, only the AI-derived
    scores are written (backward compatible with old call sites).
    """
    fit_score = evaluation.get("fit_score")
    if fit_score is None:
        return

    # Normalize the 0-100 AI fit score into the 0-5 rubric scale (fallback).
    composite_fallback = round((fit_score / 100.0) * 5.0, 2)

    # AI-derived blocker count (fallback when engine isn't available)
    blockers = evaluation.get("blocker_assessment") or []
    blocker_count_fallback = sum(1 for b in blockers if b.get("status") == "missing")

    # Engine-derived deterministic scores (NEW in Phase A finishing)
    technical_match_score = None
    gap_severity_score = None
    blocker_count = blocker_count_fallback
    engine_breakdown = None

    if req_id and candidate_id:
        try:
            engine_result = await _run_matching_engine(client, req_id, candidate_id)
            if engine_result:
                technical_match_score = engine_result["technical_match"]["score"]
                gap_severity_score = engine_result["gap_severity"]["score"]
                blocker_count = engine_result["blocker_count"]
                engine_breakdown = engine_result
        except Exception as engine_err:
            # Engine failure is non-fatal - we still write the AI scores
            print(f"[matching-engine] non-fatal: {engine_err!r}")

    # Combine engine output with AI eval output for the match_breakdown_json
    breakdown_blob = {"ai_eval": evaluation}
    if engine_breakdown:
        breakdown_blob["engine"] = engine_breakdown

    sd_id = "sd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT OR REPLACE INTO submission_dimensions
           (id, submission_id, technical_match, gap_severity, composite_score,
            blocker_count, match_breakdown_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            sd_id,
            submission_id,
            technical_match_score,
            gap_severity_score,
            composite_fallback,  # composite still AI-derived; expand later
            blocker_count,
            json.dumps(breakdown_blob),
        ],
    )


async def _run_matching_engine(client, req_id: str, candidate_id: str):
    """Internal: load req_skills + candidate_skills + adjacencies, run engine."""
    # Lazy import to avoid circular dep
    from api._matching_engine import evaluate_skills, build_adjacency_index

    # Load req_skills for this requisition
    rs = await client.execute(
        """SELECT raw_skill_text, skill_id, importance
           FROM req_skills WHERE req_id = ?""",
        [req_id],
    )
    req_skills = [
        {"raw_skill_text": r[0], "skill_id": r[1], "importance": r[2]}
        for r in rs.rows
    ]

    # Load candidate_skills for this candidate
    cs = await client.execute(
        """SELECT raw_skill_text, skill_id, recency, depth
           FROM candidate_skills WHERE candidate_id = ?""",
        [candidate_id],
    )
    candidate_skills = [
        {"raw_skill_text": r[0], "skill_id": r[1], "recency": r[2], "depth": r[3]}
        for r in cs.rows
    ]

    # If either side is empty, skip - engine has nothing to do
    if not req_skills or not candidate_skills:
        return None

    # Load adjacencies relevant to the skill ids we have
    skill_ids = set()
    for r in req_skills:
        if r["skill_id"]:
            skill_ids.add(r["skill_id"])
    for c in candidate_skills:
        if c["skill_id"]:
            skill_ids.add(c["skill_id"])

    if skill_ids:
        # Chunk the IN clause for safety
        placeholders = ",".join("?" for _ in skill_ids)
        adj = await client.execute(
            f"""SELECT skill_id, adjacent_id, weight
                FROM skill_adjacencies
                WHERE skill_id IN ({placeholders}) OR adjacent_id IN ({placeholders})""",
            list(skill_ids) + list(skill_ids),
        )
        adj_rows = [(r[0], r[1], r[2]) for r in adj.rows]
    else:
        adj_rows = []

    adjacency_index = build_adjacency_index(adj_rows)

    return evaluate_skills(req_skills, candidate_skills, adjacency_index)


# ============================================================
# Taxonomy resolution + skill writes
# ============================================================

async def resolve_skill_id(client, raw_name: str) -> Optional[str]:
    """Match a raw skill name from AI output against the canonical taxonomy.

    Strategy (fail-soft, four tiers, in priority order):
      1. Exact match on skills.canonical_name (case-insensitive)
      2. Match any alias in skills.aliases_json (case-insensitive)
      3. Match a previous user decision in skill_resolution_decisions -
         if the user aliased "c programming" -> C via Phase B2, future
         intakes resolve "c programming" automatically. THIS IS THE LOOP.
      4. Single-token whole-word match against canonical_name. Conservative
         on purpose: only fires for short raw texts where we're confident.
         "ARM Cortex" -> tokens [arm, cortex] -> "arm" matches canonical
         "ARM" exactly. Skipped if raw_text has 4+ tokens to avoid
         false positives like "experience with python and c++" -> Python.
      5. Return None if no match - caller still inserts with raw_skill_text
         populated, so we never lose data.

    The user trains the resolver via the Phase B2 approval queue at
    /app/taxonomy. Each alias decision becomes a permanent resolution rule
    via tier 3.
    """
    if not raw_name or not raw_name.strip():
        return None
    norm = " ".join(raw_name.strip().lower().split())

    # ---------- Tier 1: exact canonical match ----------
    rs = await client.execute(
        "SELECT id FROM skills WHERE LOWER(canonical_name) = ?",
        [norm],
    )
    if rs.rows:
        return rs.rows[0][0]

    # ---------- Tier 2: alias match ----------
    rs = await client.execute(
        "SELECT id, aliases_json FROM skills WHERE aliases_json IS NOT NULL"
    )
    for row in rs.rows:
        sid, aj = row[0], row[1]
        if not aj:
            continue
        try:
            aliases = json.loads(aj)
            for a in aliases:
                if " ".join(str(a).strip().lower().split()) == norm:
                    return sid
        except Exception:
            continue

    # ---------- Tier 3: previous user decision (the learning loop) ----------
    # If the user has previously aliased OR promoted this exact raw text,
    # use that decision automatically. Reject decisions are also honored -
    # if the user said this isn't a skill, we don't try to match it.
    try:
        rs = await client.execute(
            """SELECT decision, resolved_skill_id
               FROM skill_resolution_decisions
               WHERE raw_text_normalized = ?""",
            [norm],
        )
        if rs.rows and rs.rows[0]:
            decision, resolved_id = rs.rows[0]
            if decision in ("alias", "promote") and resolved_id:
                return resolved_id
            # decision == 'reject' falls through to tier 4 then None
    except Exception:
        # skill_resolution_decisions may not exist on very old environments
        pass

    # ---------- Tier 4: single-token whole-word fallback ----------
    # Conservative. Only fires for raw texts of 1-3 tokens. Looks for any
    # token that exactly matches a canonical name as a whole word. Tied
    # matches (multiple skills match) -> bail out, return None to avoid
    # picking the wrong one.
    tokens = [t for t in norm.replace(",", " ").split() if t]
    if 1 <= len(tokens) <= 3:
        # Build a set of (token, skill_id) pairs we'll check
        # Re-fetch canonical names - small table, in-memory filter is fine
        rs = await client.execute("SELECT id, LOWER(canonical_name) FROM skills")
        canonical_map = {}  # canonical_lower -> skill_id
        for row in (rs.rows or []):
            canonical_map[row[1]] = row[0]

        # Strict whole-word match: a token must be IDENTICAL to a canonical
        matches = set()
        for tok in tokens:
            if tok in canonical_map:
                matches.add(canonical_map[tok])
        if len(matches) == 1:
            # Exactly one canonical matched - confident enough
            return next(iter(matches))
        # 0 matches OR ambiguous (2+) -> fall through to None

    return None


async def write_req_skills(client, req_id: str, parsed: dict) -> int:
    """Extract structured skills from parsed JD output and write req_skills rows.

    Priority:
      1. parsed['canonical_skills']: new format - list of {name, severity}.
         These are clean skill names AI was prompted to emit specifically for
         taxonomy matching. Used when JD_PARSER_PROMPT includes the
         canonical_skills instruction block.
      2. parsed['must_have_skills'] + parsed['nice_to_have_skills']: legacy
         fallback. These entries are prose rationale that rarely matches the
         taxonomy cleanly, but we store them so no data is lost.

    Returns the number of rows inserted.
    """
    # Preferred path: canonical_skills emitted directly by the AI
    canonical = parsed.get("canonical_skills")
    if isinstance(canonical, list) and canonical:
        inserted = 0
        for s in canonical:
            if not isinstance(s, dict) or not s.get("name"):
                continue
            name = s["name"].strip()
            if not name:
                continue
            sev = s.get("severity", "preferred")
            importance = (
                "blocker" if sev == "blocker"
                else "nice_to_have" if sev == "nice_to_have"
                else "preferred"
            )
            skill_id = await resolve_skill_id(client, name)
            row_id = "rs_" + uuid.uuid4().hex[:16]
            await client.execute(
                """INSERT INTO req_skills
                   (id, req_id, skill_id, raw_skill_text, importance, rationale)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [row_id, req_id, skill_id, name, importance, None],
            )
            inserted += 1
        return inserted

    # Legacy fallback: must_have_skills + nice_to_have_skills (prose rationale)
    def _candidates():
        for s in (parsed.get("must_have_skills") or []):
            if isinstance(s, dict) and s.get("skill"):
                sev = s.get("severity", "preferred")
                importance = "blocker" if sev == "blocker" else "preferred"
                yield (s["skill"], importance, s.get("rationale"))
        for s in (parsed.get("nice_to_have_skills") or []):
            if isinstance(s, dict) and s.get("skill"):
                yield (s["skill"], "nice_to_have", s.get("rationale"))

    inserted = 0
    for skill_name, importance, rationale in _candidates():
        skill_id = await resolve_skill_id(client, skill_name)
        row_id = "rs_" + uuid.uuid4().hex[:16]
        await client.execute(
            """INSERT INTO req_skills
               (id, req_id, skill_id, raw_skill_text, importance, rationale)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [row_id, req_id, skill_id, skill_name, importance, rationale],
        )
        inserted += 1
    return inserted


async def write_candidate_skills(client, candidate_id: str, evaluation: dict) -> int:
    """Extract structured skills from candidate evaluation output and write rows.

    The CANDIDATE_EVAL_PROMPT is being updated to emit an 'extracted_skills'
    array with {name, evidence, recency, depth, confidence}. If that field is
    missing (old-format eval), we fall back to extracting skill names from
    blocker_assessment[].skill and preferred_assessment[].skill with status='met'
    or 'partial' - those are skills we have evidence the candidate has.

    Returns the number of rows inserted.
    """
    # New format path: explicit extracted_skills
    explicit = evaluation.get("extracted_skills")
    if isinstance(explicit, list) and explicit:
        inserted = 0
        for s in explicit:
            if not isinstance(s, dict) or not s.get("name"):
                continue
            sid = await resolve_skill_id(client, s["name"])
            row_id = "cs_" + uuid.uuid4().hex[:16]
            await client.execute(
                """INSERT INTO candidate_skills
                   (id, candidate_id, skill_id, raw_skill_text, evidence,
                    recency, depth, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row_id, candidate_id, sid, s["name"],
                    (s.get("evidence") or "")[:500],
                    s.get("recency", "current"),
                    s.get("depth", "mentioned"),
                    float(s.get("confidence", 0.5)),
                ],
            )
            inserted += 1
        return inserted


    # Fallback path: pull confirmed skills out of blocker/preferred assessments
    inserted = 0
    for source_field in ("blocker_assessment", "preferred_assessment"):
        for s in (evaluation.get(source_field) or []):
            if not isinstance(s, dict) or not s.get("skill"):
                continue
            status = s.get("status")
            # Only insert skills with at least partial evidence. A "missing"
            # blocker is not a candidate skill - it's a gap.
            if status not in ("met", "partial"):
                continue
            sid = await resolve_skill_id(client, s["skill"])
            row_id = "cs_" + uuid.uuid4().hex[:16]
            # Without explicit recency/depth, set conservative defaults
            depth = "production" if status == "met" else "project"
            await client.execute(
                """INSERT INTO candidate_skills
                   (id, candidate_id, skill_id, raw_skill_text, evidence,
                    recency, depth, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row_id, candidate_id, sid, s["skill"],
                    (s.get("evidence") or "")[:500],
                    "current",  # fallback assumption
                    depth,
                    0.7 if status == "met" else 0.5,
                ],
            )
            inserted += 1
    return inserted
