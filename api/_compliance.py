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
    """Write 8-dimension scores using the deterministic matching engine.

    Phase A finished, 2026-05-11. All 8 dimensions now flow through the
    engine. Dims 1, 2, 3, 4, 6, 8 are pure code. Dims 5 and 7 are AI-
    proposed with code validating range and structure. Composite is
    computed in code from the 25/15/10/15/5/10/10/10 weights per
    modes/_shared.md.

    The AI's `fit_score` is preserved in match_breakdown_json for
    historical comparison but is no longer the source of truth.

    req_id and candidate_id are optional. When provided, the engine
    path runs. When omitted, only the AI fit_score is stored (legacy
    callers should pass both going forward).
    """
    fit_score = evaluation.get("fit_score")
    ai_composite_fallback = (
        round((fit_score / 100.0) * 5.0, 2) if fit_score is not None else None
    )

    # AI-derived blocker count (fallback when engine isn't available)
    blockers = evaluation.get("blocker_assessment") or []
    blocker_count_fallback = sum(1 for b in blockers if b.get("status") == "missing")

    # Engine output (NULL when engine can't run)
    engine_result = None
    if req_id and candidate_id:
        try:
            engine_result = await _run_matching_engine(
                client, req_id, candidate_id, evaluation
            )
        except Exception as engine_err:
            print(f"[matching-engine] non-fatal: {engine_err!r}")

    # Extract per-dimension scores from engine, fall back to None where missing
    if engine_result:
        dims = engine_result.get("dimensions", {})
        technical_match = dims.get("technical_match", {}).get("score")
        seniority_fit = dims.get("seniority_fit", {}).get("score")
        location_alignment = dims.get("location_alignment", {}).get("score")
        comp_alignment = dims.get("comp_alignment", {}).get("score")
        culture_signals = dims.get("culture_signals", {}).get("score")
        gap_severity = dims.get("gap_severity", {}).get("score")
        presentation_risk = dims.get("presentation_risk", {}).get("score")
        fill_probability = dims.get("fill_probability", {}).get("score")
        composite_data = engine_result.get("composite", {})
        composite_score = composite_data.get("effective_composite", ai_composite_fallback)
        blocker_count = engine_result.get("blocker_count", blocker_count_fallback)
    else:
        technical_match = None
        seniority_fit = None
        location_alignment = None
        comp_alignment = None
        culture_signals = None
        gap_severity = None
        presentation_risk = None
        fill_probability = None
        composite_score = ai_composite_fallback
        blocker_count = blocker_count_fallback

    # Combine engine output with AI eval for transparency
    breakdown_blob = {"ai_eval": evaluation}
    if engine_result:
        breakdown_blob["engine"] = engine_result

    sd_id = "sd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT OR REPLACE INTO submission_dimensions
           (id, submission_id, technical_match, seniority_fit, location_alignment,
            comp_alignment, culture_signals, gap_severity, presentation_risk,
            fill_probability, composite_score, blocker_count, match_breakdown_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            sd_id,
            submission_id,
            technical_match,
            seniority_fit,
            location_alignment,
            comp_alignment,
            culture_signals,
            gap_severity,
            presentation_risk,
            fill_probability,
            composite_score,
            blocker_count,
            json.dumps(breakdown_blob),
        ],
    )


async def _run_matching_engine(client, req_id: str, candidate_id: str,
                                evaluation: dict = None):
    """Internal: load all required data, run full 8-dimension engine."""
    # Lazy import to avoid circular dep
    from api._matching_engine import evaluate_full, build_adjacency_index

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

    # Load req metadata from parsed_json (location, comp, seniority signals)
    req_metadata = await _load_req_metadata(client, req_id)

    # Build candidate metadata from candidates table + AI eval output
    candidate_metadata = await _load_candidate_metadata(
        client, candidate_id, evaluation or {}
    )

    # Extract AI proposals for culture + presentation from evaluation
    ai_proposals = _extract_ai_proposals(evaluation or {})

    # Historical fill rate for this req's user (or org-wide if needed)
    historical_data = await _load_historical_fill_rate(client, req_id)

    return evaluate_full(
        req_skills=req_skills,
        candidate_skills=candidate_skills,
        adjacency_index=adjacency_index,
        req_metadata=req_metadata,
        candidate_metadata=candidate_metadata,
        ai_proposals=ai_proposals,
        historical_data=historical_data,
    )


