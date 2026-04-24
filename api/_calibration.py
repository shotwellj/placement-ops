"""
Phase B1: Adjacency calibration from placement outcomes.

Core idea: every submission stage transition produces a signed signal.
Placed = +3, Offer = +2, Onsite = +1, early reject = -1, late reject = -2.
When a signal lands, the adjacencies between the candidate's skills and
the req's skills move slightly toward that signal.

The math is a standard Bayesian dampened update:

    learning_rate = base_rate / (1 + log(1 + sample_count))
    target = signal / 3.0            # normalize to roughly [-1, 1]
    new_weight = clamp(
        old_weight + learning_rate * (target - old_weight),
        0.0, 1.0
    )

    base_rate = 0.3
    sample_count = how many prior events touched this exact pair

This gives us:
    - Fast early adjustment (first 5 outcomes move the needle)
    - Slow steady-state (50th outcome barely moves it)
    - Bounded output (always in [0, 1])
    - Deterministic (same events in any order -> same result within rounding,
      because we replay in chronological order)

We do NOT update in-place. Every run writes to adjacency_history first,
then updates skill_adjacencies. That gives us a rollback path and
an audit trail for EU AI Act Article 12 record-keeping.
"""
from __future__ import annotations

import math
import uuid
from typing import Optional


# ----------------------------------------------------------------------
# Signal weights per stage transition
# ----------------------------------------------------------------------

# Positive signals: candidate made progress in the pipeline
STAGE_SIGNALS = {
    "placed":       +3.0,   # strongest positive: candidate accepted and started
    "offer":        +2.0,   # offer extended (even if not accepted)
    "onsite":       +1.0,   # survived into final-round interview
    "phone_screen": +0.5,   # very weak positive
}

# Negative signals: transition INTO rejected, magnitude depends on
# the FROM stage. Early rejection is a weaker negative (might just
# be a bad fit on paper). Late rejection is stronger (client
# evaluated deeply and declined).
REJECT_SIGNALS = {
    "submitted":    -1.0,
    "phone_screen": -1.5,
    "onsite":       -2.0,
    "offer":        -2.5,   # offer rescinded or candidate rejected offer
}

# Base learning rate. Higher = faster adjustment per event.
# 0.3 gives a reasonable balance: 5 placements move a weight ~40%
# of the way to the signal, 50 placements move it ~85% of the way.
BASE_LEARNING_RATE = 0.3


def signal_for_transition(from_stage: Optional[str], to_stage: str) -> float:
    """Return the signed signal magnitude for a stage transition.

    Returns 0.0 for transitions we don't calibrate on (e.g. withdrew,
    administrative stage changes). Returning 0.0 means the event is
    recorded but produces no adjacency update.
    """
    if to_stage == "rejected":
        return REJECT_SIGNALS.get(from_stage or "submitted", -1.0)
    if to_stage in STAGE_SIGNALS:
        return STAGE_SIGNALS[to_stage]
    return 0.0


def dampened_update(
    old_weight: float,
    signal: float,
    sample_count: int,
    base_rate: float = BASE_LEARNING_RATE,
) -> float:
    """Apply one Bayesian-dampened update to an adjacency weight.

    Pure function. No DB. Easy to unit-test.

    Args:
        old_weight: current adjacency weight in [0, 1]
        signal: signed signal in roughly [-3, +3]
        sample_count: how many prior events have touched this pair
        base_rate: initial learning rate (default 0.3)

    Returns:
        new weight, clamped to [0.0, 1.0]
    """
    # Learning rate shrinks as more events accumulate. Standard Bayesian
    # dampening: first events move the needle fast, later events barely do.
    learning_rate = base_rate / (1.0 + math.log1p(max(0, sample_count)))

    # Map signal to target in [0, 1]:
    #   +3.0 (placed)  -> target 1.0  (pull adjacency UP toward 1.0)
    #    0.0 (neutral) -> target 0.5
    #   -3.0 (reject)  -> target 0.0  (pull adjacency DOWN toward 0.0)
    target = max(0.0, min(1.0, 0.5 + signal / 6.0))

    new_weight = old_weight + learning_rate * (target - old_weight)
    return max(0.0, min(1.0, new_weight))


