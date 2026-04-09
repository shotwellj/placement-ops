# Mode: Retention

> Post-placement tracking beyond the guarantee. Measures long-term placement success and feeds retention data back into the matching engine.

## Trigger

`/placement-ops retention`

## Why This Matters

A placement that churns at 6 months costs everyone: the client loses a hire, the candidate loses stability, and you lose your reputation (and possibly the fee). Talent engineering doesn't end at the offer letter — it tracks whether the placement actually worked, and uses that data to make better matches next time.

This is also how you build expansion revenue: the 6-month check-in is when you learn the team is growing and needs more hires.

## Pre-Flight

1. Load `data/placements.tsv`
2. Load `data/retention.yml` (create if doesn't exist)
3. Load `data/calibration.yml`
4. Load `config/profile.yml`

## Tracking Schedule

Every placement gets a structured check-in cadence:

```
PLACEMENT LIFECYCLE
══════════════════════════════════════════════════════
Day 1          → Started (guarantee clock begins)
Day 30         → 30-day check-in
Day 60         → 60-day check-in
Day 85         → Pre-guarantee expiry check
Day 90         → Guarantee complete ✓
Month 6        → 6-month retention check
Month 12       → 1-year retention check (annual)
Each anniversary → Annual check-in
```

## Check-In Protocol

For each check-in, gather:

### From the Candidate

```yaml
candidate_checkin:
  date: 2026-07-08
  placement_id: PL-005
  candidate: Jane Smith
  company: Anthropic
  role: Senior MLE
  months_in_role: 3

  satisfaction: 4            # 1-5 scale
  staying_intent: "Definitely staying"  # definitely | probably | uncertain | looking
  role_match: 4.5            # 1-5: is the role what was described?
  team_fit: 4                # 1-5
  manager_relationship: 5    # 1-5

  highlights: "Shipped first model to production. Team is great."
  concerns: "Comp review isn't until December. Hoping for equity refresh."
  open_to_referrals: true    # Would they refer other candidates?

  # Expansion intel
  team_growing: true
  open_roles_mentioned: "Looking for a senior DE and another MLE"
  hiring_manager_feedback: "HM told me they want to double the team by EOY"
```

### From the Client (Hiring Manager)

```yaml
client_checkin:
  date: 2026-07-08
  placement_id: PL-005
  contact: Sarah Chen (HM)
  company: Anthropic

  performance_rating: 4.5    # 1-5
  culture_fit: 4             # 1-5
  would_hire_again: "Absolutely"  # absolutely | probably | uncertain | no
  ramp_time: "Faster than expected"

  feedback: "Jane ramped fast. Already leading a workstream. Great hire."
  concerns: "None right now."

  # Expansion intel
  additional_hiring_needs: "Need a senior DE and one more MLE"
  timeline: "Q3 2026"
  would_use_you_again: true
  referral_to_other_teams: "Could intro you to the platform team lead"
```

## Retention Scoring

Each placement gets a retention health score:

```
retention_health = (candidate_satisfaction + client_rating + staying_intent_score) / 3
```

Where `staying_intent_score`:
- "Definitely staying" = 5
- "Probably staying" = 4
- "Uncertain" = 2
- "Looking" = 1

### Retention Dashboard

```
RETENTION DASHBOARD — Active Placements
══════════════════════════════════════════════════════════════════

ID     Candidate     Company      Start       Months  Health  Status
─────────────────────────────────────────────────────────────────
PL-001 Alice Chen    Databricks   2026-01-15  3       4.5/5   ✅ Thriving
PL-002 John Doe      Stripe       2026-02-01  2       4.0/5   ✅ Good
PL-003 Bob Wilson    Scale AI     2026-02-15  2       3.0/5   ⚠️ Monitor
PL-004 Carol Davis   Ramp         2026-03-01  1       4.2/5   ✅ Good
PL-005 Jane Smith    Anthropic    2026-04-01  0       —       🔄 New

⚠️ ATTENTION: Bob Wilson @ Scale AI scored 3.0. "Uncertain" staying intent.
   Concern: "Role is more data engineering than data science."
   → ACTION: Call Bob this week. If mismatch is real, manage expectations
     with client before it becomes a falloff.

CHECK-INS DUE:
  📅 TODAY: PL-002 John Doe — 60-day check-in
  📅 THIS WEEK: PL-003 Bob Wilson — follow up on concerns
  📅 NEXT WEEK: PL-001 Alice Chen — 90-day guarantee completion 🎉
```

## Retention Analytics

After 5+ placements with retention data:

```
RETENTION ANALYTICS
══════════════════════════════════════════════════════════════════

Overall retention rate:
  6-month:     87% (13/15 still employed)
  12-month:    78% (7/9 past 1 year)

Retention by score at placement:
  4.5+ composite:    95% retained at 6 months
  4.0-4.4:           83% retained
  3.5-3.9:           60% retained
  Below 3.5:         33% retained

→ INSIGHT: Candidates scored below 3.5 churn at 3x the rate.
   Confirms the 4.0 submission threshold is protecting long-term outcomes.

Retention by dimension (what predicts staying?):
  Culture Signals:        0.72 correlation with 12-month retention
  Seniority Fit:          0.68 correlation
  Comp Alignment:         0.61 correlation
  Technical Match:        0.45 correlation

→ INSIGHT: Culture fit and seniority alignment predict retention
   better than technical skills. Consider weighting these higher.

Churn reasons (from exit data):
  Comp below market:      35% of churns
  Role mismatch:          25%
  Manager change:         20%
  Better offer:           15%
  Relocation:              5%

→ INSIGHT: 60% of churn is preventable (comp + role match).
   Improve comp benchmarking and role-match scoring.
```

## Feeding Back into the System

Retention data improves every other mode:

1. **Calibration**: Placements that churn get logged as negative outcomes, even if they initially "succeeded"
2. **Evaluate**: If culture_signals has 0.72 correlation with retention, increase its weight in the scoring rubric
3. **Benchmark**: Use retention data to identify which companies retain well vs. have a revolving door
4. **Forecast**: High-retention placements → stronger client relationship → more expansion business

## Post-Retention

1. Update `data/retention.yml` with check-in data
2. Update `data/calibration.yml` if a placement churns (negative outcome)
3. Flag at-risk placements prominently
4. Log expansion opportunities from check-in conversations
5. Generate referral requests for thriving placements
6. Update `reports/retention-{YYYY-MM-DD}.md`
