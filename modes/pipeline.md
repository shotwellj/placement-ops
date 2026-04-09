# Mode: Pipeline

> Dashboard view of all active searches, candidates, and submissions.

## Trigger

`/placement-ops pipeline`

## What This Does

Gives the recruiter a single-screen overview of everything in motion. This is the "glance at your desk" view.

## Pre-Flight

1. Load `data/pipeline.md`
2. Load `data/submissions.tsv`
3. Load `data/placements.tsv` (if exists)
4. Load `config/profile.yml` for fee calculations

## Dashboard Sections

### 1. Pipeline Summary

```
PIPELINE OVERVIEW — 2026-04-08
══════════════════════════════════════
Active Reqs:          12
  New (not yet worked): 4
  In Progress:          6
  On Hold:              2

Active Candidates:    18
  Submitted:            8
  Interviewing:         5
  Offer Stage:          1

Estimated Pipeline Value: $287,000
  (sum of estimated fees for all active submissions)
```

### 2. Active Reqs Table

```markdown
| # | Company | Role | Priority | Candidates | Status | Days Open | Est. Fee |
|---|---------|------|----------|------------|--------|-----------|----------|
| 041 | Anthropic | Senior MLE | HIGH | 2 submitted | Interviewing | 14 | $39K |
| 043 | Stripe | Staff DS | HIGH | 1 prepped | Submitted | 7 | $48K |
| 045 | Databricks | ML Manager | MED | 0 | New | 3 | $44K |
| 047 | Scale AI | Data Eng | MED | 3 evaluated | Evaluating | 1 | $32K |
```

### 3. Hot Candidates (in active interview processes)

```markdown
| Candidate | Company | Role | Stage | Last Update | Next Step |
|-----------|---------|------|-------|-------------|-----------|
| Jane Smith | Anthropic | Senior MLE | Onsite scheduled | Apr 6 | Prep call Apr 9 |
| John Doe | Stripe | Staff DS | Submitted | Apr 5 | Follow up Apr 9 |
| Alice Chen | Anthropic | Senior MLE | Phone screen done | Apr 7 | Awaiting feedback |
```

### 4. Action Items

Things that need attention right now:

```
ACTION ITEMS:
  🔴 OVERDUE: Follow up with Stripe on John Doe submission (3 days, no response)
  🟡 TODAY: Prep call with Jane Smith before Anthropic onsite
  🟡 TODAY: Evaluate 2 new candidates for Scale AI Data Eng req
  🟢 TOMORROW: Scan portals (last scan: 2 days ago)
  🟢 THIS WEEK: 4 new reqs need initial candidate sourcing
```

### 5. Revenue Tracker

```
REVENUE — 2026
══════════════════
Placements closed:     3
Revenue invoiced:      $98,000
Revenue collected:     $65,000
Revenue outstanding:   $33,000

Active pipeline value: $287,000
  Submitted (60% prob): $172,200
  Interviewing (40%):   $68,880
  Offer stage (80%):    $39,200

Projected Q2 close:    ~$120,000
```

### 6. Stale Items

Flag anything that's gone cold:

```
STALE — No activity in 7+ days:
  - Req #038: Netflix ML Manager — last touch Apr 1
  - Req #040: Notion Senior DS — submitted Apr 2, no response
  - Candidate: Bob Wilson — evaluated for 2 roles, never prepped
```

## Filters

The recruiter can ask for filtered views:

- `/placement-ops pipeline --company Anthropic` — Show only Anthropic activity
- `/placement-ops pipeline --status submitted` — Show only submitted candidates
- `/placement-ops pipeline --priority high` — Show only high-priority reqs
- `/placement-ops pipeline --stale` — Show only items needing attention

## Rules

- Always show the most actionable items first
- Calculate estimated fees using midpoint salary × rate from profile.yml
- Flag overdue follow-ups prominently — these are money on the table
- If pipeline is empty, suggest running `/placement-ops scan`