async def _load_req_metadata(client, req_id: str) -> dict:
    """Extract location, comp range, seniority signals from req parsed_json."""
    rs = await client.execute(
        "SELECT parsed_json FROM requisitions WHERE id = ?", [req_id]
    )
    if not rs.rows or not rs.rows[0][0]:
        return {}
    try:
        parsed = json.loads(rs.rows[0][0])
    except Exception:
        return {}

    core = parsed.get("core") or {}
    comp = parsed.get("comp_snapshot") or {}

    # Seniority signals come from must_have_skills rationale + executive_brief
    seniority_signals = []
    eb = parsed.get("executive_brief") or {}
    if isinstance(eb, dict):
        for key in ("seniority", "seniority_level", "level", "experience_required"):
            v = eb.get(key)
            if v and isinstance(v, str):
                seniority_signals.append(v)
    # Look for "X years" patterns in must_have_skills rationale
    import re
    for skill in (parsed.get("must_have_skills") or []):
        rat = skill.get("rationale", "") if isinstance(skill, dict) else ""
        m = re.search(r'(\d+)\+?\s*years?', rat, re.IGNORECASE)
        if m:
            seniority_signals.append(f"{m.group(1)}+ years experience")
            break

    return {
        "location": core.get("location") or parsed.get("location", "") or "",
        "remote_policy": core.get("remote_policy") or parsed.get("remote_policy", "") or "",
        "comp_range": comp.get("base_range") or comp.get("total_comp_range") or "",
        "seniority_signals": seniority_signals,
    }


async def _load_candidate_metadata(client, candidate_id: str,
                                    evaluation: dict) -> dict:
    """Build candidate metadata from candidates table + AI eval output.

    2026-05-11 Phase A finishing: location extraction now uses LinkedIn-style
    City/State/Country patterns and also pulls from AI eval's summary +
    blocker_assessment evidence fields, where the AI often mentions location
    even when the raw resume_text doesn't have a "Location:" label.
    """
    rs = await client.execute(
        """SELECT current_title, current_company, notes, resume_text
           FROM candidates WHERE id = ?""",
        [candidate_id],
    )
    notes = ""
    resume = ""
    if rs.rows:
        notes = rs.rows[0][2] or ""
        resume = (rs.rows[0][3] or "")[:3000]  # first 3k chars covers LinkedIn header

    # Build a corpus of text to search across, in priority order
    eval_summary = evaluation.get("summary", "") or ""
    eval_headline = evaluation.get("headline", "") or ""
    blocker_evidence_blob = ""
    for s in (evaluation.get("blocker_assessment") or []):
        if isinstance(s, dict):
            blocker_evidence_blob += " " + (s.get("evidence") or "")

    location = _extract_location_from_text(
        resume, notes, eval_summary, eval_headline, blocker_evidence_blob
    )

    # AI eval's comp_check gives expected comp signal
    expected_comp = ""
    comp_check = evaluation.get("comp_check", "")
    if comp_check and "unknown" not in comp_check.lower():
        import re
        m = re.search(r'\$[\d,]+(?:k|K)?', comp_check)
        if m:
            expected_comp = m.group(0)

    # Seniority signals derived from AI eval's strengths + extracted skills
    seniority_signals = _extract_candidate_seniority(evaluation)

    return {
        "location": location,
        "expected_comp": expected_comp,
        "seniority_signals": seniority_signals,
    }


