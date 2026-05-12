# SourcingNav - Talent Engine Roadmap

> **Read this first.** This is the anchor document for the entire project.
> Every session, every contributor, every Claude instance opens this before
> writing code. It defines the vision, the architecture, what's shipped,
> what's next, and the order of operations.
>
> Last meaningful update: **2026-05-11**. When this gets stale, update it
> the same session you notice. Stale anchor docs cost real time.

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

**The lifecycle is the product. Sourcing is just the first stage shipped.**
Every architectural decision below — taxonomy, matching engine, calibration,
audit chain — must work across all 10 lifecycle stages, not just source.
If a feature only makes sense for the sourcing side, it's a feature, not
infrastructure.

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
│ 197 skills with 616 adjacency edges (live in Turso).            │
│ 15 competencies with archetypes.                                │
│ Formal scoring: match_type × recency × depth × importance.      │
│ 8-dimension rubric. Blocker detection. Compatibility matrix.    │
│ Reproducible, auditable, explainable. Not a black box.          │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 1: SOURCING SENSORS (the input funnel)                    │
│ JD Parser, Boolean Builder, Pro CI, Career Switchers, Hidden    │
│ Talent Pools, Trends/clustering, Live Intelligence Stream.      │
│ Each one is a different way that talent signal enters the       │
│ system AND surfaces back to the recruiter.                      │
└─────────────────────────────────────────────────────────────────┘
```

Each layer feeds the layer above it. Without Layer 1, the brain has nothing
to match. Without Layer 2, the dashboards have nothing to score. Without
Layer 3, the system never gets smarter. Without Layers 5-6, no one is using
it. Without Layer 7, we have no enterprise revenue.

**Layers 1-3 must be lifecycle-agnostic.** The matching engine math used
to score a candidate against a JD is the same math used to score an
interview panel against a candidate, an offer probability against a comp
gap, or a retention risk against a competency drift. One engine, ten use
cases.

## Current state (as of 2026-05-11)

### Live infrastructure
- Vercel project `placement-ops` (`prj_f4LrznUkDxYMJtPrBHVAfbSMwT5Q`,
  team `team_gp53yNb8gLQOVixkEteGZ9dI`). Manual `vercel --yes --prod`
  required (GitHub auto-deploy is not reliably wired).
- Turso DB `sourcingnav-prod` at
  `libsql://sourcingnav-prod-shotwellj.aws-us-west-2.turso.io`.
  Custom httpx-based HTTP client (libsql is broken on Vercel).
- 5 migrations applied: sessions, compliance+taxonomy, calibration,
  skill_resolution, req_outcomes.
- 44 live API endpoints.
- Resend for transactional email, domain verified at `sourcingnav.com`,
  from-address `hello@sourcingnav.com`.
- Together.ai BYOK with server-key fallback (`SERVER_TOGETHER_KEY`).
  Qwen 235B FP8.
- Anthropic SERVER_ANTHROPIC_KEY for AI calls (Haiku 4.5 primary,
  Sonnet 4.5 fallback). Validated at boot.

### Database snapshot (live)
- **197** skills, **616** adjacency edges, **15** competencies
- **47** total reqs (23 open after duplicate cleanup), **618** req_skills
  rows, **33** candidate_skills rows
- **2** outcomes logged, **109** audit_events written
- Every intake writes req_skills + compliance records ✅
- Every eval writes candidate_skills + compliance records ✅

### Layer 1 (Sourcing): ~70%
- ✅ JD Parser (`POST /api/intake`) with stratified must-haves +
  comp snapshot + canonical_skills output
- ✅ Boolean Builder generating 10 strings per JD (3 LR + 7 X-ray)
- ✅ Pro tier Boolean Strings (Dragnet + Company-Targeted)
- ✅ Competitive Intelligence (Pro CI) — full 8-card competitor analysis
  with watering holes, recruiting angles, salary positioning
- ✅ Career Switcher Archetypes + Hidden Talent Pools
- ✅ Watering Holes intelligence with hit volume + signal scoring
- ✅ Live Intelligence Stream (SSE endpoint, Stages 1/2/3-A/3-B):
  pipeline-shape signals + outcome-driven signals + predictive signals
  on current intake. 9 event types across the 3 stages.
- ✅ Trends/clustering page (Phase B3 capability, see Layer 3)
- ✅ PDF export per requisition
- ✅ Close Out Req feature (pipeline detail view, all 5 outcome types)
- ❌ Outreach Generator (designed in prior tools, not ported)
- ❌ Scan / portal crawler
- ❌ Smart Response / scheduling
- ❌ DEI Jamboard

