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


# ====================================================================
# Phase A finishing - the remaining 6 dimensions
# 2026-05-11: complete the deterministic engine
# ====================================================================

# 8-dimension weights from modes/_shared.md
DIMENSION_WEIGHTS = {
    "technical_match": 0.25,
    "seniority_fit": 0.15,
    "location_alignment": 0.10,
    "comp_alignment": 0.15,
    "culture_signals": 0.05,
    "gap_severity": 0.10,
    "presentation_risk": 0.10,
    "fill_probability": 0.10,
}


# --------------------------------------------------------------------
# Dim 3: Location / Remote Alignment
# --------------------------------------------------------------------

def score_location_fit(req_location: str, req_remote_policy: str,
                       candidate_location: str) -> dict:
    """Pure data lookup. From modes/_shared.md:
      5 = Exact match (lives in the city, or role is fully remote)
      4 = Willing to relocate or hybrid-compatible
      3 = Remote but client prefers hybrid, needs conversation
      2 = Different country / timezone mismatch
      1 = No path to making location work

    Inputs are strings. Empty/None handled gracefully.
    """
    rl = (req_location or "").strip().lower()
    rp = (req_remote_policy or "").strip().lower()
    cl = (candidate_location or "").strip().lower()

    # No data on either side - default to neutral 3
    if not rl and not rp:
        return {"score": 3.0, "note": "No location data on req. Defaulting to neutral."}
    if not cl:
        return {"score": 3.0, "note": "No location data on candidate. Defaulting to neutral."}

    # Fully remote roles - location doesn't matter
    if "remote" in rp and "hybrid" not in rp and "office" not in rp:
        return {"score": 5.0, "note": "Role is fully remote. Location is not a constraint."}

    # Exact city match
    if rl and cl and rl in cl or cl in rl:
        return {"score": 5.0, "note": f"Candidate location matches req location ({rl})."}

    # Hybrid role with remote candidate
    if "hybrid" in rp and "remote" in cl:
        return {"score": 3.0, "note": "Role is hybrid, candidate is remote. Needs conversation."}

    # Different country detection (cheap heuristic on common country names)
    countries = ["usa", "united states", "uk", "united kingdom", "canada",
                 "india", "germany", "france", "australia", "brazil"]
    req_country = next((c for c in countries if c in rl), None)
    cand_country = next((c for c in countries if c in cl), None)
    if req_country and cand_country and req_country != cand_country:
        return {"score": 2.0,
                "note": f"Different countries ({req_country} vs {cand_country})."}

    # Default: assume some friction but bridgeable
    return {"score": 3.5, "note": f"Different location ({rl} vs {cl}). May need relocation."}


# --------------------------------------------------------------------
# Dim 4: Compensation Alignment
# --------------------------------------------------------------------

def _parse_comp_range(comp_str: str) -> tuple:
    """Parse a comp range string like '$120k-$160k' or '120000-160000'.
    Returns (low, high) as ints, or (None, None) if unparseable.
    """
    import re
    if not comp_str:
        return (None, None)
    # Find all dollar amounts
    nums = re.findall(r'\$?\s*(\d+(?:,\d{3})*)\s*[kK]?', comp_str)
    parsed = []
    for n in nums:
        n_clean = n.replace(",", "")
        try:
            val = int(n_clean)
            # If 'k' suffix in original text near this number, multiply
            if "k" in comp_str.lower() and val < 10000:
                val *= 1000
            parsed.append(val)
        except ValueError:
            continue
    if len(parsed) >= 2:
        return (min(parsed), max(parsed))
    if len(parsed) == 1:
        return (parsed[0], parsed[0])
    return (None, None)


