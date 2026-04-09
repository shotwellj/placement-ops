# Mode: Calibrate

> Learn from past placements and rejections to improve matching accuracy over time.

## Trigger

`/placement-ops calibrate`

## Why This Matters

Every recruiter has a gut instinct built from years of placements. This mode turns that gut instinct into data. When a candidate gets placed or rejected, you feed the outcome back in, and the system learns what actually matters vs. what JDs say matters.

After 10-20 calibration data points, the matching engine starts outperforming keyword matching because it knows things like:
- "Kubernetes is always listed as required but clients rarely reject for it"
- "Cross-functional communication is listed as nice-to-have but is actually the #1 rejection reason at Staff+ level"
- "Adjacent ML framework experience converts at 85% — treat it like a match"

## Input

The recruiter provides an outcome for a past submission:

```
"Jane Smith got placed at Anthropic as Senior MLE"
or
"John Doe got rejected at Stripe — they said not enough Spark experience"
or
"Alice Chen withdrew from Netflix — comp was too low"
```

## Workflow

### Step 1 — Find the Evaluation

Load the evaluation report from `reports/` for this candidate + role combination. If no report exists, ask the recruiter to provide the key details.

### Step 2 — Record the Outcome

Create or update `data/calibration.yml`:

```yaml
outcomes:
  - id: CAL-001
    date: 2026-04-08
    candidate: Jane Smith
    company: Anthropic
    role: Senior MLE
    archetype: IC-MLE
    composite_score: 4.3
    outcome: placed                # placed | rejected | withdrawn | offer_declined
    stage_reached: offer           # submitted | phone_screen | onsite | offer
    rejection_reason: null
    salary: 195000
    fee_earned: 39000
    notes: "Placed in 3 weeks. Client loved the systems design experience."

    # Snapshot of the scores at submission time
    scores:
      technical_match: 4.5
      seniority_fit: 4.5
      location_remote: 5.0
      comp_alignment: 3.5
      culture_signals: 3.0
      gap_severity: 4.0
      presentation_risk: 4.5
      fill_probability: 4.0

    # Which gaps existed and whether they mattered
    gaps_at_submission:
      - skill: Kubernetes
        score_at_time: 0.0
        importance_in_jd: preferred
        did_it_matter: false        # client didn't care
      - skill: Spark
        score_at_time: 0.3
        importance_in_jd: nice-to-have
        did_it_matter: false

  - id: CAL-002
    date: 2026-04-10
    candidate: John Doe
    company: Stripe
    role: Staff Data Scientist
    archetype: IC-DS
    composite_score: 3.6
    outcome: rejected
    stage_reached: phone_screen
    rejection_reason: "Insufficient Spark/distributed systems experience"
    salary: null
    fee_earned: 0
    notes: "Client was firm on Spark requirement despite JD listing it as preferred."

    scores:
      technical_match: 3.5
      seniority_fit: 4.0
      location_remote: 3.0
      comp_alignment: 4.0
      culture_signals: 3.0
      gap_severity: 3.0
      presentation_risk: 3.5
      fill_probability: 3.5

    gaps_at_submission:
      - skill: Apache Spark
        score_at_time: 0.0
        importance_in_jd: preferred
        did_it_matter: true         # THIS is what killed it
      - skill: Scala
        score_at_time: 0.0
        importance_in_jd: nice-to-have
        did_it_matter: false
```

### Step 3 — Pattern Analysis

After 5+ outcomes, run pattern analysis:

```markdown
## Calibration Report — [Date]

### Dataset: [N] outcomes ([X] placed, [Y] rejected, [Z] withdrawn)

### Predictive Accuracy
- Candidates scored 4.0+ who got placed: X / Y (Z%)
- Candidates scored 3.5-3.9 who got placed: X / Y (Z%)
- Candidates scored below 3.5 who got placed: X / Y (Z%)
- Current scoring threshold (4.0) accuracy: Z%

### Skill Importance Corrections
These JD labels don't match reality:

| Skill | JD Says | Actually Is | Evidence |
|-------|---------|-------------|----------|
| Kubernetes | required | preferred | 3/3 placements had K8s gaps, clients didn't care |
| Spark | preferred | required | 2/2 Spark-gap candidates rejected at Stripe/Databricks |
| Cross-functional communication | nice-to-have | required (Staff+) | All Staff+ rejections cited this |

### Adjacency Accuracy
How well do adjacent skill matches convert?

| Adjacent Pair | Times Tested | Accepted | Conversion |
|--------------|-------------|----------|------------|
| TensorFlow → PyTorch | 4 | 3 | 75% |
| Docker → Kubernetes | 3 | 2 | 67% |
| Airflow → Prefect | 2 | 2 | 100% |

### Recommended Adjustments
1. Upgrade "Spark" from adjacent_score 0.6 → 0.3 for Stripe/Databricks roles (they're strict)
2. Add "cross-functional communication" to required list for all LEAD/HEAD archetypes
3. Current threshold of 4.0 is well-calibrated — 82% placement rate above this line

### Score-to-Outcome Correlation
| Score Range | Submissions | Placed | Rate | Recommendation |
|------------|------------|--------|------|----------------|
| 4.5+ | 5 | 4 | 80% | Strong submit ✅ |
| 4.0-4.4 | 8 | 5 | 63% | Submit ✅ |
| 3.5-3.9 | 6 | 1 | 17% | Avoid unless thin pipeline ⚠️ |
| Below 3.5 | 3 | 0 | 0% | Never submit ❌ |
```

### Step 4 — Apply Adjustments

If the data supports it, suggest specific changes to:

1. **Taxonomy adjacency scores** — If TensorFlow→PyTorch converts at 90%, suggest raising adjacent score from 0.6 to 0.8
2. **Company-specific overrides** — If Stripe always rejects for Spark gaps, add a company-specific weight override
3. **Threshold tuning** — If 3.8+ converts at 70%, suggest lowering the submit threshold
4. **Archetype weights** — If LEAD roles care more about soft skills than the rubric weights, adjust

These suggestions are always presented to the recruiter for approval — the system never auto-adjusts.

### Step 5 — Save

1. Update `data/calibration.yml` with the new outcome
2. Save the pattern analysis to `reports/calibration-{YYYY-MM-DD}.md`
3. If adjustments are approved, update `taxonomy/skills.yml` and `modes/_shared.md`

## Starting Cold (No Data Yet)

If this is the first time running calibrate:

```
No calibration data yet. Here's how to build it:

1. Think of your last 5-10 placements and rejections
2. For each, tell me:
   - Candidate name + role + company
   - Outcome (placed / rejected / withdrawn)
   - If rejected: what reason did the client give?
   - If placed: what was the salary?

I'll backfill the calibration data from your memory and we'll
have a baseline to start improving the matching engine.
```

## Rules

- Never auto-adjust the taxonomy — always present recommendations for recruiter approval
- Minimum 5 data points before generating pattern analysis
- Always show confidence levels ("based on 3 data points" vs. "based on 20 data points")
- Protect candidate PII — calibration data stays local