### Layer 2 (Taxonomy + Matching Engine): ~75%
- ✅ Schema with 13+ tables (brain + compliance + outcomes + signatures)
- ✅ Seed: 197 skills, 616 adjacencies, 15 competencies in DB
- ✅ JD_PARSER_PROMPT emits canonical_skills for clean taxonomy matching
- ✅ CANDIDATE_EVAL_PROMPT emits extracted_skills with recency/depth
- ✅ Every intake writes req_skills + compliance records
- ✅ Every eval writes candidate_skills + compliance records
- ❌ **Formal matching engine math in code** (currently AI does the
  scoring via prompt rubric, NOT the formula from
  `modes/_matching-engine.md`). This is the **last open Phase A gap**.
- ❌ Medical device / automotive / aerospace taxonomy domains beyond
  what's seeded today
- ❌ Backfill script not yet run on early reqs created before req_skills
  writes were added

### Layer 3 (Calibration Loop): ~35%
- ✅ `calibration_events` table
- ✅ `audit_events` tamper-evident HMAC chain (109 events written)
- ✅ `decision_explanations` table populated for EU AI Act Article 13
- ✅ `req_outcomes` table + endpoints (POST + GET) for placement
  outcome logging
- ✅ Phase B1 calibration math in `api/_calibration.py` — Bayesian
  dampened adjacency updates from submission stage transitions.
  `POST /api/calibration/run` endpoint live.
- ✅ Phase B2 taxonomy resolution: `/api/taxonomy/unresolved`,
  `/api/taxonomy/suggestion`, `/api/taxonomy/decide` endpoints live.
  Skill promotion workflow exists.
- ✅ Phase B3 role archetype discovery: Jaccard clustering live at
  `/ui/trends.html`, signatures table populated, re-cluster admin
  endpoint, public market intel page.
- ⚠️ Phase B1 wired but unproven — calibration_events table not heavily
  populated yet because submissions are low volume. **B1 will only
  start producing visibly different adjacency weights once 5+ placement
  outcomes are logged with placed_candidate_skills.**
- ❌ Phase B2 surfaced in UI but admin-only; not yet a recurring
  workflow with notifications
- ❌ Phase B4 skill mesh co-occurrence — not built

### Layer 4 (Lifecycle Modes): ~10%
**This is the gap that defines the next 12 months of work.** Only one
of ten modes has any real implementation.

- ✅ **Match** (basic): candidate evaluation endpoint
  (`POST /api/source/evaluate`) — but uses AI scoring, not the formal
  engine. Refactor planned as the Phase A finishing piece.
- ❌ **Source**: partial — intake + Booleans + CI exist as features but
  no "source mode" UI that orchestrates them as a workflow
- ❌ **Schedule**: not built. Future feature includes native booking
  page + Google Calendar sync via MCP.
- ❌ **Interview**: not built. Interview kit generator, structured
  rubric capture, panel coordination.
- ❌ **Offer**: not built. Offer letter generation, e-sign integration,
  comp negotiation tracking.
- ❌ **Onboard**: not built. First 90 day milestones, HRIS integration.
- ❌ **Perform**: not built.
- ❌ **Develop**: not built. Career path mapping, skill gap → training.
- ❌ **Retain**: not built. Post-placement health checks, flight risk.
- ❌ **Depart**: not built. Exit interview capture, departure pattern
  analysis, sister-company placement matching.

### Layer 5 (Agency Surface): ~40%
- ✅ Auth + sessions (magic link, 30-day sessions)
- ✅ Intake form (`/app/`) with full CI + career switchers + hidden
  pools + watering holes
- ✅ Pipeline page list view (`/app/pipeline.html`)
- ✅ Pipeline page detail view (comp, skills, Booleans, sourcer notes)
- ✅ Candidate evaluation form + ranked submissions list
- ✅ Status mutations via outcome logging (Close Out Req feature)
- ✅ Trends page (Market Intelligence internal view)
- ✅ Settings page (BYOK + sessions + plan)
- ❌ The other 9 agency modes from `/ui/dashboard.html` demo
- ❌ Source mode as an orchestrated workflow (vs. one-shot intake)
- ❌ Forecast / Benchmark / Batch / Strategy / Outreach / Scan /
  Retention modes