def _extract_location_from_text(*sources) -> str:
    """Extract a location string from multiple text sources.

    Tries patterns in order of confidence:
      1. LinkedIn-style "City, State/Province, Country" (3 parts) - highest
      2. Standard "City, State/Province" (2 parts, US/Canada style)
      3. Old "Location: X" or "based in X" label pattern
      4. Country mention as last resort (helps catch "Canada" vs "USA" mismatch)

    Returns the most specific location found, or empty string if nothing.
    """
    import re

    # Common country names we care about for ITAR/work-auth signal
    COUNTRIES = ["United States", "USA", "Canada", "UK", "United Kingdom",
                  "Germany", "France", "India", "Australia", "Brazil",
                  "Mexico", "Japan", "China", "Singapore", "Israel"]

    # US states (full names) - subset; expand if needed
    US_STATES = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming",
    ]
    # Canadian provinces
    PROVINCES = [
        "Ontario", "Quebec", "British Columbia", "Alberta", "Manitoba",
        "Saskatchewan", "Nova Scotia", "New Brunswick", "Newfoundland",
        "Prince Edward Island",
    ]

    # Tier 1: City, State, Country (LinkedIn header style)
    # e.g. "Kitchener, Ontario, Canada" or "Union City, New Jersey, United States"
    state_pattern = "|".join(re.escape(s) for s in US_STATES + PROVINCES)
    country_pattern = "|".join(re.escape(c) for c in COUNTRIES)
    # Use [^\n,] for city to avoid greedy newline-crossing matches that
    # capture preceding lines like "Senior Software Engineer\nKitchener..."
    full_pattern = re.compile(
        rf'(?:^|\n|\s)([A-Z][^,\n]{{1,40}}?),\s*({state_pattern}),\s*({country_pattern})',
        re.IGNORECASE | re.MULTILINE,
    )
    for text in sources:
        if not text:
            continue
        m = full_pattern.search(text)
        if m:
            return f"{m.group(1).strip()}, {m.group(2).strip()}, {m.group(3).strip()}"[:100]

    # Tier 2: City, State (no country, very common in US resumes)
    short_pattern = re.compile(
        rf'(?:^|\n|\s)([A-Z][^,\n]{{1,40}}?),\s*({state_pattern})\b',
        re.IGNORECASE | re.MULTILINE,
    )
    for text in sources:
        if not text:
            continue
        m = short_pattern.search(text)
        if m:
            return f"{m.group(1).strip()}, {m.group(2).strip()}"[:100]

    # Tier 3: explicit "Location:" / "based in" labels
    label_pattern = re.compile(
        r'(?:location|based\s+in|lives?\s+in)[:\s]+([A-Za-z\s,]+?)(?:\n|\||$)',
        re.IGNORECASE,
    )
    for text in sources:
        if not text:
            continue
        m = label_pattern.search(text)
        if m:
            return m.group(1).strip()[:100]

    # Tier 4: just a country mention (low confidence but useful for cross-border signals)
    country_only = re.compile(rf'\b({country_pattern})\b', re.IGNORECASE)
    for text in sources:
        if not text:
            continue
        m = country_only.search(text)
        if m:
            return m.group(1).strip()[:100]

    return ""


def _extract_candidate_seniority(evaluation: dict) -> dict:
    """Build the seniority vector from AI eval output.

    The AI doesn't emit this directly today. We derive from strengths
    + extracted_skills + risks_to_probe. This is best-effort; a future
    prompt change will have AI emit this directly.
    """
    strengths = evaluation.get("strengths", []) or []
    skills = evaluation.get("extracted_skills", []) or []
    summary = (evaluation.get("summary") or "")

    # Count tech_lead signals from keywords in strengths/summary
    tech_lead = 0
    mgmt = 0
    text_blob = " ".join(strengths) + " " + summary
    text_lower = text_blob.lower()
    if any(k in text_lower for k in ["led", "leads", "leading", "tech lead",
                                       "staff engineer", "architecture decision"]):
        tech_lead += 1
    if any(k in text_lower for k in ["mentor", "mentored", "mentoring",
                                       "coached", "trained junior"]):
        tech_lead += 1
    if any(k in text_lower for k in ["managed", "manager", "direct report",
                                       "team of", "people manager"]):
        mgmt = 1

    # Years extracted from any "X years" mention
    import re
    years_total = 0
    years_in_niche = 0
    m = re.search(r'(\d+)\+?\s*years?', text_blob, re.IGNORECASE)
    if m:
        years_total = int(m.group(1))
        # Default years_in_niche to half of total unless we have better signal
        years_in_niche = years_total // 2 + 1

    # Refine niche years from extracted_skills with depth=expert or production
    senior_skill_count = sum(
        1 for s in skills
        if isinstance(s, dict) and s.get("depth") in ("expert", "production")
    )
    if senior_skill_count >= 5:
        years_in_niche = max(years_in_niche, 5)
    elif senior_skill_count >= 3:
        years_in_niche = max(years_in_niche, 3)

    return {
        "years_total": years_total,
        "years_in_niche": years_in_niche,
        "management_experience": mgmt,
        "tech_lead_signals": tech_lead,
        "scope_level": "team" if mgmt > 0 or tech_lead >= 2 else "individual",
    }