def score_comp_alignment(req_comp_range: str, candidate_expected_comp: str) -> dict:
    """Pure data math. From modes/_shared.md:
      5 = Candidate expectations within role budget
      4 = Within 10% - negotiable
      3 = 10-20% gap - needs expectation management
      2 = 20-35% gap - unlikely without adjustment
      1 = 35%+ gap - don't waste anyone's time

    Inputs are strings (e.g. '$120k-$160k' for req, '$140k' for candidate).
    Returns score + note + parsed values for transparency.
    """
    req_low, req_high = _parse_comp_range(req_comp_range)
    cand_low, cand_high = _parse_comp_range(candidate_expected_comp)

    if not req_low or not cand_low:
        return {
            "score": 3.0,
            "note": "Insufficient comp data on one side. Defaulting to neutral.",
            "req_range": (req_low, req_high),
            "candidate_range": (cand_low, cand_high),
        }

    # Use midpoints for comparison
    req_mid = (req_low + req_high) / 2
    cand_mid = (cand_low + cand_high) / 2

    # If candidate expectation falls inside req range, perfect match
    if req_low <= cand_mid <= req_high:
        return {
            "score": 5.0,
            "note": f"Candidate expectation (${cand_mid:,.0f}) fits within req range "
                    f"(${req_low:,.0f}-${req_high:,.0f}).",
            "req_range": (req_low, req_high),
            "candidate_range": (cand_low, cand_high),
        }

    # Compute gap as percentage
    gap_pct = abs(cand_mid - req_mid) / req_mid

    if gap_pct < 0.10:
        score = 4.0
        note = f"Comp gap {gap_pct*100:.1f}% - negotiable."
    elif gap_pct < 0.20:
        score = 3.0
        note = f"Comp gap {gap_pct*100:.1f}% - needs expectation management."
    elif gap_pct < 0.35:
        score = 2.0
        note = f"Comp gap {gap_pct*100:.1f}% - unlikely without adjustment."
    else:
        score = 1.0
        note = f"Comp gap {gap_pct*100:.1f}% - too large to bridge."

    return {
        "score": score,
        "note": note,
        "req_range": (req_low, req_high),
        "candidate_range": (cand_low, cand_high),
        "gap_pct": round(gap_pct, 3),
    }


# --------------------------------------------------------------------
# Dim 2: Seniority Fit
# --------------------------------------------------------------------

def score_seniority_fit(req_signals: list, candidate_signals: dict) -> dict:
    """Hybrid: AI extracts the signals, code does the matching.

    req_signals: list of strings from the JD like ['5+ years', 'lead a team',
        'mentor juniors']
    candidate_signals: dict with keys:
      years_total (int), years_in_niche (int), management_experience (int),
      tech_lead_signals (int), scope_level ('individual'|'team'|'org'|'company')

    Per modes/_matching-engine.md Step 5, each JD signal is checked against
    the candidate's vector. Score = avg of all checks * 5.0.
    """
    if not req_signals:
        return {"score": 3.5, "note": "No seniority signals in JD. Defaulting to slight positive."}

    if not candidate_signals or not isinstance(candidate_signals, dict):
        return {"score": 2.5, "note": "No candidate seniority data extracted."}

    years = candidate_signals.get("years_total", 0) or 0
    years_niche = candidate_signals.get("years_in_niche", 0) or 0
    mgmt = candidate_signals.get("management_experience", 0) or 0
    tech_lead = candidate_signals.get("tech_lead_signals", 0) or 0

    checks = []
    for sig in req_signals:
        s = sig.lower()
        # Extract numeric years requirement
        import re
        years_match = re.search(r'(\d+)\+?\s*years?', s)
        if years_match:
            required = int(years_match.group(1))
            if years_niche >= required:
                checks.append((sig, 1.0, f"{years_niche}yr niche >= {required}yr required"))
            elif years_niche >= required - 1:
                checks.append((sig, 0.6, f"{years_niche}yr niche near {required}yr required"))
            else:
                checks.append((sig, 0.0, f"{years_niche}yr niche < {required}yr required"))
        elif "lead" in s or "manag" in s:
            if mgmt > 0 or tech_lead >= 2:
                checks.append((sig, 1.0, f"mgmt={mgmt}, tech_lead={tech_lead} satisfies leadership signal"))
            elif tech_lead >= 1:
                checks.append((sig, 0.6, f"tech_lead={tech_lead} partial match for leadership"))
            else:
                checks.append((sig, 0.0, "No leadership signals"))
        elif "mentor" in s:
            if tech_lead >= 1:
                checks.append((sig, 1.0, f"tech_lead={tech_lead} satisfies mentorship signal"))
            else:
                checks.append((sig, 0.3, "Mentorship signal unclear"))
        else:
            # Unrecognized signal, give neutral credit
            checks.append((sig, 0.5, "Signal not pattern-matched"))

    if not checks:
        return {"score": 3.0, "note": "No signals matched any pattern."}

    avg = sum(c[1] for c in checks) / len(checks)
    score = round(avg * 5.0, 2)

    return {
        "score": score,
        "note": f"Seniority Fit = avg({len(checks)} signal checks) × 5 = {score}",
        "checks": [{"signal": c[0], "weight": c[1], "rationale": c[2]} for c in checks],
    }


# --------------------------------------------------------------------
# Dim 5: Culture Signals (AI-proposed, code-validated)
# --------------------------------------------------------------------

