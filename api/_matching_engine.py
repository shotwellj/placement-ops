"""Phase A finishing: deterministic matching engine.

Scope (Option 2, 2026-05-11): only Technical Match (Dim 1) and Gap Severity
(Dim 6) of the 8-dimension rubric are computed here. The other 6 dimensions
(Seniority Fit, Location, Comp, Culture, Presentation, Fill Probability)
stay AI-scored for now and will be migrated in future phases.

This module is designed to be called from ANY lifecycle stage, not just
candidate evaluation:
  - Source mode: rank candidates against a req
  - Match mode (future): batch ranking
  - Interview mode (future): score interview rubric outputs
  - Offer mode (future): predict acceptance from comp gap + fill probability
  - Retain mode (future): score competency drift on roster
  - Develop mode (future): map skill gaps to training pathways

Public interface:
  - score_technical_match(req_skills, cand_skills, adjacencies) -> dict
  - score_gap_severity(req_skills, cand_skills, adjacencies) -> dict
  - detect_blockers(req_skills, skill_matches) -> list
  - apply_composite_threshold(composite, has_blockers) -> str
  - evaluate_skills(...) -> full deterministic skill scoring output

Acceptance: same inputs always produce identical outputs (within float
rounding). This is the deterministic guarantee that Phase B1 calibration
needs to produce visible scoring differences.

Math reference: modes/_matching-engine.md and modes/_shared.md.

EU AI Act Article 13 compliance: every score includes a math breakdown
that can be audited. No hidden weights, no LLM intermediaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ----------------------------------------------------------------------
# Constants (from modes/_matching-engine.md and modes/_shared.md)
# ----------------------------------------------------------------------

# Importance weights (Step 1 in matching-engine.md)
IMPORTANCE_WEIGHTS = {
    "blocker": 1.0,        # internal name for "required" in our schema
    "required": 1.0,
    "preferred": 0.6,
    "nice_to_have": 0.3,
    "nice-to-have": 0.3,
}

# Recency decay (Step 2)
RECENCY_WEIGHTS = {
    "current": 1.0,
    "recent": 0.8,
    "dated": 0.5,
    "unclear": 0.5,        # be charitable but not generous
}

# Depth multiplier (Step 2)
DEPTH_WEIGHTS = {
    "expert": 1.0,
    "production": 0.9,
    "project": 0.7,
    "mentioned": 0.4,
    "not found": 0.0,      # AI sometimes returns this string
    "unclear": 0.4,
}

# Match type weights (Step 3)
MATCH_TYPE_WEIGHTS = {
    "exact": 1.0,
    "alias": 1.0,
    "adjacent": 0.6,       # default; actual weight comes from skill_adjacencies.weight
    "parent": 0.3,
    "none": 0.0,
}

# Composite thresholds (from _shared.md)
THRESHOLDS = [
    (4.5, "Strong Submit"),
    (4.0, "Submit"),
    (3.5, "Maybe"),
    (3.0, "Pass"),
    (0.0, "Hard Pass"),
]

# Blocker rule: any required skill with score 0.0 and no adjacent match
# caps the composite at 3.0 max (PASS territory)
BLOCKER_CAP = 3.0


# ----------------------------------------------------------------------
# Data shapes
# ----------------------------------------------------------------------

@dataclass
class SkillMatch:
    """The result of matching ONE requirement against the candidate's skills."""
    requirement_text: str               # raw req skill text
    requirement_skill_id: Optional[str] # canonical skill id if resolved
    importance: str                     # blocker | preferred | nice_to_have
    matched_candidate_text: Optional[str]   # raw candidate skill text that matched
    matched_candidate_skill_id: Optional[str]
    match_type: str                     # exact | alias | adjacent | parent | none
    match_weight: float                 # the actual taxonomy weight applied
    recency: str
    depth: str
    raw_score: float                    # match_weight × recency × depth × importance
    is_blocker: bool                    # required + raw_score == 0 + no adjacent
    rationale: str                      # human-readable explanation of this score


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _normalize(text: Optional[str]) -> str:
    """Same normalization as api/_skill_resolution.py."""
    if text is None:
        return ""
    return text.strip().lower()


def _importance_weight(importance: str) -> float:
    return IMPORTANCE_WEIGHTS.get((importance or "").lower(), 0.6)


def _recency_weight(recency: str) -> float:
    return RECENCY_WEIGHTS.get((recency or "").lower(), 0.5)