### Layer 6 (Company Surface): 0%
- ❌ All 11 modes from `/ui/people-ops.html` demo are HTML mockups only
- ❌ No company-side onboarding flow
- ❌ No ATS handoff from agency placement to company roster
- ❌ No company-side billing infrastructure

### Layer 7 (Market Intelligence): ~5%
- ✅ Public Market Intelligence page at `/market-intel` (anonymized,
  aggregated counts from all users' parsed signatures)
- ❌ No paid enterprise customers
- ❌ No API access for enterprise integrations
- ❌ Data volume too small for enterprise-grade reports yet

## What's left in each phase (rewritten 2026-05-11)

### Phase A: Foundation - Port the brain
**Status:** ~85% done. One open gap.

The taxonomy + matching engine became first-class database tables. Every
operation writes structured data to them. Intake parser emits canonical
skills. Candidate eval extracts structured skills with recency/depth.
All compliance/audit infrastructure is live.

**The one remaining gap:** the formal matching engine math from
`modes/_matching-engine.md` is not in code. Today, candidate evaluation
returns an AI-generated `fit_score: 0-100` from a prose rubric. The
8-dimension scoring (Technical / Seniority / Location / Comp / Culture
/ Gap / Presentation / Fill Probability) with `match_type × recency ×
depth × importance` math is specified but not implemented.

**Why this matters disproportionately:**
1. Scores are non-deterministic — same candidate + same JD = different
   score each call
2. Scores can't be audited at the math level — only at the AI-call level
3. **Calibration (Layer 3, the moat) can't fully work without it**.
   Adjacency weights live in the DB and Phase B1 already updates them
   from outcomes, but the engine consuming those weights at scoring
   time is still the LLM, not code. So the weights don't yet flow into
   visible scoring differences.
4. The same engine is needed for Match mode, Interview mode, Retain
   mode, Offer mode, Develop mode — every lifecycle stage above source.
   Without it, the full-cycle product is blocked.

**What "finish Phase A" requires:**
Build `api/_matching_engine.py` as a pure Python module callable from
any lifecycle endpoint. Public interface:

- `score_skill_match(candidate_skill, req_requirement, taxonomy)`
- `score_technical_match(...)`, `detect_blockers(...)`
- `score_seniority_fit(...)`, `score_comp_alignment(...)`,
  `score_location_fit(...)` — these three from structured data, no AI
- `score_qualitative_dimensions(...)` — AI proposes Culture / Presentation
  scores, code validates and stores
- `compute_composite(eight_dimensions, weights)` with the 25/15/10/15/5/
  10/10/10 weighting from `modes/_shared.md`
- `apply_threshold(composite, has_blockers) -> SUBMIT|INTERVIEW|PASS`

Then `POST /api/source/evaluate` is refactored to call this module
instead of getting the score from a prompt. Future lifecycle endpoints
(Match mode batch, Interview rubric capture, Offer probability, Retain
risk) all call the same module.

**Acceptance test:** Same candidate + same JD evaluated twice produces
**identical** composite score within rounding. Today this is false.

**Estimated effort:** 4-6 hours. Module ~400 lines, endpoint refactor
~150 lines, tests ~200 lines.

### Phase B: Taxonomy Evolution Loop
**Status:** ~35% done. B1 scaffolding exists, B2 endpoints exist,
B3 fully shipped, B4 not started.

**B1 (skill adjacency calibration):** Math + endpoint exist in
`api/_calibration.py`. **Will not produce visible adjacency updates
until 5+ placement outcomes are logged with placed_candidate_skills.**
Phase A matching engine finishing unlocks B1 visibility because the
deterministic scorer will actually use the updated weights.

**B2 (unresolved skill promotion):** Endpoints exist
(`/api/taxonomy/unresolved`, `/decide`). Need:
- UI surface (admin page) for browsing unresolved skills with frequency
  counts
- Notification trigger when N+ raw skill names appear with skill_id=NULL
- Backfill on approval so existing rows link to the new canonical ID

**B3 (role archetype discovery):** Shipped. Jaccard clustering live at
`/ui/trends.html` plus public version at `/market-intel`. Re-cluster
admin endpoint. **The noise pile (singletons not in any cluster) is the
emerging-archetype surface that no competitor ships.** At N=47 the
clusters are mostly duplicate-driven. Real cross-company archetypes
will emerge as N grows past 100.

**B4 (skill mesh co-occurrence):** Not built. Lower priority until
B1 is producing measurable improvements.

### Phase C: Agency Mode Buildout
**Status:** ~25% done.

Modes shipped beyond intake/pipeline/eval:
- Pro Competitive Intelligence ✅
- Career Switcher Archetypes ✅
- Hidden Talent Pools ✅
- Pro Boolean Strings (Dragnet + Company-Targeted) ✅
- Trends/Market Intelligence (internal) ✅
- Live Intelligence Stream ✅

Modes still missing from the original `/ui/dashboard.html` vision:
1. **Benchmark** — comp ranges + skill scarcity from own corpus. Closest
   to shipping; the data is already aggregated for the Trends page.
2. **Forecast** — hiring signal detection + 30/60/90 day predictions.
3. **Analytics** — funnel, time-to-fill, revenue per hour, client
   scoreboard.
4. **Batch** — rank N candidates against one JD.
5. **Strategy** — workforce plans for clients.
6. **Outreach Generator** — personalized message variants tied to JD.
7. **Scan** — career portal crawler.
8. **Retention** — post-placement health checks.

### Phase D: Company Surface Launch
**Status:** 0%. Mockup HTML only.

Cannot start meaningfully until Phase A matching engine is finished,
because the Company surface IS the lifecycle in action — every Company
mode (Roster, Onboarding, Retention, Development, Engagement, Workforce
Plan) is the same matching engine scoring different inputs.

### Phase E: Lifecycle Completion (Layer 4 modes)
**Status:** 0%. This is the biggest unbuilt piece of the product.

Each lifecycle stage becomes a mode that writes structured data to the
brain. **Every mode uses the matching engine from Phase A.** The
matching engine is the API between lifecycle stages — Source produces
candidates ranked by the engine; Match scores them deeper; Interview
captures structured feedback that calibrates the engine; Offer predicts
acceptance using the engine; Onboard scores early-tenure signals; Retain
detects drift; Develop maps gaps to training; Depart matches to sister
companies.

Without finishing Phase A, Phase E is impossible. With Phase A finished,
each lifecycle mode becomes a focused 10-20 hour build.

### Phase F: Market Intelligence Product
**Status:** ~5%. Public page exists. No paid revenue.

Cannot grow into a real $50-500k/yr enterprise product until Phases C,
D, E produce 6-12 months of cross-customer data.

## Phase ordering (revised)

**Old order:** A → B → C and D in parallel → E → F
**New order:** Finish A → C/B1-visibility together → C/E in parallel → D → F

**Rationale for the change:**

1. **Finishing A is the unlock.** Every lifecycle mode (Phase E),
   every Company-side feature (Phase D), and Phase B1's visible impact
   on scoring all depend on the deterministic matching engine.

2. **C and B work together, not sequentially.** Building Benchmark mode
   (C #1) produces aggregated data that B3 archetype discovery can use.
   Building Outreach Generator (C #6) needs the matching engine output
   to personalize messages. Building Analytics (C #3) requires outcome
   data to be flowing. Each Phase C mode pulls B forward.

3. **E (the lifecycle modes beyond source) is the actual product.**
   The original roadmap treated E as a "fill in the missing stages"
   late phase. That undersold it. **E is what makes SourcingNav a
   Talent OS rather than a sourcing tool.** Schedule, Interview, Offer,
   Onboard, Retain, Develop, Depart — each of these is a multi-billion
   dollar market on its own. The reason the cross-company calibration
   moat is defensible is because we own all 10 stages.

4. **D (Company Surface) comes after E gets started.** A 50-person
   company isn't going to pay $499/mo for a Roster page. They'll pay
   for a Roster page that flags flight risk (Retain mode), suggests
   the next 4 hires (Workforce Plan), and benchmarks team comp
   (Benchmark). Build the lifecycle modes first; the Company surface
   becomes valuable as their consumer.

5. **F is downstream of everything.** Cannot exist as a product until
   the data exists.

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
   outcome event it writes to `calibration_events` or `req_outcomes`
   so the system gets smarter from its use.

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
   that powers the paid Company SKU and the enterprise Layer 7 product.

6. **Build for the lifecycle, not just the stage you're shipping.**
   Every Layer 1-3 module must be callable from any lifecycle stage.
   If a feature only makes sense for source, it doesn't belong in
   shared infrastructure; build it as a Source-mode-specific feature
   and don't pollute the engine.

7. **No em dashes in user-facing copy.** Use commas, colons, parens,
   or sentence breaks. (Project-wide style rule.)

8. **Update this file the session you notice it's stale.** Anchor
   docs that drift cost real time in every future session.

## Anti-goals (things we are explicitly NOT building)

To stay focused, here is what SourcingNav is NOT:

- **Not a Gem clone.** Smart-response and outreach generation are
  features inside the lifecycle, not the product. We do not chase
  Gem's 800M+ profile index. Per-search universe curation (GitHub API,
  Google Scholar, Crunchbase, conferences, patents) + PSE retrieval +
  LLM rubric scoring achieves ~70% of Gem's capability at a fraction
  of the cost.
- **Not an LLM wrapper.** The brain is a structured taxonomy with
  formal scoring math, not "ask Claude to evaluate this candidate."
  When Phase A is finished, this is provably true.
- **Not a generic ATS.** ATS functionality emerges naturally from the
  Company surface owning the lifecycle. We do not compete with
  Greenhouse on req management features.
- **Not a job board.** We do not help candidates find jobs. We work
  for the recruiter and the company.
- **Not a single-feature SaaS.** If a feature can be built as a
  standalone tool that doesn't feed the brain, it doesn't belong here.

## Where each session should start

1. Read this file (`ROADMAP.md`) first
2. Check current phase status (the "Current state" section)
3. Pick the smallest concrete task from the current priority
4. Apply the operating principles above to validate the task
5. Build, test, ship, update the "Current state" section in the same
   commit. Do not let the anchor doc drift.

The next concrete task right now is **finish Phase A**: build
`api/_matching_engine.py` and refactor candidate evaluation to use it.

Do NOT start with "what features should we add?" That question has an
answer in this document.

## File map for future sessions

Critical reference files:
- `ROADMAP.md` (this file) — read first, always
- `taxonomy/skills.yml` and `taxonomy/skills_*.yml` — skill taxonomy
  with adjacency (197 skills live)
- `taxonomy/competencies.yml` — 15-competency framework
- `modes/_matching-engine.md` — formal scoring algorithm spec
- `modes/_shared.md` — 8-dimension rubric, role archetypes, rules
- `api/index.py` — live backend (44 endpoints)
- `api/_calibration.py` — Phase B1 adjacency calibration math
- `api/_compliance.py` — audit chain, EU AI Act Article 12-13
- `api/_skill_resolution.py` — Phase B2 taxonomy resolution
- `api/_matching_engine.py` — (to be built) lifecycle-wide scoring
- `app/index.html` — intake form (live)
- `app/pipeline.html` — pipeline list + detail + candidate eval +
  Close Out Req
- `app/settings.html` — BYOK + sessions
- `app/print.html` — PDF export template
- `ui/dashboard.html` — Agency surface vision (demo only)
- `ui/people-ops.html` — Company surface vision (demo only)
- `ui/trends.html` — Trends/clustering Market Intelligence (live)
- `ui/knowledge-hub.html` — Knowledge Hub / marketing surface
- `scripts/schema.sql` — base DB schema
- `scripts/migration_002` through `migration_006.sql` — applied migrations
- `scripts/cleanup_duplicate_reqs.py` — idempotent duplicate cleanup
- `docs/strategy/` — strategic planning docs

## Glossary

- **Agency** — recruiter user, paying $49/mo Pro
- **Company** — HR/People team user, paying $499-$2k/mo
- **Brain** — Layers 2 + 3 (taxonomy + matching engine + calibration)
- **Sensor** — Layer 1 sourcing tool that feeds the brain
- **Mode** — a discrete capability mapped to a UI page (e.g.,
  "evaluate mode," "calibrate mode," "retain mode")
- **Lifecycle** — the 10 employment stages from source to depart
- **Calibration event** — a placement, rejection, retention milestone,
  or departure that feeds back into the brain
- **Adjacency** — taxonomy relationship between skills (e.g., PyTorch
  ↔ TensorFlow = 0.6) that allows partial credit matching
- **Signature** — the bag-of-features representation of a parsed JD
  used for Jaccard clustering (canonical_skills + adjacent_crossover)
- **Outcome** — a req closing event (filled / lost / cancelled /
  fell_off / reopened) logged via the Close Out Req feature

---

*Last meaningful update: 2026-05-11. Reflects Live Intelligence Stream
shipped, Pro CI shipped, Trends/clustering shipped, Close Out Req
shipped, duplicate cleanup applied (47 → 23 open reqs), Phase B1
calibration scaffolded, Phase B2 endpoints live, Phase A matching
engine finishing identified as the next priority.*
