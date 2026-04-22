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

# HMAC key for audit chain. Separate from MAGIC_LINK_SECRET in production —
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

    Used for EU AI Act Article 11 (technical documentation) — every score
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
) -> None:
    """Extract whatever dimensional data we can from the current AI eval output
    and write a submission_dimensions row.

    Current AI returns: fit_score, recommendation, blocker_assessment[], etc.
    We map fit_score -> composite_score (0-5 scale) and count blockers.

    When we change the CANDIDATE_EVAL_PROMPT to emit the 8 dimensions explicitly
    (next session), this function gets expanded to store all of them. For now
    we at least record what we have, so the table isn't empty for any eval.
    """
    fit_score = evaluation.get("fit_score")
    if fit_score is None:
        return

    # Normalize the 0-100 AI fit score into the 0-5 rubric scale.
    composite = round((fit_score / 100.0) * 5.0, 2)

    # Count missing blockers — these are the hard "below-3.0 cap" signal per
    # the matching engine spec in modes/_matching-engine.md.
    blockers = evaluation.get("blocker_assessment") or []
    blocker_count = sum(1 for b in blockers if b.get("status") == "missing")

    sd_id = "sd_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT OR REPLACE INTO submission_dimensions
           (id, submission_id, composite_score, blocker_count, match_breakdown_json)
           VALUES (?, ?, ?, ?, ?)""",
        [sd_id, submission_id, composite, blocker_count, json.dumps(evaluation)],
    )