def score_culture_signals(ai_proposed_score: float, ai_note: str = "") -> dict:
    """AI emits a 1-5 culture score and a rationale. Code validates the
    range and structure. We don't try to do this in pure code because
    'startup person vs enterprise person' is genuinely qualitative.

    From modes/_shared.md:
      5 = Strong alignment (startup person for startup, enterprise for enterprise)
      4 = Likely compatible, minor unknowns
      3 = Neutral - insufficient data
      2 = Some red flags
      1 = Clear mismatch
    """
    try:
        score = float(ai_proposed_score) if ai_proposed_score is not None else 3.0
    except (ValueError, TypeError):
        score = 3.0
    # Clamp to valid range
    score = max(1.0, min(5.0, score))
    return {
        "score": round(score, 2),
        "note": ai_note or f"AI-proposed culture score = {score}",
        "source": "ai_validated",
    }


# --------------------------------------------------------------------
# Dim 7: Presentation Risk (AI-proposed, code-validated)
# --------------------------------------------------------------------

def score_presentation_risk(ai_proposed_score: float, ai_note: str = "") -> dict:
    """AI emits a 1-5 presentation risk score. Code validates the range.
    From modes/_shared.md:
      5 = Interviews well, strong communicator, polished resume
      4 = Solid with light coaching
      3 = Average
      2 = Known interview weakness or resume concerns
      1 = High risk of poor impression
    """
    try:
        score = float(ai_proposed_score) if ai_proposed_score is not None else 3.0
    except (ValueError, TypeError):
        score = 3.0
    score = max(1.0, min(5.0, score))
    return {
        "score": round(score, 2),
        "note": ai_note or f"AI-proposed presentation score = {score}",
        "source": "ai_validated",
    }


# --------------------------------------------------------------------
# Dim 8: Fill Probability
# --------------------------------------------------------------------

def score_fill_probability(
    technical_match_score: float,
    gap_severity_score: float,
    historical_fill_rate: float = None,
    n_historical_outcomes: int = 0,
) -> dict:
    """Pure code. Derive fill probability from:
      - The engine's own Technical Match score (40% weight)
      - The engine's own Gap Severity score (30% weight)
      - Historical fill rate from req_outcomes if available (30% weight)
        with confidence weighting by sample size (needs n >= 5 to count fully)

    From modes/_shared.md:
      5 = 80%+ chance of offer if submitted
      4 = 60-79% - strong, normal competition
      3 = 40-59% - competitive but realistic
      2 = 20-39% - long shot
      1 = Below 20% - don't submit
    """
    # Normalize technical and gap to 0-1
    tech_norm = (technical_match_score or 0) / 5.0
    gap_norm = (gap_severity_score or 0) / 5.0

    # Base probability from engine signals
    base_prob = 0.40 * tech_norm + 0.30 * gap_norm

    # Add historical fill rate if we have enough sample
    if historical_fill_rate is not None and n_historical_outcomes >= 5:
        # Full confidence on historical
        base_prob += 0.30 * historical_fill_rate
        hist_note = (f"historical fill rate {historical_fill_rate*100:.0f}% "
                     f"(n={n_historical_outcomes})")
    elif historical_fill_rate is not None and n_historical_outcomes > 0:
        # Partial confidence on historical, blend with neutral 0.5
        confidence = n_historical_outcomes / 5.0
        adjusted = (confidence * historical_fill_rate) + ((1 - confidence) * 0.5)
        base_prob += 0.30 * adjusted
        hist_note = (f"historical fill rate {historical_fill_rate*100:.0f}% "
                     f"(low confidence, n={n_historical_outcomes})")
    else:
        # No history, use neutral 0.5 for the historical component
        base_prob += 0.30 * 0.5
        hist_note = "no historical outcomes available"

    # Scale to 1-5
    # base_prob is in [0, 1], scale to [1, 5]
    score = 1.0 + (base_prob * 4.0)
    score = max(1.0, min(5.0, score))

    return {
        "score": round(score, 2),
        "note": f"Fill Probability = 0.4×tech({tech_norm:.2f}) + "
                f"0.3×gap({gap_norm:.2f}) + 0.3×{hist_note} → {score:.2f}",
        "components": {
            "technical_normalized": round(tech_norm, 3),
            "gap_normalized": round(gap_norm, 3),
            "historical_fill_rate": historical_fill_rate,
            "n_outcomes": n_historical_outcomes,
        },
    }


# --------------------------------------------------------------------
# Composite: weighted average of all 8 dimensions
# --------------------------------------------------------------------

