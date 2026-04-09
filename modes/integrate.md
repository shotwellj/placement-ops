# Mode: Integrate

> Sync data between your ATS, HRIS, and Placement-Ops. The bridge between where your data lives and where it becomes intelligence.

## Trigger

`/placement-ops integrate`

## Why This Matters

Without integration, you're copy-pasting between your ATS and this tool. With integration, your pipeline auto-populates from Greenhouse, your candidate scores feed back into Lever, and your competitive intel updates overnight. The data flows, you make decisions.

This mode works for BOTH sides:
- **Agency mode**: Pulls jobs, candidates, and stage data from your ATS into the Placement-Ops pipeline
- **Company mode**: Pulls employee records, comp data, and hiring pipeline from your HRIS + ATS into People-Ops

## Pre-Flight

1. Load `config/integrations.yml`
2. Verify at least one integration is enabled
3. Check API key validity before syncing
4. Load `config/profile.yml` to determine agency vs. company mode

## Supported Integrations

### Tier 1: Direct API (Best data quality)

```
ATS INTEGRATIONS
═══════════════════════════════════════════════════════════

  Platform        API Type      Auth          What You Get
  ─────────────────────────────────────────────────────────
  Greenhouse      REST v1       API Key       Jobs, candidates, scorecards,
                                              offers, stage transitions
  Lever           REST v1       API Key       Postings, opportunities,
                                              feedback forms, offers
  Ashby           REST          API Key       Jobs, candidates, interview
                                              schedules, offers
  Workday         REST + OAuth  OAuth2        Workers, requisitions,
                                              compensation, candidates
  iCIMS           REST          Basic Auth    Jobs, people, workflows

HRIS INTEGRATIONS (Company mode)
═══════════════════════════════════════════════════════════

  Platform        API Type      Auth          What You Get
  ─────────────────────────────────────────────────────────
  BambooHR        REST          API Key       Employees, comp, time off,
                                              performance reviews
  Rippling        REST          API Key       Employees, comp, departments,
                                              org chart
  Workday         REST + OAuth  OAuth2        Full employee lifecycle
  HiBob           REST          API Key       Employees, comp, org data
```

### Tier 2: Universal Adapter (Easiest setup)

```
MERGE.DEV — UNIVERSAL CONNECTOR
═══════════════════════════════════════════════════════════

  Connect 50+ ATS/HRIS platforms through ONE API.
  Merge normalizes data into a common schema.

  Supported ATS:    Greenhouse, Lever, Ashby, Workday,
                    BambooHR, JazzHR, Recruiterflow,
                    Jobvite, Breezy, Pinpoint, + 40 more

  Supported HRIS:   BambooHR, Rippling, Gusto, ADP,
                    Justworks, Paychex, + 30 more

  Setup:
  1. Sign up at merge.dev (free tier: 3 linked accounts)
  2. Create an API key
  3. Link your ATS/HRIS through Merge's OAuth flow
  4. Paste the account_token into integrations.yml
  5. Run /placement-ops integrate sync

  Cost: Free for 3 accounts, $0.50/employee/month after.
  This is the recommended path for most recruiters.
```

### Tier 3: Built-in Scrapers (No API key needed)

```
PORTAL SCANNER (Already built — modes/scan.md)
═══════════════════════════════════════════════════════════

  Uses Playwright browser automation, Greenhouse API
  (public endpoints), and WebSearch to pull job postings.

  154 companies pre-configured in portals.yml.
  No API keys required.

  Feeds: New roles, JD text, posting dates, comp ranges
  Limitation: Can't see candidate data or internal pipeline
```

## Sync Flow

### Agency Mode

```
YOUR ATS (Greenhouse/Lever/Ashby)
         │
         ▼
┌─────────────────────────┐
│  /placement-ops integrate│
│  sync --mode=agency      │
│                          │
│  1. Pull open jobs       │ ──→ data/synced/jobs/
│  2. Pull candidates      │ ──→ data/synced/candidates/
│  3. Pull stage changes   │ ──→ data/synced/pipeline-ats.tsv
│  4. Pull scorecards      │ ──→ data/synced/feedback/
│  5. Pull offers          │ ──→ data/synced/offers/
│  6. Merge with local     │ ──→ data/pipeline.md (updated)
│     pipeline data        │
└─────────────────────────┘
         │
         ▼
  Placement-Ops Pipeline
  (pipeline, evaluate, track, analytics — all auto-populated)
```

