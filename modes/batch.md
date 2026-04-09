# Mode: Batch

> Evaluate multiple candidates against a single JD in parallel using sub-agents.

## Trigger

`/placement-ops batch`

## Input

The recruiter provides:
1. A **job description** (pasted or URL)
2. A **list of candidates** — either:
   - Specific files: "Evaluate jane-doe.yml, john-smith.yml, alice-chen.yml"
   - All candidates: "Evaluate everyone in my candidates folder"
   - Filtered: "Evaluate all candidates who target ML Engineer roles"

## Pre-Flight

1. Load `modes/_shared.md`
2. Load `modes/evaluate.md` (each sub-agent runs a full evaluation)
3. Load `config/profile.yml`
4. Load all specified candidate files
5. Load `data/pipeline.md` to check for prior evaluations

## Execution

### Step 1 — Candidate Selection

If the recruiter said "all candidates":
1. List all YAML files in `candidates/`
2. For each, check if their `target.roles` overlap with the JD's archetype
3. Filter out candidates whose `min_compensation` exceeds the role's likely budget by 35%+
4. Filter out candidates whose `location` is incompatible
5. Present the filtered list to the recruiter for confirmation before running

### Step 2 — Parallel Evaluation

Launch one sub-agent per candidate. Each sub-agent runs the full `/placement-ops evaluate` flow:

- Archetype detection
- 8-dimension scoring
- Gap analysis
- Recommendation

**Concurrency limit**: 3 sub-agents at a time (to manage API costs).

### Step 3 — Comparison Table

Once all evaluations complete, produce a ranked comparison:

```markdown
## Batch Evaluation: [Company] — [Role]
**Date**: 2026-04-08 | **Candidates evaluated**: 5

| Rank | Candidate | Composite | Tech | Senior | Loc | Comp | Culture | Gaps | Present | Fill | Verdict |
|------|-----------|-----------|------|--------|-----|------|---------|------|---------|------|---------|
| 1 | Jane Smith | 4.3 | 4.5 | 4.5 | 5.0 | 3.5 | 3.0 | 4.0 | 4.5 | 4.0 | SUBMIT |
| 2 | Alice Chen | 4.1 | 4.0 | 4.0 | 5.0 | 4.5 | 3.0 | 4.0 | 4.0 | 3.8 | SUBMIT |
| 3 | John Doe | 3.6 | 3.5 | 4.0 | 3.0 | 4.0 | 3.0 | 3.0 | 3.5 | 3.5 | MAYBE |
| 4 | Bob Wilson | 3.2 | 3.0 | 3.5 | 5.0 | 3.0 | 3.0 | 2.5 | 3.5 | 3.0 | PASS |
| 5 | Carol Davis | 2.8 | 2.5 | 3.0 | 5.0 | 4.0 | 3.0 | 2.0 | 3.0 | 2.5 | HARD PASS |

**Recommended submissions** (score ≥ 4.0):
1. Jane Smith — Lead with her. Strongest technical match. Address comp gap in cover memo.
2. Alice Chen — Solid backup. Comp alignment is better. Less depth in ML infra.

**Borderline** (3.5-3.9):
3. John Doe — Only if pipeline is thin. Location mismatch is the main risk.
```

### Step 4 — Head-to-Head (Top 2)

For the top 2 candidates, produce a detailed comparison:

```markdown
### Jane Smith vs. Alice Chen

| Dimension | Jane | Alice | Edge |
|-----------|------|-------|------|
| Technical Match | Deeper ML infra experience, built production systems | Broader but shallower, more recent LLM work | Jane |
| Comp Risk | Wants $200K, role likely $185K | Wants $175K, perfect fit | Alice |
| Interview Risk | Strong communicator, prepped before | First-time at this level of company | Jane |
| Unique Value | Built recommendation system serving 50M users | Led migration from legacy ML to modern stack | Depends on HM priority |

**Recommendation**: Submit both. Jane as the primary, Alice as the complement.
```

## Post-Batch (Mandatory)

1. Save individual evaluation reports for each candidate (per evaluate.md rules)
2. Save the comparison table to `reports/{###}-{company-slug}-batch-{YYYY-MM-DD}.md`
3. Update `data/pipeline.md` with all evaluated candidates
4. Tell the recruiter which candidates to prep: "Run `/placement-ops prep` for Jane Smith and Alice Chen"