def _extract_ai_proposals(evaluation: dict) -> dict:
    """Pull culture + presentation proposals from AI eval.

    The current CANDIDATE_EVAL_PROMPT doesn't emit explicit culture or
    presentation scores. Derive them as best-effort signals from the
    fit_score and risks_to_probe. A future prompt revision will have
    AI emit these directly.

    Default both to a moderate 3.5 when no clear signal exists.
    """
    fit_score = evaluation.get("fit_score") or 50
    risks = evaluation.get("risks_to_probe", []) or []

    # Culture: start at 3.5, lower if culture-flavored risks present
    culture = 3.5
    culture_risk_keywords = ["startup", "enterprise", "culture", "remote",
                              "in-office", "scrappy", "process", "structured"]
    culture_note = "Default culture score (no specific signals)."
    for r in risks:
        rl = r.lower() if isinstance(r, str) else ""
        if any(k in rl for k in culture_risk_keywords):
            culture = 3.0
            culture_note = f"Culture risk flagged: {r[:80]}"
            break

    # Scale culture up for high-fit candidates
    if fit_score >= 85:
        culture = max(culture, 4.0)
    elif fit_score < 50:
        culture = min(culture, 2.5)

    # Presentation: derive from fit_score + risk count
    presentation = 3.5
    if fit_score >= 85:
        presentation = 4.0
    elif fit_score < 50:
        presentation = 2.5
    if len(risks) >= 4:
        presentation = max(2.0, presentation - 0.5)
    presentation_note = (
        f"Derived from fit_score={fit_score} and {len(risks)} risks_to_probe."
    )

    return {
        "culture_score": culture,
        "culture_note": culture_note,
        "presentation_score": presentation,
        "presentation_note": presentation_note,
    }


async def _load_historical_fill_rate(client, req_id: str) -> dict:
    """Compute historical fill rate from req_outcomes for this user.

    Fill rate definition: filled / (filled + lost).
      - 'filled' = placement landed
      - 'lost'   = req went to another agency or internal hire
      - 'cancelled' = req closed without a real search competition; EXCLUDED
        from denominator because it's an admin event, not a market outcome
      - 'fell_off' = placement reversed; counts as half-filled (placement
        actually happened, just didn't stick)
      - 'reopened' = ignored, it's a state transition not a terminal outcome

    Minimum sample size: returns None if (filled + lost) < 3. Below that,
    the fill rate signal is too noisy to use; engine falls back to neutral
    0.5 weighting. Prevents one cancelled req from making every candidate
    look unfillable.
    """
    # Get user_id from this req
    rs = await client.execute(
        "SELECT user_id FROM requisitions WHERE id = ?", [req_id]
    )
    if not rs.rows:
        return None
    user_id = rs.rows[0][0]

    # Count outcomes by type for this user
    rs = await client.execute(
        """SELECT outcome, COUNT(*) FROM req_outcomes
           WHERE logged_by_user_id = ?
           GROUP BY outcome""",
        [user_id],
    )
    counts = {r[0]: int(r[1]) for r in rs.rows}

    filled = counts.get("filled", 0)
    lost = counts.get("lost", 0)
    fell_off = counts.get("fell_off", 0)
    placeable_total = filled + lost + fell_off

    # Need at least 3 placeable outcomes for the signal to be meaningful
    if placeable_total < 3:
        return None

    # Count fell_off as half-credit (the placement did happen, just didn't stick)
    effective_fills = filled + (0.5 * fell_off)
    fill_rate = effective_fills / placeable_total

    return {
        "fill_rate": fill_rate,
        "n_outcomes": placeable_total,
        "counts_by_type": counts,
    }


# ============================================================
# Taxonomy resolution + skill writes
# ============================================================