# ----------------------------------------------------------------------
# DB-bound helpers (used by the calibration run)
# ----------------------------------------------------------------------

async def record_calibration_event(
    client,
    user_id: str,
    submission_id: str,
    req_id: Optional[str],
    from_stage: Optional[str],
    to_stage: str,
    reason: Optional[str] = None,
    audit_event_id: Optional[str] = None,
) -> str:
    """Insert a calibration_events row. Idempotent on (submission_id, to_stage)
    only in the sense that duplicate stage transitions are tolerated — they
    simply produce multiple events, which the run will process fairly because
    sample_count grows. Returns the new event id.
    """
    event_id = "ce_" + uuid.uuid4().hex[:16]
    weight = signal_for_transition(from_stage, to_stage)
    event_type = "placed" if to_stage == "placed" else (
        "rejected" if to_stage == "rejected" else "stage_advance"
    )
    await client.execute(
        """INSERT INTO calibration_events
           (id, user_id, submission_id, req_id, event_type, reason,
            from_stage, to_stage, event_weight, audit_event_id, processed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        [event_id, user_id, submission_id, req_id, event_type, reason,
         from_stage, to_stage, weight, audit_event_id],
    )
    return event_id


async def _load_pair_sample_count(client, skill_a: str, skill_b: str) -> int:
    """How many prior events touched this exact (skill_a, skill_b) pair?

    We read from adjacency_history. If no history exists, count is 0 and the
    weight came from the taxonomy seed.
    """
    rs = await client.execute(
        """SELECT COUNT(*) FROM adjacency_history
           WHERE (skill_id = ? AND adjacent_id = ?)
              OR (skill_id = ? AND adjacent_id = ?)""",
        [skill_a, skill_b, skill_b, skill_a],
    )
    if rs.rows and rs.rows[0]:
        return int(rs.rows[0][0] or 0)
    return 0


async def _load_current_weight(client, skill_a: str, skill_b: str) -> tuple[float, str]:
    """Return (weight, source) for the adjacency between two skills.

    If no row exists in skill_adjacencies, returns (0.0, 'taxonomy') which
    is the starting point for a previously-unrelated pair.
    """
    rs = await client.execute(
        """SELECT weight, source FROM skill_adjacencies
           WHERE skill_id = ? AND adjacent_id = ?""",
        [skill_a, skill_b],
    )
    if rs.rows and rs.rows[0]:
        return (float(rs.rows[0][0]), str(rs.rows[0][1]))
    return (0.0, "taxonomy")


async def _write_adjacency(
    client,
    skill_a: str,
    skill_b: str,
    new_weight: float,
    source: str,
) -> None:
    """Upsert a skill_adjacencies row with the new weight + source.

    Always writes the symmetric pair (A->B and B->A). Keeps consistency
    with how seed_taxonomy.py populates the table.
    """
    now_sql = "CURRENT_TIMESTAMP"
    for a, b in [(skill_a, skill_b), (skill_b, skill_a)]:
        existing = await client.execute(
            "SELECT id FROM skill_adjacencies WHERE skill_id = ? AND adjacent_id = ?",
            [a, b],
        )
        if existing.rows and existing.rows[0]:
            await client.execute(
                f"UPDATE skill_adjacencies SET weight = ?, source = ?, "
                f"updated_at = {now_sql} WHERE skill_id = ? AND adjacent_id = ?",
                [new_weight, source, a, b],
            )
        else:
            row_id = "sa_" + uuid.uuid4().hex[:16]
            await client.execute(
                """INSERT INTO skill_adjacencies
                   (id, skill_id, adjacent_id, weight, source)
                   VALUES (?, ?, ?, ?, ?)""",
                [row_id, a, b, new_weight, source],
            )


async def run_calibration(
    client,
    triggered_by_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Process all unprocessed calibration_events and update adjacency weights.

    Returns a summary dict:
        {
            "run_id": "cr_...",
            "events_processed": N,
            "pairs_updated": M,
        }

    Design:
      1. Create a calibration_runs row (run_id) so every change ties to it
      2. Load all unprocessed events in chronological order
      3. For each event:
           - Join candidate_skills.skill_id against req_skills.skill_id
           - For each (candidate_skill, req_skill) pair where the two
             skills are different:
               - Load current weight + sample count
               - Compute new weight via dampened_update
               - Write adjacency_history (audit)
               - Write skill_adjacencies (live weight)
      4. Mark events processed = 1
      5. Close calibration_runs.completed_at

    Safe to re-run at any time. Processed events won't be re-applied.
    """
    run_id = "cr_" + uuid.uuid4().hex[:16]
    await client.execute(
        """INSERT INTO calibration_runs
           (id, triggered_by_user_id, learning_rate_used, notes)
           VALUES (?, ?, ?, ?)""",
        [run_id, triggered_by_user_id, BASE_LEARNING_RATE, notes],
    )

    events_rs = await client.execute(
        """SELECT id, submission_id, req_id, from_stage, to_stage, event_weight
           FROM calibration_events
           WHERE processed = 0 AND event_weight != 0.0
           ORDER BY created_at ASC"""
    )

    pairs_updated = 0
    events_processed = 0

    for ev in (events_rs.rows or []):
        event_id, submission_id, req_id, from_stage, to_stage, signal = ev
        signal = float(signal or 0.0)
        if signal == 0.0:
            # Defensive: already filtered in query, but guard anyway
            continue

        # Resolve candidate_id from submission
        sub_rs = await client.execute(
            "SELECT candidate_id, req_id FROM submissions WHERE id = ?",
            [submission_id],
        )
        if not sub_rs.rows or not sub_rs.rows[0]:
            continue
        candidate_id = sub_rs.rows[0][0]
        # Prefer denormalized req_id from the event; fall back to submission's
        effective_req_id = req_id or sub_rs.rows[0][1]
        if not effective_req_id:
            continue

        # Load candidate's resolved skills
        cand_rs = await client.execute(
            """SELECT DISTINCT skill_id FROM candidate_skills
               WHERE candidate_id = ? AND skill_id IS NOT NULL""",
            [candidate_id],
        )
        cand_skills = [r[0] for r in (cand_rs.rows or []) if r and r[0]]

        # Load req's resolved skills
        req_rs = await client.execute(
            """SELECT DISTINCT skill_id FROM req_skills
               WHERE req_id = ? AND skill_id IS NOT NULL""",
            [effective_req_id],
        )
        req_skills = [r[0] for r in (req_rs.rows or []) if r and r[0]]

        # Update every (candidate_skill, req_skill) pair where they differ
        for cs in cand_skills:
            for rs in req_skills:
                if cs == rs:
                    continue  # same skill — not an adjacency
                # Canonicalize ordering for history lookup
                a, b = (cs, rs) if cs < rs else (rs, cs)
                sample_count = await _load_pair_sample_count(client, a, b)
                old_weight, old_source = await _load_current_weight(client, a, b)
                new_weight = dampened_update(old_weight, signal, sample_count)
                new_source = "calibrated"

                # Write history first (audit trail)
                hist_id = "ah_" + uuid.uuid4().hex[:16]
                await client.execute(
                    """INSERT INTO adjacency_history
                       (id, run_id, skill_id, adjacent_id,
                        old_weight, new_weight, sample_count_before,
                        source_before, source_after)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [hist_id, run_id, a, b,
                     old_weight, new_weight, sample_count,
                     old_source, new_source],
                )
                # Then update the live weight
                await _write_adjacency(client, a, b, new_weight, new_source)
                pairs_updated += 1

        # Mark event as processed
        await client.execute(
            "UPDATE calibration_events SET processed = 1 WHERE id = ?",
            [event_id],
        )
        events_processed += 1

    # Close run
    await client.execute(
        """UPDATE calibration_runs
           SET events_processed = ?, pairs_updated = ?, completed_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        [events_processed, pairs_updated, run_id],
    )

    return {
        "run_id": run_id,
        "events_processed": events_processed,
        "pairs_updated": pairs_updated,
    }
