# Mode: Track

> Update pipeline status, log events, and maintain the single source of truth.

## Trigger

`/placement-ops track`

## What This Does

Handles all pipeline state changes — new reqs, candidate status updates, interview feedback, offers, placements, and rejections. This is the bookkeeping engine.

## Pre-Flight

1. Load `data/pipeline.md`
2. Load `data/submissions.tsv`
3. Load `config/profile.yml`
4. Load `templates/states.yml` for valid status transitions

## Commands

The recruiter tells you what happened, and you update the pipeline:

### Adding a New Req

Recruiter says: "New req — [company] is looking for a [role]"

→ Add a row to `data/pipeline.md`:

```markdown
| 048 | 2026-04-08 | Stripe | Senior Data Scientist | manual | high | New | — | SF/Remote. Fraud team. Contact: hiring-manager@stripe.com |
```

### Updating Candidate Status

Recruiter says: "Jane got a phone screen at Anthropic" or "They passed on John for the Stripe role"

→ Update the relevant row and log the event:

Valid status flow:
```
New → Evaluating → Prepped → Submitted → Phone Screen → Onsite →
  → Offer → Accepted → Started → Guarantee Complete
  → Rejected (at any stage)
  → Withdrawn (candidate pulls out)
  → On Hold (client pauses the search)
```

### Logging Interview Feedback

Recruiter says: "HM said Jane was strong technically but they want someone with more Spark experience"

→ Append to the report in `reports/`:

```markdown
## Interview Feedback — [Date]
**Stage**: Phone Screen
**Interviewer**: [Name if known]
**Outcome**: Advancing to onsite
**Notes**: "Strong technically. HM wants more Spark depth. Prep Jane on Spark talking points before onsite."
**Action**: Schedule onsite prep session with candidate
```

### Logging an Offer

Recruiter says: "Jane got an offer from Anthropic — $195K base, $50K RSU, Senior MLE"

→ Update pipeline status to "Offer"
→ Log offer details in report
→ Calculate placement fee:

```markdown
## Offer Details — [Date]
- Base: $195,000
- Equity: $50,000/year RSU
- Level: Senior MLE (IC5)
- Start date: TBD
- **Estimated fee**: $195,000 × 20% = $39,000
- **Guarantee period**: 90 days from start date
```

### Logging a Placement

Recruiter says: "Jane started at Anthropic today"

→ Update status to "Started"
→ Calculate guarantee expiration date
→ Set reminder: "90-day guarantee check — [expiry date]"
→ Log in `data/placements.tsv`:

```
2026-04-08\tJane Smith\tAnthropic\tSenior MLE\t195000\t39000\t2026-07-07
```

## Pipeline Integrity Checks

Every time track mode runs, verify:

1. **No orphaned entries**: Every pipeline row has a matching report (if status ≥ Evaluating)
2. **No stale submissions**: Flag any submission older than 5 business days with no status update
3. **No missing follow-ups**: Flag any "Submitted" entry without a scheduled follow-up
4. **Sequential numbering**: No gaps or duplicates in the # column
5. **Valid statuses**: Every status is in the valid list from `templates/states.yml`

Print any integrity issues found.

## Follow-Up Queue

At the end of every track update, print the follow-up queue:

```
FOLLOW-UPS DUE:
  1. [OVERDUE] Stripe — Senior DS — Submitted 4 days ago, no response
  2. [TODAY] Anthropic — Staff MLE — Onsite was yesterday, get feedback
  3. [TOMORROW] Netflix — ML Manager — Phone screen scheduled
```