def _depth_weight(depth: str) -> float:
    return DEPTH_WEIGHTS.get((depth or "").lower(), 0.4)


# ----------------------------------------------------------------------
# Core matching: one requirement against the candidate's skill set
# ----------------------------------------------------------------------

def match_one_requirement(
    requirement: dict,
    candidate_skills: list[dict],
    adjacency_index: dict,
) -> SkillMatch:
    """Score one req requirement against the candidate's skill set.

    Inputs (dict shapes match the DB tables):
      requirement: {
        'raw_skill_text': str,
        'skill_id': Optional[str],
        'importance': 'blocker' | 'preferred' | 'nice_to_have'
      }
      candidate_skills: list of {
        'raw_skill_text': str,
        'skill_id': Optional[str],
        'recency': str,
        'depth': str,
      }
      adjacency_index: dict mapping skill_id -> {adjacent_id: weight}
        Built once per evaluation from skill_adjacencies query.

    Returns a SkillMatch with full math breakdown.
    """
    req_text = requirement.get("raw_skill_text", "")
    req_text_norm = _normalize(req_text)
    req_skill_id = requirement.get("skill_id")
    importance = (requirement.get("importance") or "preferred").lower()
    importance_w = _importance_weight(importance)

    # Track the best match found so far. We iterate all candidate skills
    # and keep the one with the highest raw_score.
    best: Optional[SkillMatch] = None

    for cs in candidate_skills:
        cs_text = cs.get("raw_skill_text", "")
        cs_text_norm = _normalize(cs_text)
        cs_skill_id = cs.get("skill_id")
        recency = (cs.get("recency") or "unclear").lower()
        depth = (cs.get("depth") or "mentioned").lower()
        recency_w = _recency_weight(recency)
        depth_w = _depth_weight(depth)

        match_type = "none"
        match_weight = 0.0

        # Exact match by canonical skill_id (both sides resolved to same skill)
        if req_skill_id and cs_skill_id and req_skill_id == cs_skill_id:
            match_type = "exact"
            match_weight = 1.0
        # Exact match by normalized raw text (fallback when skill_id missing on one side)
        elif req_text_norm and cs_text_norm and req_text_norm == cs_text_norm:
            match_type = "exact"
            match_weight = 1.0
        # Adjacent match via skill_adjacencies table
        elif req_skill_id and cs_skill_id:
            adj_weights = adjacency_index.get(req_skill_id, {})
            if cs_skill_id in adj_weights:
                match_type = "adjacent"
                match_weight = adj_weights[cs_skill_id]
            else:
                # Check reverse direction (adjacency is bidirectional)
                reverse_weights = adjacency_index.get(cs_skill_id, {})
                if req_skill_id in reverse_weights:
                    match_type = "adjacent"
                    match_weight = reverse_weights[req_skill_id]

        raw_score = match_weight * recency_w * depth_w * importance_w

        if best is None or raw_score > best.raw_score:
            rationale = (
                f"match_type={match_type} ({match_weight:.2f}) × "
                f"recency={recency} ({recency_w:.2f}) × "
                f"depth={depth} ({depth_w:.2f}) × "
                f"importance={importance} ({importance_w:.2f}) "
                f"= {raw_score:.3f}"
            )
            best = SkillMatch(
                requirement_text=req_text,
                requirement_skill_id=req_skill_id,
                importance=importance,
                matched_candidate_text=cs_text if match_type != "none" else None,
                matched_candidate_skill_id=cs_skill_id if match_type != "none" else None,
                match_type=match_type,
                match_weight=match_weight,
                recency=recency,
                depth=depth,
                raw_score=raw_score,
                is_blocker=False,  # determined later in detect_blockers
                rationale=rationale,
            )

    # No candidate skills at all (or no matches found)
    if best is None:
        best = SkillMatch(
            requirement_text=req_text,
            requirement_skill_id=req_skill_id,
            importance=importance,
            matched_candidate_text=None,
            matched_candidate_skill_id=None,
            match_type="none",
            match_weight=0.0,
            recency="unclear",
            depth="not found",
            raw_score=0.0,
            is_blocker=False,
            rationale=f"No candidate skill matched requirement '{req_text}'.",
        )

    return best


# ----------------------------------------------------------------------
# Blocker detection (Step 6 in matching-engine.md)
# ----------------------------------------------------------------------

