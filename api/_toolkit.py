"""SourcingNav internal toolkit (Phase E.2-prep, 2026-05-19).

A documented surface of core capabilities, exposed as Pydantic-wrapped
functions. NOT an agentic tool registry yet - this is groundwork for:

  (a) Building new lifecycle modes (Schedule, Interview) faster. Today,
      every new mode hand-discovers what helpers exist by grep + read.
      With this module, importing one toolkit gets you the verified
      surface.

  (b) Adding an MCP server later. When a Pro customer asks "can Claude
      Desktop access my SourcingNav data?", these wrappers become MCP
      tools with their existing schemas. No refactor.

  (c) Building a "Sourcing Yoda"-style chat surface someday. Same story:
      the toolkit is already MCP-ready.

Design rules for this module:
  1. Thin pass-throughs only. Each tool wraps an existing primitive with
     Pydantic input/output models. No business logic lives here.
  2. Input/Output models are dataclass-frozen Pydantic models. Validation
     happens at the boundary. Downstream code can rely on shapes.
  3. Every tool has a docstring explaining: what it does, when to use,
     what it returns, what it can't do.
  4. No async wrappers around sync code; no sync wrappers around async.
     Keep the existing async signatures.

What this module does NOT do:
  - Replace direct imports of the primitives. If you already
    `from api._compliance import run_matching_engine` and it works, fine,
    don't switch for the sake of switching. Use the toolkit when the
    Pydantic-validated surface adds value (e.g. when input shapes are
    coming from an HTTP request or another agent).
  - Introduce a tool-routing layer. The agent doesn't pick tools here.
    The route handler picks tools, same as today.
  - Cache or memoize. Pass-through only. If a primitive is slow, fix the
    primitive.
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field

# Re-export the underlying primitives so callers don't have to know which
# module they live in. The toolkit is the single import surface.
from api._compliance import (
    run_matching_engine as _run_matching_engine,
    resolve_skill_id as _resolve_skill_id,
    write_candidate_skills as _write_candidate_skills,
    write_req_skills as _write_req_skills,
    write_submission_dimensions as _write_submission_dimensions,
)


# ============================================================================
# Skill resolution
# ============================================================================

class ResolveSkillInput(BaseModel):
    """Input for resolve_skill: a raw skill phrase, possibly with formatting."""
    raw_name: str = Field(..., description="The raw skill text as captured from a JD or candidate profile. May include parentheticals, slashes, leading phrases like 'experience with'.")


class ResolveSkillOutput(BaseModel):
    """Output: the canonical skill_id if found, or None."""
    skill_id: Optional[str] = Field(None, description="The skill_id from the skills table, or None if no match found.")
    raw_name: str = Field(..., description="Echo of the input for traceability.")


async def resolve_skill(client, input: ResolveSkillInput) -> ResolveSkillOutput:
    """Resolve a raw skill phrase to a canonical skill_id.

    Tries variant forms (slashes split, parentheticals stripped, leading
    phrases removed, etc.) and falls through 4 tiers: exact canonical,
    alias match, fuzzy normalized, no match. See api/_compliance.py
    resolve_skill_id docstring for the full algorithm.
    """
    skill_id = await _resolve_skill_id(client, input.raw_name)
    return ResolveSkillOutput(skill_id=skill_id, raw_name=input.raw_name)


# ============================================================================
# Matching engine
# ============================================================================

class RunMatchingEngineInput(BaseModel):
    """Input for run_matching_engine: a (req, candidate) pair.

    Both must exist in the database (req via requisitions, candidate via
    candidates with skills already written via write_candidate_skills).
    The optional evaluation dict provides AI-derived signals (seniority,
    culture, presentation) - pass None if the candidate has no prior eval.
    """
    req_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    evaluation: Optional[dict] = Field(
        None,
        description="The candidate's most recent AI evaluation, used for "
                    "seniority signal extraction + AI proposal dims. Pass "
                    "None for candidates with no prior eval (engine falls "
                    "back to neutral on those dims).",
    )


# We don't model the output shape strictly because the engine returns a
# big nested dict from evaluate_full(). Callers iterate the dimensions
# directly. Strict modeling would create a coupling burden every time the
# engine schema evolves. Pass-through for now.
async def run_matching_engine(client, input: RunMatchingEngineInput) -> Optional[dict]:
    """Score one candidate against one req using the 8-dimension engine.

    Returns evaluate_full() output:
      {
        "dimensions": {tech_match, seniority_fit, location_alignment, ...},
        "composite": {composite, effective_composite, recommendation, ...},
        "blocker_count": int,
        "blockers": [...],
        "skill_matches": [...],
      }
    Or None if the candidate has no candidate_skills data.

    No DB writes. Read-only. Same engine code path as Source eval.
    """
    return await _run_matching_engine(
        client, input.req_id, input.candidate_id, input.evaluation
    )


# ============================================================================
# Candidate skill capture
# ============================================================================

class WriteCandidateSkillsInput(BaseModel):
    """Input for write_candidate_skills: which candidate, what eval to mine."""
    candidate_id: str = Field(..., min_length=1)
    evaluation: dict = Field(
        ...,
        description="The AI evaluation output. Skills are mined from "
                    "extracted_skills (preferred) or from blocker_assessment "
                    "+ preferred_assessment as a fallback.",
    )


class WriteCandidateSkillsOutput(BaseModel):
    inserted: int = Field(..., description="Number of candidate_skills rows written.")


async def write_candidate_skills(
    client, input: WriteCandidateSkillsInput
) -> WriteCandidateSkillsOutput:
    """Mine skills from an AI evaluation and persist them.

    Writes met/partial/unclear skills (drops 'missing' since that's a
    confirmed gap, not a skill). Status-aware confidence + depth values.
    Resolves each skill to a canonical skill_id where possible.
    """
    inserted = await _write_candidate_skills(
        client, input.candidate_id, input.evaluation
    )
    return WriteCandidateSkillsOutput(inserted=inserted)


# ============================================================================
# Requisition skill capture
# ============================================================================

class WriteReqSkillsInput(BaseModel):
    """Input for write_req_skills: which req, what parsed JD to mine."""
    req_id: str = Field(..., min_length=1)
    parsed: dict = Field(
        ...,
        description="The parsed JD JSON. Skills are extracted from "
                    "blockers + preferred + other fields per the JD schema.",
    )


class WriteReqSkillsOutput(BaseModel):
    inserted: int = Field(..., description="Number of req_skills rows written.")


async def write_req_skills(client, input: WriteReqSkillsInput) -> WriteReqSkillsOutput:
    """Persist skills extracted from a parsed JD."""
    inserted = await _write_req_skills(client, input.req_id, input.parsed)
    return WriteReqSkillsOutput(inserted=inserted)


# ============================================================================
# Submission dimensions writer (the engine output -> DB persistence)
# ============================================================================

class WriteSubmissionDimensionsInput(BaseModel):
    """Input for write_submission_dimensions: persist a full eval to the DB.

    This is called at the end of Source eval to record the 8 dimension
    scores + composite + blockers + match_breakdown for audit purposes.
    """
    submission_id: str = Field(..., min_length=1)
    req_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    evaluation: Optional[dict] = Field(
        None,
        description="The AI evaluation output - used to extract AI proposals "
                    "for culture/presentation dims and seniority signals.",
    )


async def write_submission_dimensions(
    client, input: WriteSubmissionDimensionsInput
) -> dict:
    """Run the engine and persist all 8 dimensions to submission_dimensions.

    Returns the engine output for caller inspection. Side effect: writes
    one row to submission_dimensions with composite, blocker_count, and
    match_breakdown_json populated.
    """
    return await _write_submission_dimensions(
        client,
        input.submission_id,
        input.req_id,
        input.candidate_id,
        input.evaluation,
    )


# ============================================================================
# Manifest: what this toolkit exposes
# ============================================================================

# For future MCP server use: this list becomes the tool advertisement.
TOOLS = (
    {
        "name": "resolve_skill",
        "description": "Resolve a raw skill phrase to a canonical skill_id.",
        "input_schema": ResolveSkillInput.model_json_schema(),
    },
    {
        "name": "run_matching_engine",
        "description": "Score a candidate against a req using the 8-dimension engine.",
        "input_schema": RunMatchingEngineInput.model_json_schema(),
    },
    {
        "name": "write_candidate_skills",
        "description": "Mine skills from an AI eval and persist to candidate_skills.",
        "input_schema": WriteCandidateSkillsInput.model_json_schema(),
    },
    {
        "name": "write_req_skills",
        "description": "Persist skills extracted from a parsed JD.",
        "input_schema": WriteReqSkillsInput.model_json_schema(),
    },
    {
        "name": "write_submission_dimensions",
        "description": "Persist a full 8-dim engine evaluation to submission_dimensions.",
        "input_schema": WriteSubmissionDimensionsInput.model_json_schema(),
    },
)


def _boot_log() -> None:
    print(f"[toolkit] registered {len(TOOLS)} tools: {[t['name'] for t in TOOLS]}")


_boot_log()