### Company Mode

```
YOUR ATS                        YOUR HRIS
(Greenhouse/Lever)              (BambooHR/Rippling)
         │                              │
         ▼                              ▼
┌──────────────────────────────────────────┐
│  /placement-ops integrate                 │
│  sync --mode=company                      │
│                                           │
│  FROM ATS:                                │
│  1. Pull open requisitions  ──→ Hiring Pipeline view
│  2. Pull candidate pipeline ──→ Hiring funnel metrics
│  3. Pull interview data     ──→ Time-to-hire analytics
│  4. Pull offer data         ──→ Offer accept rate
│                                           │
│  FROM HRIS:                               │
│  1. Pull employee roster    ──→ Team Roster view
│  2. Pull compensation data  ──→ Comp distribution
│  3. Pull org structure      ──→ Workforce Plan view
│  4. Pull tenure/start dates ──→ Retention tracking
│  5. Pull performance data   ──→ Development view
│                                           │
│  MERGE + DEDUPLICATE                      │
│  6. Match ATS candidates to HRIS employees│
│  7. Build unified timeline                │
│  8. Flag data quality issues              │
└──────────────────────────────────────────┘
         │
         ▼
  People-Ops Dashboard
  (roster, onboarding, retention, competency — all live)
```

## Sync Commands

```bash
# Full sync — pull everything
/placement-ops integrate sync

# Sync only new data since last run
/placement-ops integrate sync --incremental

# Sync specific data type
/placement-ops integrate sync --only=jobs
/placement-ops integrate sync --only=candidates
/placement-ops integrate sync --only=employees

# Check connection health
/placement-ops integrate status

# Preview what would sync (dry run)
/placement-ops integrate sync --dry-run

# Force re-sync everything (ignores last sync timestamp)
/placement-ops integrate sync --full
```

## Output: Sync Status Report

```
ATS SYNC COMPLETE — Greenhouse
══════════════════════════════════════════════════════════════

  Last sync:    2026-04-08 09:15:00 UTC
  Duration:     12 seconds
  API calls:    23 (of 50/min rate limit)

  Jobs synced:          12 open requisitions
  Candidates synced:    34 active
  Stage changes:         8 since last sync
  New scorecards:        3
  Offers:                1 new (Engineering Manager)

  Pipeline changes detected:
    ✅ Marcus Rivera → moved to "Final Round" at Databricks
    ✅ Alice Chen → offer extended at Anthropic ($260K base)
    ✅ New candidate: James Park sourced for Scale AI role
    ⚠️  Priya Patel → ATS shows "Rejected" but local shows "Interviewing"
        → Resolved: ATS wins (updated local pipeline)

  Data quality:
    ✅ All candidate emails valid
    ⚠️  2 candidates missing resume attachments
    ⚠️  1 job missing comp range (Scale AI — Head of ML Eng)

  Next auto-sync: 2026-04-08 09:45:00 UTC (in 30 min)
```

## Webhook Support (Real-time)

For real-time updates instead of polling:

```yaml
# In integrations.yml, add:
webhooks:
  enabled: true
  endpoint: "http://localhost:3847/webhooks"   # Local webhook receiver
  events:
    - candidate.stage_change      # Instant pipeline updates
    - candidate.hired             # Trigger retention tracking
    - offer.created               # Alert for offer management
    - job.opened                  # New req detection
    - job.closed                  # Pipeline cleanup
```

The `scripts/webhook-server.mjs` script runs a local Express server that receives webhook events from your ATS and updates the local data files in real-time. No polling delay.

## Data Privacy

- All synced data stays LOCAL in `data/synced/`
- The `.gitignore` already excludes `data/` from version control
- API keys are in `config/integrations.yml` (also gitignored)
- No data is sent to third parties — sync is pull-only
- Webhook endpoint runs on localhost only

## Post-Sync

1. Print sync summary (shown above)
2. Update `data/pipeline.md` with any ATS changes
3. Flag conflicts between local and ATS data
4. Update `data/synced/last-sync.yml` with timestamp
5. Trigger dependent modes if significant changes detected:
   - New candidate → suggest `/placement-ops evaluate`
   - Stage change → update `/placement-ops track`
   - New job → suggest `/placement-ops scan` to check if it matches niche
   - Offer accepted → trigger `/placement-ops retention` check-in schedule