def detect_blockers(skill_matches: list[SkillMatch]) -> list[SkillMatch]:
    """Mark required skills with score 0 and no adjacent match as blockers.

    Per modes/_matching-engine.md Step 6:
      - A `required` skill with score 0.0 and no adjacent match = BLOCKER
      - A `required` skill with adjacent match (score 0.3-0.6) = mitigatable
      - A `preferred` or `nice-to-have` skill = never a blocker

    Mutates the SkillMatch.is_blocker field in place and returns the list
    of just the blockers (for convenience).
    """
    blockers = []
    for sm in skill_matches:
        # Only required (or its alias 'blocker') skills can be blockers
        if sm.importance not in ("blocker", "required"):
            continue
        # If raw_score is 0 AND match_type is 'none' (no adjacent at all)
        if sm.raw_score == 0.0 and sm.match_type == "none":
            sm.is_blocker = True
            blockers.append(sm)
    return blockers


# ----------------------------------------------------------------------
# Dimension 1: Technical Match
# ----------------------------------------------------------------------

def score_technical_match(skill_matches: list[SkillMatch]) -> dict:
    """Compute Dimension 1 (Technical Match) on the 1-5 scale.

    Per modes/_matching-engine.md Step 4:
      technical_score = sum(skill_scores) / sum(max_possible_scores) × 5.0

    The max possible score for each requirement is just the importance
    weight (because a perfect match has match_type=1.0, recency=1.0,
    depth=1.0, importance=W -> W).
    """
    if not skill_matches:
        return {
            "score": 0.0,
            "raw_sum": 0.0,
            "max_sum": 0.0,
            "ratio": 0.0,
            "breakdown": [],
            "note": "No requirements to score.",
        }

    raw_sum = sum(sm.raw_score for sm in skill_matches)
    max_sum = sum(_importance_weight(sm.importance) for sm in skill_matches)

    if max_sum == 0:
        ratio = 0.0
    else:
        ratio = raw_sum / max_sum

    # Scale to 1-5
    score = ratio * 5.0
    # Cap at 5.0 (defensive, shouldn't happen)
    score = min(score, 5.0)

    return {
        "score": round(score, 2),
        "raw_sum": round(raw_sum, 3),
        "max_sum": round(max_sum, 3),
        "ratio": round(ratio, 3),
        "breakdown": [asdict(sm) for sm in skill_matches],
        "note": (
            f"Technical Match = (sum of skill scores {raw_sum:.2f}) / "
            f"(sum of max possible {max_sum:.2f}) × 5.0 = {score:.2f}"
        ),
    }


# ----------------------------------------------------------------------
# Dimension 6: Gap Severity
# ----------------------------------------------------------------------

def score_gap_severity(skill_matches: list[SkillMatch]) -> dict:
    """Compute Dimension 6 (Gap Severity) on the 1-5 scale.

    Per modes/_shared.md:
      5 = No meaningful gaps
      4 = Gaps are nice-to-haves, not hard requirements
      3 = 1-2 gaps that need a mitigation story in the cover memo
      2 = Multiple hard-requirement gaps
      1 = Core skill missing, not positionable

    Algorithm: count gaps by severity, derive score.
      - A "gap" is any requirement with raw_score < 0.5
      - "hard gap" = blocker/required gap (importance blocker or required)
      - "soft gap" = preferred or nice-to-have gap

    Score mapping:
      - 0 hard gaps, 0 soft gaps -> 5.0
      - 0 hard gaps, 1-2 soft gaps -> 4.5
      - 0 hard gaps, 3+ soft gaps -> 4.0
      - 1 hard gap (mitigatable, raw_score 0.3-0.5) -> 3.5
      - 1 hard gap (full, raw_score < 0.3) -> 3.0
      - 2+ hard gaps (any) -> 2.0
      - blocker present (is_blocker=True) -> 1.0
    """
    hard_full = 0      # blocker/required, raw_score < 0.3 (and not is_blocker)
    hard_mitigatable = 0  # blocker/required, raw_score 0.3-0.5
    soft = 0           # preferred / nice_to_have, raw_score < 0.5
    blockers = 0

    for sm in skill_matches:
        if sm.is_blocker:
            blockers += 1
            continue
        if sm.raw_score >= 0.5:
            continue  # not a gap
        if sm.importance in ("blocker", "required"):
            if sm.raw_score >= 0.3:
                hard_mitigatable += 1
            else:
                hard_full += 1
        else:
            soft += 1

    total_hard = hard_full + hard_mitigatable

    if blockers > 0:
        score = 1.0
        note = f"{blockers} blocker(s) present -> Gap Severity = 1.0"
    elif total_hard >= 2:
        score = 2.0
        note = f"{total_hard} hard-requirement gaps -> Gap Severity = 2.0"
    elif hard_full == 1:
        score = 3.0
        note = "1 hard-requirement gap with no adjacent credit -> Gap Severity = 3.0"
    elif hard_mitigatable == 1:
        score = 3.5
        note = "1 hard-requirement gap with adjacent credit -> Gap Severity = 3.5"
    elif soft >= 3:
        score = 4.0
        note = f"{soft} soft gaps -> Gap Severity = 4.0"
    elif soft >= 1:
        score = 4.5
        note = f"{soft} soft gap(s) -> Gap Severity = 4.5"
    else:
        score = 5.0
        note = "No meaningful gaps -> Gap Severity = 5.0"

    return {
        "score": round(score, 2),
        "blockers": blockers,
        "hard_gaps_full": hard_full,
        "hard_gaps_mitigatable": hard_mitigatable,
        "soft_gaps": soft,
        "note": note,
    }


