# Mode: Evaluate

> Score a candidate against a specific job description across 8 dimensions.

## Trigger

`/placement-ops evaluate`

## Input

The recruiter provides:
1. A **job description** (pasted text or URL)
2. A **candidate file** (path to a YAML file in `candidates/`, or the recruiter picks from available candidates)

## Pre-Flight

1. Load `modes/_shared.md` for scoring rubric and archetypes
2. Load `modes/_matching-engine.md` for the formal matching algorithm
3. Load `taxonomy/skills.yml` for skill adjacency relationships
4. Load `config/profile.yml` for fee structure
5. Load `data/calibration.yml` if it exists (for learned adjustments)
6. Load the candidate file
7. If the candidate has a `resume_path`, load that file too

## Step 0 — Archetype Detection

Classify the JD into one of the 8 archetypes from `_shared.md`:

- IC-DS, IC-MLE, IC-DE, IC-AE, IC-AI, MGR, LEAD, HEAD

If the role is a hybrid (e.g., "ML Engineer who also manages a team"), list the top 2 archetypes. The primary archetype drives which skills and experience to weight most heavily.

## Block A — Role Summary

| Field | Value |
|-------|-------|
| Company | [extracted] |
| Role Title | [extracted] |
| Archetype | [detected] |
| Domain | [platform / applied / research / infra / analytics] |
| Seniority | [junior / mid / senior / staff / manager / director] |
| Location | [city / remote / hybrid] |
| Team Size | [if mentioned] |
| Estimated Comp | [from JD or research] |
| Estimated Fee | [comp × fee rate from profile.yml] |
| TL;DR | [one sentence] |

## Block B — Taxonomy-Driven Technical Match

**Use the matching engine algorithm from `_matching-engine.md` for this block.**

### Step B.1 — Extract JD Requirements

Parse the JD into structured requirements per the matching engine's Step 1:

```yaml
requirements:
  - skill: "PyTorch"
    importance: required
    years: 3
  - skill: "Kubernetes"
    importance: preferred
  - skill: "distributed training"
    importance: required
```

### Step B.2 — Extract Candidate Skills

Parse the resume into a skill profile per the matching engine's Step 2:

```yaml
candidate_skills:
  - skill: "TensorFlow"
    evidence: "Built recommendation model using TF Serving"
    recency: current
    depth: production
```

### Step B.3 — Taxonomy Match Table

For each requirement, look up the best candidate skill in `taxonomy/skills.yml` and calculate the score:

| # | JD Requirement | Importance | Best Candidate Match | Match Type | Recency | Depth | Score |
|---|---------------|-----------|---------------------|-----------|---------|-------|-------|
| 1 | PyTorch (3yr) | required (1.0) | TensorFlow | adjacent (0.6) | current (1.0) | production (0.9) | **0.54** |
| 2 | Kubernetes | preferred (0.6) | Docker | adjacent (0.6) | current (1.0) | production (0.9) | **0.32** |
| 3 | 5yr Python ML | required (1.0) | Python (7yr ML) | exact (1.0) | current (1.0) | expert (1.0) | **1.00** |
| 4 | distributed training | required (1.0) | — | none (0.0) | — | — | **0.00** ⚠️ |

### Step B.4 — Gap Analysis

For every requirement where score < 0.5:

| Gap | Score | Severity | Blocker? | Mitigation |
|-----|-------|----------|----------|-----------|
| Kubernetes | 0.32 | preferred | No | Has Docker production exp. Can learn K8s in 30 days. |
| distributed training | 0.00 | required | **YES** | No adjacent experience. Core to role. |

**Blocker check**: If any required skill = 0.0 with no adjacent match → flag as blocker → cap composite at 3.0.

### Step B.5 — Calibration Override Check

If `data/calibration.yml` exists, check for learned adjustments:
- Has this skill been flagged as "clients don't actually care" in past outcomes?
- Does this company have specific patterns in the calibration data?
- Apply any adjustments and note them: "Calibration note: K8s gaps haven't caused rejections in 3 past placements"

## Block C — Seniority Assessment

1. What level does the JD describe?
2. What level is the candidate actually at?
3. If there's a mismatch, can it be positioned?
   - **Candidate is more senior**: Frame as "looking for depth over breadth" or "wants to be hands-on again"
   - **Candidate is more junior**: Identify specific achievements that demonstrate readiness for the next level
