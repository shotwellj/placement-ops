# SourcingNav — Talent Engine Roadmap

> **Read this first.** This is the anchor document for the entire project.
> Every session, every contributor, every Claude instance opens this before
> writing code. It defines the vision, the architecture, what's shipped,
> what's next, and the order of operations.

## What we are building

SourcingNav is a **unified Talent Operating System** that covers the full
employee lifecycle:

```
SOURCE → SCHEDULE → MATCH → INTERVIEW → OFFER →
ONBOARD → PERFORM → DEVELOP → RETAIN → DEPART
```

Today, companies use 15+ fragmented point solutions to cover this lifecycle.
LinkedIn for sourcing. Calendly for scheduling. Greenhouse for ATS. DocuSign
for offers. Workday for HRIS. Lattice for performance. CultureAmp for
engagement. None of them talk to each other. None of them get smarter from
the others' data.

SourcingNav replaces all of them with one platform where every stage's data
makes every other stage smarter.

## Why this wins

The competitive moat is **cross-company calibration data spanning the full
lifecycle**. No internal recruiting org (even Meta's) can build this because
they only see one company. No point solution (LinkedIn, Greenhouse, Workday)
can build this because they only see one stage. Only a unified platform
covering both dimensions has the data to predict, calibrate, and benchmark
across the entire industry.

The strategic frame everywhere in the product: **proactive vs reactive**.
Today's recruiting is reactive. Req opens, recruiter scrambles, candidates
get cold-blasted, most get ghosted. SourcingNav makes it proactive: the
data tells you what's coming 30/60/90 days before the req opens.

## The 7-layer architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 7: MARKET INTELLIGENCE PRODUCT                            │
│ Sold to enterprise workforce planning teams.                    │
│ $50k-$500k/yr contracts.                                        │
│ Powered by aggregated, anonymized data from Layers 1-6.         │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 6: COMPANY SURFACE (people-ops.html + ATS modes)          │
│ Hiring manager / VP People product.                             │
│ Roster, onboarding, performance, retention, departure.          │
│ Internal lifecycle ownership. $499-$2k/mo per company.          │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 5: AGENCY SURFACE (dashboard.html)                        │
│ Recruiter product.                                              │
│ External lifecycle: source → present → place → retain.          │
│ Free tier + Pro $49/mo.                                         │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 4: LIFECYCLE MODES                                        │
│ Every stage of employment is a mode that writes structured      │
│ data to the brain.                                              │
│ Source / Schedule / Match / Interview / Offer / Onboard /       │
│ Perform / Develop / Retain / Depart                             │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 3: CALIBRATION LOOP (the moat that compounds)             │
│ Every placement → adjacency scores adjust.                      │
│ Every rejection → company-specific rules learn.                 │
│ Every retention milestone → competency weights refine.          │
│ Every comp data point → market benchmarks sharpen.              │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 2: TAXONOMY + MATCHING ENGINE (the brain)                 │
│ 150+ skills with adjacency edges (skills.yml).                  │
│ 14 competencies with archetypes (competencies.yml).             │
│ Formal scoring: match_type × recency × depth × importance.      │
│ 8-dimension rubric. Blocker detection. Compatibility matrix.    │
│ Reproducible, auditable, explainable. Not a black box.          │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 1: SOURCING SENSORS (the input funnel)                    │
│ JD Parser, Boolean Builder, Outreach Generator, Scan, Smart     │
│ Response, DEI Jamboard, Competitive Intel.                      │
│ Built in prior tools. Each one is a different way that talent   │
│ signal enters the system.                                       │
└─────────────────────────────────────────────────────────────────┘
```

Each layer feeds the layer above it. Without Layer 1, the brain has nothing
to match. Without Layer 2, the dashboards have nothing to score. Without
Layer 3, the system never gets smarter. Without Layers 5-6, no one is using
it. Without Layer 7, we have no enterprise revenue.

## Current state (as of April 2026)

### What is shipped (live at sourcingnav.com)

**Layer 1 (Sourcing):** ~30%
- ✅ JD Parser endpoint (`POST /api/intake`) with stratified must-haves + comp snapshot
- ✅ Boolean Builder generating 10 strings per JD (3 LR + 7 X-ray)
- ❌ Outreach Generator (designed in prior tools, not ported)
- ❌ Scan / portal crawler (designed, not built)
- ❌ Smart Response / scheduling (designed, not built)
- ❌ DEI Jamboard (designed, not built)
- ❌ Competitive Intelligence (designed, not built)

**Layer 2 (Taxonomy + Matching Engine):** 0%
- ❌ Skills table (currently freeform strings inside JSON blobs)
- ❌ Competencies table
- ❌ Adjacency edges
- ❌ Formal matching algorithm in code (currently AI-only via prompt)
- ✅ All design lives in `taxonomy/skills.yml`, `taxonomy/competencies.yml`,
  `modes/_matching-engine.md` — needs porting to DB

**Layer 3 (Calibration Loop):** 0%
- ❌ Outcome tracking events
- ❌ Adjacency weight adjustments
- ❌ Company-specific rule learning

**Layer 4 (Lifecycle Modes):** ~10%
- ✅ Match (basic — candidate evaluation endpoint)
- ❌ Source, Schedule, Interview, Offer, Onboard, Perform, Develop, Retain, Depart

**Layer 5 (Agency Surface):** ~20%
- ✅ Auth + sessions (magic link, 30-day sessions, full session management)
- ✅ Intake form (`/app/`)
- ✅ Pipeline page list view (`/app/pipeline.html`)
- ✅ Pipeline page detail view (comp, skills, Booleans, sourcer notes)
- ✅ Candidate evaluation form + ranked submissions list
- ❌ Status mutations (mark req as Open/Placed/Closed)
- ❌ The other 9 modes from /ui/dashboard.html demo

**Layer 6 (Company Surface):** 0%
- ❌ All 11 modes from /ui/people-ops.html demo are HTML mockups only

**Layer 7 (Market Intelligence):** 0%
- ❌ Cannot exist until Layers 2-4 are populated with real data

### What infrastructure exists
- Vercel deployed (project `placement-ops`, manual `vercel --yes --prod`)
- Turso DB `sourcingnav-prod` with tables for users, organizations,
  requisitions, candidates, submissions, sessions, login_attempts,
  employees, scan_results, signals, recommendations, activity_log,
  meetings, outreach_messages, calibration_events, style_edits,
  schema_migrations
- Custom httpx-based Turso HTTP client (libsql is broken on Vercel)
- Together.ai BYOK with Qwen 235B (FP8 model name required)
- Resend for magic-link emails (sandbox sender, sourcingnav.com domain
  not yet verified — only delivers to info@nostalgicskinco.com)

## The build phases (in order)

The phases are ordered by **what unlocks what**, not by what's most fun
to build. Layer 2 (the brain) must exist before Layers 3-7 can deliver
their value. So we port the brain first.

### Phase A: Foundation — Port the brain
**Goal:** The taxonomy + matching engine become first-class database
tables, and every operation writes structured data to them.

**Estimated effort:** 12-16 hours across 2-3 sessions.

**Tasks:**
1. Schema migration: `skills`, `skill_aliases`, `skill_adjacencies`,
   `competencies`, `req_skills`, `candidate_skills`,
   `submission_dimensions`, `comp_observations`, `taxonomy_events`
2. Seed: import `taxonomy/skills.yml` (150+ skills) and
   `taxonomy/competencies.yml` (14 competencies) into the DB
3. Refactor JD_PARSER_PROMPT to also output structured skill names +
   importance, write to `req_skills` on every intake
4. Refactor CANDIDATE_EVAL_PROMPT to output structured skills + recency
   + depth, then apply the formal matching engine math in CODE (not in
   prompt) to compute the 8-dimension scores
5. Backfill: re-run intake on existing reqs (Qualcomm, etc.) to populate
   the new tables for data we already have

**Acceptance criteria:**
- Every intake from Phase A onward produces structured, queryable data
- Recruiter UX is unchanged but the data layer underneath is real
- Can run a SQL query like "show me all candidates with PyTorch + transformers"
  and get accurate results
- Compatibility matrix on candidate evaluation shows the formal math, not
  just AI-generated rich text

**Why this is first:** Nothing else compounds without it. Every intake we
run today without the taxonomy is data we cannot aggregate later.

### Phase B: Calibration + Outcomes — Make the brain learn
**Goal:** Add the feedback loop. Every placement, rejection, and retention
event adjusts the brain's weights.

**Estimated effort:** 8-12 hours across 2 sessions.

**Tasks:**
1. Status mutations: mark req as Open/Placed/Closed; mark candidate as
   Submitted/Interviewed/Placed/Rejected with rejection reason
2. Outcome event log: every status change writes to `calibration_events`
3. Calibration job (cron or on-demand): aggregate outcomes, adjust
   adjacency scores, identify company-specific patterns
4. Calibrate UI on /app/: recruiter sees "Stripe rejected 3 candidates
   citing Spark — should we treat it as required for Stripe?"
5. Adjacency adjustments persist back to the `skill_adjacencies` table

**Acceptance criteria:**
- Recruiter can log a placement outcome in <30 seconds
- After 5+ outcomes, the system surfaces calibration insights
- Adjacency weights visibly change based on data, not guesses

**Why this is next:** This is the moat. Without it, SourcingNav is a
wrapper around an LLM. With it, the system gets sharper than any
competitor every week.

### Phase C: Agency Mode Buildout — Make /ui/dashboard.html real
**Goal:** The 9 missing agency modes from the dashboard demo become
real, backed by the brain and the calibration loop.

**Estimated effort:** 25-35 hours across 4-6 sessions.

**Priority order (highest data value first):**
1. **Benchmark** — Given a JD, query `taxonomy_events` to show comp ranges
   + skill scarcity from your own data + public sources. Sells itself
   to recruiters because it's an immediate "wow."
2. **Forecast** — Hiring signal detection + 30/60/90 day predictions per
   company. The proactive vs reactive value prop made real.
3. **Analytics** — Funnel, time-to-fill, revenue per hour, client
   scoreboard. Recruiter retention feature.
4. **Batch** — Rank N candidates against one JD. High utility for
   sourcing days.
5. **Strategy** — Workforce plans for clients. Bridge product to selling
   on the Company side.
6. **Outreach Generator** — Personalized message variants tied to the
   JD's key requirements. Phase 2 marquee feature from old plan.
7. **Scan** — Career portal crawler. Biggest engineering project in this
   phase, defer until last unless it's a deal-closer.
8. **Retention** — Post-placement health checks for placed candidates.
9. **Market Intel (recruiter view)** — Aggregated market data, recruiter-
   facing version of Layer 7.

**Acceptance criteria:**
- Every panel in `/ui/dashboard.html` has a real backend behind it
- A recruiter can run a full week's work entirely inside SourcingNav
- The Pro tier ($49/mo) becomes worth paying for

### Phase D: Company Surface Launch — Make /ui/people-ops.html real
**Goal:** Launch the Company SKU as a separate paid product. This is
where ATS functionality lives.

**Estimated effort:** 20-30 hours across 3-5 sessions.

**Tasks:**
1. New schema for company-side: extend `employees` table, add
   `departures`, `engagement_scores`, `competency_assessments`,
   `hiring_roadmap_items`, `workforce_plan_targets`
2. Onboarding flow for company users (different from recruiter
   onboarding): import roster from CSV or HRIS, auto-tag competencies,
   set up hiring pipeline
3. Build the People-side modes:
   - Roster (team composition)
   - Onboarding (first 90 days)
   - Retention (flight risk monitor)
   - Development (promotion readiness)
   - Competency Map (heat map by team)
   - Engagement (quarterly trends)
   - Workforce Plan (hiring roadmap)
   - Hiring Pipeline (where they meet the Agency side)
   - Analytics (funnel)
4. ATS handoff: when a recruiter places a candidate, the candidate
   flows directly into the company's roster with all matching data
   preserved. No CSV export. No re-keying.
5. Pricing infrastructure: $499-$2k/mo per company depending on
   headcount

**Acceptance criteria:**
- A hiring manager at a 50-person company can see team composition,
  identify flight risks, plan next 4 hires, benchmark against market
- ATS handoff from Agency to Company is one-click
- Company SKU has its own onboarding, billing, support

### Phase E: Lifecycle Completion — Fill in the missing stages
**Goal:** Every stage of the lifecycle has a mode.

**Estimated effort:** 15-25 hours across 3-4 sessions.

Stages still needing modes after Phases A-D:
- **Schedule** — native booking page (sourcingnav.com/book/[slug]),
  Google Calendar sync via MCP
- **Interview** — interview kit generator, structured rubric capture,
  panel coordination
- **Offer** — offer letter generation, e-sign integration, comp
  negotiation tracking
- **Onboard** — first 90 day milestones, integration with payroll/HRIS
- **Perform** — performance review cycles, goal tracking
- **Develop** — career path mapping, skill gap → training recommendations
- **Depart** — exit interview capture, departure pattern analysis

### Phase F: Market Intelligence Product — The B2B revenue engine
**Goal:** Layer 7 launches as a separate enterprise product.

**Estimated effort:** 40+ hours, mostly later (post 6-12 months of data).

**Tasks:**
1. Aggregated dashboards: skill demand by week, comp cluster trends,
   talent flow by region, hiring velocity by company tier
2. Custom reports: quarterly comp benchmarks, role scarcity reports,
   industry talent maps
3. API access: Workday/Greenhouse integrations for enterprise customers
   to pull data
4. Sales motion: outbound to VP Talent / VP People at Series B+ companies
   and Fortune 1000 workforce planning teams
5. Pricing: $50k-$500k/yr contracts

**Acceptance criteria:**
- Paying enterprise customers logging in monthly to pull data they
  cannot get anywhere else
- Data quality competitive with or better than Radford, Levels.fyi,
  Payscale
- Real-time updates that survey-based competitors cannot match

## Phase ordering rationale

A → B → C and D in parallel → E → F

- **A first** because nothing compounds without the brain
- **B second** because the calibration loop is the moat and needs data
  flowing through it as early as possible
- **C and D in parallel** after A+B. The agency side drives data volume
  (recruiters using it daily). The company side drives revenue per
  customer (higher-ticket subscriptions). Both feed Layer 7 data.
- **E to fill gaps** in the lifecycle once core surfaces are real
- **F when data is ripe** — typically after 6-12 months of Layers A-E
  generating volume

## Operating principles

These are non-negotiable. They go in every PR description and every
session prompt.

1. **Every feature evaluated against three questions:**
   - Does it write structured data the brain can use?
   - Does it grow data volume?
   - Does it lock in daily usage on Agency or Company side?

   If a proposed feature can't answer YES to at least one, it's a
   distraction. Don't build it.

2. **Calibration is the moat.** Every shipped feature must have an
   outcome event it writes to `calibration_events` so the system gets
   smarter from its use.

3. **Proactive vs reactive at the system level.** Every UX decision
   should make the user MORE forward-looking, not less. If we're
   shipping a feature that helps the user react faster to something
   that already happened, we're missing the point.

4. **The taxonomy is shared infrastructure.** Don't let any AI prompt
   invent skill names, comp ranges, or competencies on its own. The
   model returns proposals, code resolves them against the taxonomy,
   structured data goes to the DB.

5. **Recruiters are sensors, companies are customers, the data is the
   product.** The free/Pro recruiter tier exists to drive data volume
   that powers the paid Company SKU and the enterprise Layer 7
   product. Decisions about the recruiter tier should optimize for
   data quality and volume, not just recruiter happiness.

## Where each session should start

1. Read this file (`ROADMAP.md`) first
2. Check current phase status (the "What is shipped" section)
3. Pick the smallest concrete task from the current phase
4. Apply the operating principles above to validate the task
5. Build, test, ship, update the "What is shipped" section

Do NOT start with "what features should we add?" That question already
has an answer in this document.

## Anti-goals (things we are explicitly NOT building)

To stay focused, here is what SourcingNav is NOT:

- **Not a Gem clone.** Smart-response and outreach generation are
  features, not the product.
- **Not an LLM wrapper.** The brain is a structured taxonomy with formal
  scoring math, not "ask Claude to evaluate this candidate."
- **Not a generic ATS.** ATS functionality emerges naturally from the
  Company surface owning the lifecycle. We don't compete with Greenhouse
  on req management features.
- **Not a job board.** We don't help candidates find jobs. We work for
  the recruiter and the company.
- **Not a single-feature SaaS.** If a feature can be built as a
  standalone tool that doesn't feed the brain, it doesn't belong here.

## File map for future sessions

Critical reference files in this repo:
- `ROADMAP.md` (this file) — read first, always
- `taxonomy/skills.yml` — the 150+ skill taxonomy with adjacency
- `taxonomy/competencies.yml` — the 14-competency framework
- `modes/_matching-engine.md` — the formal scoring algorithm
- `modes/_shared.md` — the 8-dimension rubric, role archetypes, rules
- `api/index.py` — the live backend
- `app/index.html` — intake form (live)
- `app/pipeline.html` — pipeline list + detail + candidate eval (live)
- `app/settings.html` — BYOK + sessions (live)
- `ui/dashboard.html` — Agency surface vision (demo only)
- `ui/people-ops.html` — Company surface vision (demo only)
- `scripts/schema.sql` — base DB schema
- `scripts/migration_002_sessions.sql` — sessions migration

## Glossary

- **Agency** — recruiter user, paying $49/mo Pro
- **Company** — HR/People team user, paying $499-$2k/mo
- **Brain** — Layers 2 + 3 (taxonomy + matching engine + calibration)
- **Sensor** — Layer 1 sourcing tool that feeds the brain
- **Mode** — a discrete capability mapped to a UI page (e.g., "evaluate
  mode," "calibrate mode")
- **Lifecycle** — the 10 employment stages from source to depart
- **Calibration event** — a placement, rejection, retention milestone,
  or departure that feeds back into the brain
- **Adjacency** — taxonomy relationship between skills (e.g., PyTorch ↔
  TensorFlow = 0.6) that allows partial credit matching

---

*Last updated: April 2026. When this gets stale, update it.*