# ----------------------------------------------------------------------
# Composite + threshold (used by full eval flow)
# ----------------------------------------------------------------------

def apply_composite_threshold(composite: float, has_blockers: bool) -> str:
    """Map a composite score to a recommendation per _shared.md.

    Composite >= 4.5 -> Strong Submit
    Composite 4.0-4.4 -> Submit
    Composite 3.5-3.9 -> Maybe
    Composite 3.0-3.4 -> Pass
    Composite < 3.0 -> Hard Pass

    Blocker rule (matching-engine.md Step 6): if any blocker exists,
    composite is capped at 3.0 BEFORE this function is called. So a
    composite >= 4.0 with has_blockers=True is impossible by construction.
    But we defensively cap here too in case the caller forgot.
    """
    effective = min(composite, BLOCKER_CAP) if has_blockers else composite
    for threshold, label in THRESHOLDS:
        if effective >= threshold:
            return label
    return "Hard Pass"


# ----------------------------------------------------------------------
# Top-level: evaluate technical + gap dimensions deterministically
# ----------------------------------------------------------------------

def evaluate_skills(
    req_skills: list[dict],
    candidate_skills: list[dict],
    adjacency_index: dict,
) -> dict:
    """Run the deterministic 2-dimension scoring for one candidate against
    one req's skill set.

    Returns a dict with:
      - skill_matches: list of SkillMatch as dicts
      - blockers: list of blocker SkillMatch as dicts
      - blocker_count: int
      - technical_match: dict (Dim 1 score + breakdown)
      - gap_severity: dict (Dim 6 score + breakdown)

    The caller (typically /api/source/evaluate) is responsible for:
      - Getting the AI to extract candidate_skills from the resume
      - Loading req_skills from the DB
      - Building adjacency_index from skill_adjacencies query
      - Calling AI for the other 6 dimensions (Seniority, Location, Comp,
        Culture, Presentation, Fill Probability) and combining everything
        into the final composite_score
    """
    # Score each requirement against the candidate's skill set
    skill_matches = [
        match_one_requirement(req, candidate_skills, adjacency_index)
        for req in req_skills
    ]

    # Detect blockers (mutates is_blocker on SkillMatch)
    blockers = detect_blockers(skill_matches)

    # Compute Dim 1 (Technical Match)
    technical = score_technical_match(skill_matches)

    # Compute Dim 6 (Gap Severity)
    gaps = score_gap_severity(skill_matches)

    return {
        "skill_matches": [asdict(sm) for sm in skill_matches],
        "blockers": [asdict(b) for b in blockers],
        "blocker_count": len(blockers),
        "technical_match": technical,
        "gap_severity": gaps,
    }


# ----------------------------------------------------------------------
# Helper: build adjacency index from skill_adjacencies query rows
# ----------------------------------------------------------------------

def build_adjacency_index(rows: list[tuple]) -> dict:
    """Build {skill_id: {adjacent_id: weight}} index from query rows.

    Expected row shape: (skill_id, adjacent_id, weight)
    Adjacency is treated as bidirectional even though the table only
    stores it one direction; we mirror it here.
    """
    index: dict = {}
    for row in rows:
        if len(row) < 3:
            continue
        sid, aid, w = row[0], row[1], float(row[2])
        if sid is None or aid is None:
            continue
        index.setdefault(sid, {})[aid] = w
        # Mirror for reverse lookup
        index.setdefault(aid, {})[sid] = w
    return index
