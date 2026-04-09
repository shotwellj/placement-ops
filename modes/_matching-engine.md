# Matching Engine — The Algorithm

> This file defines the formal matching algorithm. Loaded by `evaluate`, `batch`, and `calibrate` modes.

## Overview

The matching engine replaces gut-feel "this looks like a fit" with a structured, reproducible scoring algorithm. It uses the skill taxonomy (`taxonomy/skills.yml`) to understand skill relationships and give partial credit for adjacent experience.

Think of it like this: a traditional recruiter sees "PyTorch" on a JD and "TensorFlow" on a resume and says "close enough." This engine does the same thing, but consistently, across every candidate, with a score you can compare.

## Step 1 — Requirement Extraction

Parse the JD and extract a structured requirement set:

```yaml
requirements:
  hard_skills:
    - skill: "PyTorch"
      importance: required        # required | preferred | nice-to-have
      years: 3                    # minimum years if stated, null if not
    - skill: "Kubernetes"
      importance: preferred
      years: null
    - skill: "distributed training"
      importance: required
      years: 2

  seniority_signals:
    - "5+ years experience"
    - "lead a team of 3-5"
    - "mentor junior engineers"

  domain_signals:
    - "recommendation systems"
    - "real-time serving"

  soft_skills:
    - "cross-functional collaboration"
    - "stakeholder management"
```

**Importance weights:**
- `required` = 1.0
- `preferred` = 0.6
- `nice-to-have` = 0.3

## Step 2 — Candidate Skill Extraction

Parse the candidate's resume and build a skill profile:

```yaml
candidate_skills:
  - skill: "TensorFlow"
    evidence: "Built recommendation model using TensorFlow Serving"
    recency: current              # current | recent (1-3yr) | dated (3+yr)
    depth: production             # mentioned | project | production | expert

  - skill: "Docker"
    evidence: "Containerized all ML services"
    recency: current
    depth: production
```

**Recency decay:**
- `current` = 1.0 (at current or most recent job)
- `recent` = 0.8 (1-3 years ago)
- `dated` = 0.5 (3+ years ago)

**Depth multiplier:**
- `expert` = 1.0 (taught others, designed systems, deep expertise)
- `production` = 0.9 (used in production, shipped real things)
- `project` = 0.7 (used in a project or side work)
- `mentioned` = 0.4 (listed on resume but no supporting detail)

## Step 3 — Taxonomy-Driven Match Scoring

For each JD requirement, find the best matching candidate skill:

```
match_score = match_type × recency × depth × importance
```

Where `match_type` comes from the taxonomy:
- **Exact match** = 1.0 (candidate has the exact skill)
- **Alias match** = 1.0 (candidate has a known alias, e.g., "sklearn" = "scikit-learn")
- **Adjacent match** = 0.6 (candidate has a related skill, e.g., "TensorFlow" for "PyTorch")
- **Parent category match** = 0.3 (candidate has skills in the same category)
- **No match** = 0.0

### Example Calculation

JD requires: "PyTorch" (required, 3+ years)
Candidate has: "TensorFlow" (production, current)

```
match_type  = 0.6   (adjacent per taxonomy)
recency     = 1.0   (current)
depth       = 0.9   (production)
importance  = 1.0   (required)

skill_score = 0.6 × 1.0 × 0.9 × 1.0 = 0.54
```

This candidate gets 54% credit for this requirement — not zero (like a keyword match would give) and not 100% (because it's not an exact match).

## Step 4 — Composite Technical Score

```
technical_score = sum(all skill_scores) / sum(all max_possible_scores) × 5.0
```

This normalizes to the 1-5 scale. A perfect candidate (exact match on everything, all current, all production-depth) gets 5.0.

## Step 5 — Seniority Vector

Instead of a single "years of experience" number, compute a seniority vector:

```yaml
seniority_vector:
  years_total: 7
  years_in_niche: 5              # years doing ML/DS specifically
  management_experience: 0        # years managing people
  tech_lead_signals: 2            # architecture decisions, mentorship, cross-team work
  scope_level: "team"             # individual | team | org | company
```

Compare this vector against the JD's seniority signals:

| JD Signal | Vector Check | Score |
|-----------|-------------|-------|
| "5+ years" | years_in_niche ≥ 5 | ✅ 1.0 |
| "lead a team" | management_experience > 0 OR tech_lead_signals ≥ 2 | ⚠️ 0.6 |
| "mentor juniors" | tech_lead_signals ≥ 1 | ✅ 1.0 |

Seniority score = average of all checks × 5.0

## Step 6 — Gap Analysis

For every requirement where `skill_score < 0.5`:

```yaml
gaps:
  - requirement: "Kubernetes"
    score: 0.0
    severity: preferred           # based on importance
    mitigatable: true
    mitigation: "Has Docker production experience (adjacent). Can learn K8s in first month."
    blocker: false

  - requirement: "distributed training"
    score: 0.0
    severity: required
    mitigatable: false
    mitigation: "No adjacent experience. Core to the role."
    blocker: true
```

**Blocker rules:**
- A `required` skill with score 0.0 and no adjacent match = BLOCKER
- A `required` skill with adjacent match (score 0.3-0.6) = mitigatable
- A `preferred` or `nice-to-have` skill = never a blocker

If any BLOCKER exists → cap composite at 3.0 maximum (PASS territory).

## Step 7 — Compatibility Matrix Output

The final output is a structured matrix the recruiter can read at a glance:

```
╔══════════════════════════════════════════════════════════════╗
║  COMPATIBILITY MATRIX: Jane Smith → Anthropic Senior MLE    ║
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
║  Skill Breakdown:                                            ║
║    Exact matches:   8 / 12 requirements                     ║
║    Adjacent credit: 2 / 12 (TensorFlow→PyTorch, Docker→K8s) ║
║    Gaps:            2 / 12 (Spark: nice-to-have, Scala: low)║
║    Blockers:        0                                        ║
║                                                              ║
║  Est. Fee: $39,000 (20% × $195K midpoint)                   ║
╚══════════════════════════════════════════════════════════════╝
```

## Step 8 — Multi-Candidate Ranking (Batch Mode)

When evaluating multiple candidates against one JD:

1. Run Steps 1-7 for each candidate
2. Rank by composite score
3. Break ties with:
   - Fewer blockers wins
   - Higher exact-match count wins
   - More recent experience wins
4. Flag "complementary pairs" — candidates who are strong where the other is weak
5. Output the comparison table from `batch.md`

## Calibration Hooks

The matching engine improves over time via `/placement-ops calibrate`:

- When a placement closes: record which scores predicted success
- When a candidate gets rejected: record which gaps the client cited
- Over time, adjust: importance weights, adjacency scores, and seniority thresholds

See `modes/calibrate.md` for the calibration workflow.