def _generate_skill_variants(raw_name: str) -> list:
    """Produce a prioritized list of normalized variants for skill matching.

    Order matters - earlier variants get tried first against the canonical
    taxonomy. Strategy:
      1. The full normalized name (case-folded, whitespace-collapsed)
      2. Strip trailing parentheticals: "Linux (kernel-level)" -> "Linux"
      3. Strip leading phrases like "Experience with", "Proficiency in"
      4. Split on slash: "C/C++" -> "C++", "C"  (note: "C++" tried first
         because it's more specific and the taxonomy has both)
      5. Split on " and " / " or ": "Python and C++" -> "Python", "C++"
      6. Last token alone (catches "TCP/IP Networking" -> "Networking" or
         "ARM Cortex" -> "Cortex" attempts)
    """
    import re
    if not raw_name:
        return []

    variants = []
    seen = set()

    def add(v):
        if not v:
            return
        n = " ".join(v.strip().lower().split())
        if n and n not in seen:
            seen.add(n)
            variants.append(n)

    # 1. As-is
    add(raw_name)

    # 2. Strip trailing parenthetical: "Linux (kernel-level understanding...)"
    no_paren = re.sub(r'\s*\([^)]*\)\s*$', '', raw_name).strip()
    if no_paren != raw_name:
        add(no_paren)

    # 3. Strip leading verb-prepositional phrases
    LEAD_STRIP = re.compile(
        r'^(experience\s+(with|in)|proficiency\s+(with|in)|skilled\s+(with|in)|'
        r'familiar\s+with|knowledge\s+of|expert\s+(with|in)|hands-on\s+(with|in))\s+',
        re.IGNORECASE,
    )
    lead_stripped = LEAD_STRIP.sub('', no_paren or raw_name).strip()
    if lead_stripped:
        add(lead_stripped)

    # 4. Slash splits: "C/C++", "TCP/IP", "I2C/SPI"
    base = lead_stripped or no_paren or raw_name
    if '/' in base:
        # Special case: keep multi-character codes like "TCP/IP" together but
        # also try the parts. Try each side independently.
        parts = [p.strip() for p in base.split('/') if p.strip()]
        # Add longer/more specific parts first
        for p in sorted(parts, key=len, reverse=True):
            add(p)

    # 5. Conjunction splits: "Python and C++", "C or Assembly"
    for sep in [' and ', ' & ', ' or ']:
        if sep.lower() in base.lower():
            parts = re.split(sep, base, flags=re.IGNORECASE)
            for p in parts:
                p = p.strip()
                if p:
                    add(p)

    # 6. Last word fallback (for things like "ARM Cortex" -> "ARM" or
    #    "Linux Kernel" -> "Linux"/"Kernel")
    tokens = re.findall(r'\b[\w+#-]+\b', base)
    if len(tokens) >= 2:
        # Try first and last token
        add(tokens[0])
        add(tokens[-1])

    return variants


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

    # Generate variant forms of the input to try.
    # Examples:
    #   "C/C++" -> ["c/c++", "c++", "c"]
    #   "Linux (kernel-level)" -> ["linux (kernel-level)", "linux"]
    #   "Experience with Python" -> ["experience with python", "python"]
    #   "TCP/IP Networking" -> ["tcp/ip networking", "tcp/ip", "networking"]
    variants = _generate_skill_variants(raw_name)

    # Try each variant against tier 1 (exact canonical match) first - cheap
    # and high-confidence. This catches "C/C++" -> "C" without needing aliases.
    for norm in variants:
        rs = await client.execute(
            "SELECT id FROM skills WHERE LOWER(canonical_name) = ?",
            [norm],
        )
        if rs.rows:
            return rs.rows[0][0]

    # If no exact canonical hits, fall back to original behavior on the
    # primary normalized form (first variant)
    norm = variants[0]

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


    # Fallback path: extract skills from blocker_assessment + preferred_assessment.
    # 
    # 2026-05-11 Phase A finishing fix: write ALL skills the AI inspected, not
    # just "met"/"partial". The previous version dropped "unclear" skills which
    # discarded real signal - e.g. Sandeep Gill's resume listed "Linux Kernel"
    # as his #1 LinkedIn top skill but AI marked it "partial" due to ambiguity
    # about kernel-depth. Either way, he HAS Linux experience, and the engine
    # should see it.
    #
    # We still skip "missing" status because those are confirmed gaps - writing
    # them as candidate_skills would create false matches.
    #
    # Depth + confidence are now status-aware: 'met' gets full credit,
    # 'partial' gets project-depth credit, 'unclear' gets mentioned-depth at
    # low confidence (engine math will weight these accordingly).
    inserted = 0
    DEPTH_BY_STATUS = {
        "met": "production",
        "partial": "project",
        "unclear": "mentioned",
    }
    CONFIDENCE_BY_STATUS = {
        "met": 0.8,
        "partial": 0.55,
        "unclear": 0.3,
    }
    for source_field in ("blocker_assessment", "preferred_assessment"):
        for s in (evaluation.get(source_field) or []):
            if not isinstance(s, dict) or not s.get("skill"):
                continue
            status = (s.get("status") or "").lower()
            # Skip 'missing' (confirmed gap) and any unknown status. Allow met,
            # partial, unclear through.
            if status not in DEPTH_BY_STATUS:
                continue
            sid = await resolve_skill_id(client, s["skill"])
            row_id = "cs_" + uuid.uuid4().hex[:16]
            await client.execute(
                """INSERT INTO candidate_skills
                   (id, candidate_id, skill_id, raw_skill_text, evidence,
                    recency, depth, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row_id, candidate_id, sid, s["skill"],
                    (s.get("evidence") or "")[:500],
                    "current" if status == "met" else "unclear",
                    DEPTH_BY_STATUS[status],
                    CONFIDENCE_BY_STATUS[status],
                ],
            )
            inserted += 1
    return inserted