4. What are the risks if the candidate gets downleveled in the interview?

## Block D — Compensation & Market Data

Use web search to gather:
- Role's likely salary range (Glassdoor, Levels.fyi, Blind)
- Company's compensation reputation (top of market, average, below)
- How the candidate's expectations align

Present as a table with cited sources. If data is unavailable, say so — don't guess.

**Fee Estimate**: Calculate the estimated placement fee based on the midpoint salary × fee rate from `profile.yml`.

## Block E — The 8-Dimension Scorecard + Compatibility Matrix

Score each dimension 1-5 per the rubric in `_shared.md`. The Technical Match score MUST come from the taxonomy-driven calculation in Block B (not a gut estimate).

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Technical Match | 4.2 | "Taxonomy: 8/12 exact, 2/12 adjacent (0.54 avg), 2/12 gaps (nice-to-haves). Normalized: 4.2" |
| Seniority Fit | 4.5 | "Seniority vector: 6yr IC + 1yr tech lead. JD wants Senior IC5. Match: 4.5" |
| Location/Remote | 5.0 | "Remote US role, candidate is US-based remote." |
| Comp Alignment | 3.5 | "Candidate wants $200K, role likely $170-185K. Needs conversation." |
| Culture Signals | 3.0 | "Insufficient data on company culture. Candidate has startup + big-co mix." |
| Gap Severity | 4.0 | "Only gap is Kubernetes — listed as preferred, not required. No blockers." |
| Presentation Risk | 4.0 | "Clean resume, strong communicator per past submissions." |
| Fill Probability | 3.8 | "Solid match but competitive role — expect 3-5 other agencies submitting." |

**Composite Score: 4.05 → SUBMIT**

### Compatibility Matrix (Visual Output)

Always render the visual compatibility matrix from `_matching-engine.md` Step 7:

```
╔══════════════════════════════════════════════════════════════╗
║  COMPATIBILITY MATRIX: [Candidate] → [Company] [Role]      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Technical Match    ████████░░  4.2 / 5.0                   ║
║  Seniority Fit      █████████░  4.5 / 5.0                   ║
║  Location/Remote    ██████████  5.0 / 5.0                   ║
║  Comp Alignment     ███████░░░  3.5 / 5.0                   ║
║  Culture Signals    ██████░░░░  3.0 / 5.0                   ║
║  Gap Severity       ████████░░  4.0 / 5.0                   ║
║  Presentation Risk  ████████░░  4.0 / 5.0                   ║
║  Fill Probability   ███████░░░  3.8 / 5.0                   ║
║                                                              ║
║  COMPOSITE:         ████████░░  4.05 / 5.0  → SUBMIT       ║
║                                                              ║
║  Skill Match:  8 exact | 2 adjacent | 2 gaps | 0 blockers  ║
║  Est. Fee: $39,000                                           ║
╚══════════════════════════════════════════════════════════════╝
```

## Block F — Recommendation

Based on the composite score:

- **STRONG SUBMIT (4.5+)**: "Lead with this candidate. Call the HM today."
- **SUBMIT (4.0-4.4)**: "Solid submission. Address [specific gap] in cover memo."
- **MAYBE (3.5-3.9)**: "Only submit if your pipeline is thin for this req. Risk: [specific concern]."
- **PASS (3.0-3.4)**: "Don't submit. Reason: [specific]. Better fit for [other type of role]."
- **HARD PASS (<3.0)**: "Not a match. Move on."

Include:
- Top 3 things to emphasize in the cover memo
- Top 2 risks to preemptively address
- Suggested talking points for the candidate prep call

## Post-Evaluation (Mandatory)

### 1. Save Report

File: `reports/{###}-{company-slug}-{role-slug}-{YYYY-MM-DD}.md`

Structure:
- Header with date, company, role, candidate, archetype, composite score
- Blocks A through F (full content)
- Keywords extracted from JD (for later resume tailoring)

### 2. Update Pipeline

In `data/pipeline.md`, update the row for this req:
- Add candidate name to the Candidates column
- Update status if this is the first evaluation for this req

### 3. Print Next Steps

Tell the recruiter exactly what to do next:
- "Run `/placement-ops prep` to generate the submission package"
- "Or evaluate another candidate against this same req"