def compute_composite(dimensions: dict, has_blockers: bool = False) -> dict:
    """Weighted average per modes/_shared.md.

    dimensions: dict with all 8 keys, each value being either a number
      (the dimension score) or a dict with a 'score' key.

    Returns dict with composite + effective composite (post-blocker-cap) +
    recommendation.
    """
    raw_total = 0.0
    weight_total = 0.0
    breakdown = {}

    for dim_name, weight in DIMENSION_WEIGHTS.items():
        val = dimensions.get(dim_name)
        if val is None:
            continue
        if isinstance(val, dict):
            score = val.get("score")
        else:
            score = val
        if score is None:
            continue
        try:
            score_f = float(score)
        except (ValueError, TypeError):
            continue
        raw_total += score_f * weight
        weight_total += weight
        breakdown[dim_name] = {"score": score_f, "weight": weight,
                                "contribution": round(score_f * weight, 3)}

    if weight_total == 0:
        return {
            "composite": 0.0,
            "effective_composite": 0.0,
            "recommendation": "Hard Pass",
            "breakdown": breakdown,
            "note": "No dimensions had scores. Cannot compute composite.",
        }

    composite = raw_total / weight_total
    effective = min(composite, BLOCKER_CAP) if has_blockers else composite
    recommendation = apply_composite_threshold(effective, has_blockers)

    return {
        "composite": round(composite, 3),
        "effective_composite": round(effective, 3),
        "recommendation": recommendation,
        "has_blockers": has_blockers,
        "weight_total": round(weight_total, 3),
        "breakdown": breakdown,
        "note": (f"Composite = sum({sum(b['contribution'] for b in breakdown.values()):.2f}) "
                 f"/ weight_total ({weight_total:.2f}) = {composite:.2f}"
                 + (f" → capped at {BLOCKER_CAP} due to blockers" if has_blockers else "")),
    }


# --------------------------------------------------------------------
# Full 8-dimension evaluation
# --------------------------------------------------------------------

def evaluate_full(
    req_skills: list,
    candidate_skills: list,
    adjacency_index: dict,
    req_metadata: dict,
    candidate_metadata: dict,
    ai_proposals: dict,
    historical_data: dict = None,
) -> dict:
    """Run the complete 8-dimension scoring.

    req_metadata: {
      'location': str, 'remote_policy': str,
      'comp_range': str, 'seniority_signals': list[str],
    }
    candidate_metadata: {
      'location': str, 'expected_comp': str,
      'seniority_signals': dict (years_total, years_in_niche, mgmt, tech_lead),
    }
    ai_proposals: {
      'culture_score': float (1-5),
      'culture_note': str,
      'presentation_score': float (1-5),
      'presentation_note': str,
    }
    historical_data: {
      'fill_rate': float (0-1),
      'n_outcomes': int,
    } or None
    """
    # First 2 dimensions from skills (already-shipped path)
    skill_result = evaluate_skills(req_skills, candidate_skills, adjacency_index)
    technical = skill_result["technical_match"]
    gap = skill_result["gap_severity"]
    has_blockers = skill_result["blocker_count"] > 0

    # Other 6 dimensions
    location = score_location_fit(
        req_metadata.get("location", ""),
        req_metadata.get("remote_policy", ""),
        candidate_metadata.get("location", ""),
    )
    comp = score_comp_alignment(
        req_metadata.get("comp_range", ""),
        candidate_metadata.get("expected_comp", ""),
    )
    seniority = score_seniority_fit(
        req_metadata.get("seniority_signals", []),
        candidate_metadata.get("seniority_signals", {}),
    )
    culture = score_culture_signals(
        ai_proposals.get("culture_score"),
        ai_proposals.get("culture_note", ""),
    )
    presentation = score_presentation_risk(
        ai_proposals.get("presentation_score"),
        ai_proposals.get("presentation_note", ""),
    )

    hist = historical_data or {}
    fill = score_fill_probability(
        technical["score"], gap["score"],
        hist.get("fill_rate"), hist.get("n_outcomes", 0),
    )

    all_dimensions = {
        "technical_match": technical,
        "seniority_fit": seniority,
        "location_alignment": location,
        "comp_alignment": comp,
        "culture_signals": culture,
        "gap_severity": gap,
        "presentation_risk": presentation,
        "fill_probability": fill,
    }

    composite_result = compute_composite(all_dimensions, has_blockers)

    return {
        "skill_matches": skill_result["skill_matches"],
        "blockers": skill_result["blockers"],
        "blocker_count": skill_result["blocker_count"],
        "dimensions": {k: v for k, v in all_dimensions.items()},
        "composite": composite_result,
    }
