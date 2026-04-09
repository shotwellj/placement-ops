# Mode: Analytics

> Funnel metrics, time-to-fill trends, conversion rates, and fee-per-hour analysis. Turns your pipeline into strategic intelligence.

## Trigger

`/placement-ops analytics`

## Why This Matters

Most recruiters know how much they billed. Talent engineers know their cost-per-hire, conversion rate at every funnel stage, which companies close fastest, and where their time generates the most revenue per hour. This mode turns raw pipeline data into those answers.

## Pre-Flight

1. Load `data/pipeline.md`
2. Load `data/submissions.tsv`
3. Load `data/placements.tsv`
4. Load `data/calibration.yml`
5. Load `config/profile.yml`

## Dashboard Sections

### 1. Funnel Conversion Rates

Track the drop-off at every stage:

```
PLACEMENT FUNNEL — Last 90 Days
══════════════════════════════════════════════════════
Reqs Identified        ████████████████████  142
  ↓ Worked (evaluated)  ███████████████       89  (63%)
  ↓ Candidates Prepped  ██████████            52  (58%)
  ↓ Submitted           ████████              38  (73%)
  ↓ Phone Screen        █████                 24  (63%)
  ↓ Onsite              ███                   14  (58%)
  ↓ Offer               ██                     7  (50%)
  ↓ Placed              █                      5  (71%)

Overall funnel:  142 reqs → 5 placements = 3.5% conversion
Submission-to-placement: 38 → 5 = 13.2%
```

**Bottleneck analysis**: Where is the biggest drop-off?
- If Submitted → Phone Screen is low: your submission packages aren't landing. Improve cover memos.
- If Phone Screen → Onsite is low: candidates aren't interviewing well. Improve prep mode.
- If Offer → Placed is low: comp negotiations are failing. Improve benchmarking.

### 2. Time-to-Fill Analysis

```
TIME-TO-FILL — Last 90 Days
══════════════════════════════════════════════════════
Average time to fill:         34 days
Median time to fill:          28 days
Fastest placement:            12 days (Jane @ Anthropic)
Slowest placement:            67 days (Bob @ Netflix)

By stage (average days):
  Req → First submission:      7 days
  Submission → Phone screen:   5 days
  Phone screen → Onsite:       8 days
  Onsite → Offer:              6 days
  Offer → Start:               14 days

By company tier:
  FAANG / Tier 1:              42 days avg (longer loops)
  Growth stage:                28 days avg
  Series A-B:                  19 days avg (fastest)

By archetype:
  IC-MLE:                      31 days avg
  IC-DS:                       29 days avg
  MGR/HEAD:                    48 days avg (always slower)
```

### 3. Revenue Analytics

```
REVENUE ANALYTICS — 2026 YTD
══════════════════════════════════════════════════════
Total placements:          5
Total revenue:             $187,000
Average fee:               $37,400
Highest fee:               $48,000 (Staff DS @ Stripe)
Lowest fee:                $28,000 (Senior DE @ Ramp)

Revenue per hour worked:
  Total hours tracked:      320 hrs
  Revenue per hour:         $584/hr
  Best client ($/hr):       Anthropic ($890/hr — fast process)
  Worst client ($/hr):      Netflix ($210/hr — 67 day cycle)

Fee efficiency:
  Contingency placements:   4 @ avg $35K
  Retained placements:      1 @ $48K
  Retained premium:         +37% over contingency

Monthly trend:
  Jan: $0    Feb: $39K    Mar: $48K    Apr: $100K (projected)
```

### 4. Client Analytics

```
CLIENT SCOREBOARD
══════════════════════════════════════════════════════
                    Reqs  Subs  Placed  Rate   Avg Fee  Avg Days  $/Hr
Anthropic            3     5      2     40%    $39K     18 days   $890
Stripe               2     3      1     33%    $48K     34 days   $520
Scale AI             2     4      1     25%    $36K     28 days   $470
Databricks           3     6      1     17%    $44K     42 days   $340
Netflix              2     4      0      0%    —        —         $0

INSIGHT: Anthropic converts fastest and generates highest $/hr.
         Prioritize Anthropic reqs over Netflix.
         Netflix has 0% close rate — consider whether to keep working them.
```

### 5. Candidate Analytics

```
CANDIDATE PERFORMANCE
══════════════════════════════════════════════════════
Total candidates in system:    24
  Active (in process):          8
  Placed:                       5
  Rejected:                     9
  Withdrawn:                    2

Candidates by submission count:
  Submitted to 1 company:      12
  Submitted to 2-3 companies:   8
  Submitted to 4+ companies:    4

Placement rate by score:
  4.5+ composite:   80% placed (4/5)
  4.0-4.4:          63% placed (5/8)
  3.5-3.9:          17% placed (1/6)
  Below 3.5:         0% placed (0/5)

Top performers (highest placement rate):
  Jane Smith:    2/3 submitted → placed (67%)
  Alice Chen:    1/2 submitted → placed (50%)
```

### 6. Taxonomy Effectiveness

```
SKILL TAXONOMY PERFORMANCE
══════════════════════════════════════════════════════
Most common JD requirements (last 90 days):
  1. Python (94% of JDs)
  2. SQL (82%)
  3. PyTorch/TensorFlow (71%)
  4. Kubernetes (64%)
  5. Spark (58%)

Adjacent match conversion rates:
  TensorFlow → PyTorch:    75% (clients accepted)
  Docker → Kubernetes:     67%
  Airflow → Prefect:       100%
  scikit-learn → XGBoost:  50%

Skills most correlated with placement:
  1. Production ML systems (89% of placed candidates had this)
  2. A/B testing (78%)
  3. Cross-functional communication (72%)

Skills least correlated (JDs ask, clients don't reject for):
  1. Kubernetes (listed in 64% of JDs, only 12% of rejections cite it)
  2. Scala (listed in 31%, never cited in rejections)
```

## Filters

- `/placement-ops analytics --period Q1` — Filter to a specific quarter
- `/placement-ops analytics --client Anthropic` — Single client deep-dive
- `/placement-ops analytics --archetype IC-MLE` — By role type

## Post-Analytics

1. Save the report to `reports/analytics-{YYYY-MM-DD}.md`
2. Flag actionable insights: "Your Netflix close rate is 0% over 4 submissions. Consider dropping or renegotiating."
3. Suggest calibration updates if the data reveals taxonomy adjustments
4. If time-to-fill is increasing, suggest process improvements
