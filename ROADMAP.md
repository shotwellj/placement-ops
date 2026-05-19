# SourcingNav - Talent Engine Roadmap

> **Read this first.** This is the anchor document for the entire project.
> Every session, every contributor, every Claude instance opens this before
> writing code. It defines the vision, the architecture, what's shipped,
> what's next, and the order of operations.
>
> Last meaningful update: **2026-05-19**. When this gets stale, update it
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

## Current state (as of 2026-05-19)

### Live infrastructure
- Vercel project `placement-ops` (`prj_f4LrznUkDxYMJtPrBHVAfbSMwT5Q`,
  team `team_gp53yNb8gLQOVixkEteGZ9dI`). Manual `vercel --yes --prod`
  required (GitHub auto-deploy is not reliably wired).
- **Function timeout extended to 300s** via `vercel.json` `functions.maxDuration`.
  Was 60s default; Source eval + engine writes regularly hit 45-70s on
  candidates with deep skill graphs, causing browser timeouts even when
  the backend succeeded. 300s gives real headroom.
- Turso DB `sourcingnav-prod`. Custom httpx-based HTTP client.
- 5 migrations applied.
- Resend for transactional email, verified `sourcingnav.com`,
  from-address `hello@sourcingnav.com`.
- AI: Anthropic Claude Haiku 4.5 primary, Sonnet 4.5 fallback via
  `_call_with_failover`. Together.ai dropped from primary path (kept
  in BYOK back-compat only).
- **Prompt module live**: 3 versioned prompts (JD_PARSER, BOOLEAN_BUILDER,
  CANDIDATE_EVAL) in `api/_prompts.py`. Audit chain hashes prompt body
  for version traceability per AI Act Article 11.
- **Toolkit registry live**: 5 Pydantic-documented capabilities in
  `api/_toolkit.py`. MCP-ready for future Pro customer integrations.
- **JSON repair wrapper live**: `call_ai_json` retries malformed AI
  output with repair instruction. Wired into all 3 AI-decision routes.

### Database snapshot (live)
- **227** skills, **682** adjacency edges, **15** competencies
- **42** total reqs (1 closed, 18 open, 23 cancelled after dup cleanup)
- **634** req_skills (360 resolved = 56%)
- **61** candidate_skills (36 resolved = 59%)
- **9** evaluated submissions, **2** submission_dimensions for Nuve Controls
  Steve Benz eval (both successful end-to-end, near-duplicates from
  retry-on-timeout)
- **4** outcomes logged (2 cancelled, 2 lost; 2 placeable). **1 more
  placeable outcome unlocks Fill Probability calibration.**

### Layer 1 (Sourcing): ~80%
- ✅ JD Parser, Boolean Builder, Competitive Intelligence
- ✅ Career Switcher Archetypes + Hidden Talent Pools
- ✅ Watering Holes intelligence
- ✅ Trends/clustering page
- ✅ **30 canonical skills + 66 adjacencies added** (embedded systems
  coverage: I2C, SPI, UART, TCP/IP, Embedded Linux, Yocto, Zynq, ARM
  Cortex, MISRA C, BSP, HAL, board bring-up, real-time systems,
  hardware debugging, etc.)
- ✅ **4-tier location extractor** + **skill variant generator**
  (slash splits, paren strip, lead-phrase strip)

### Layer 2 (Taxonomy + Matching Engine): ~100%
- ✅ **Phase A FINISHED.** All 8 dimensions deterministic per
  `modes/_matching-engine.md` spec. Engine code in
  `api/_matching_engine.py` (1012 lines).
- ✅ Composite cap at 3.0 on blockers
- ✅ Engine ran end-to-end on Steve Benz vs Nuve Controls: correctly
  flagged as Hard Pass (composite 2.92) due to 2 blockers (Qt + QML)
  despite AI eval scoring him INTERVIEW (72/100). AI-vs-engine
  disagreement working as designed: AI is generous, engine is honest.

### Layer 3 (Calibration Loop): ~40%
- ✅ Math live in `api/_calibration.py`
- ✅ Phase B2 endpoints: `/api/taxonomy/unresolved` etc.
- ✅ Phase B3 archetype discovery: Jaccard clustering live at `/api/trends/role-archetypes`
- ⚠️ **Still need 1 more placeable outcome** to unlock Fill Probability
  calibration. Steve Benz Nuve Controls submission is a natural
  candidate (currently evaluated stage).
- ❌ Phase B4 skill mesh — not built (premature, corpus too small)

### Layer 4 (Lifecycle Modes): ~35%
- ✅ **Source mode** — full 8-dim engine on every eval
- ✅ **Match mode (Phase E.1)** — `/api/match/batch`, ranks all user
  candidates against one req using engine, no extra AI calls.
  UI at `/app/match.html` with Engine Scorecard per candidate.
- ✅ **Match → Source connector (Phase E.2)** — `/api/source/reevaluate`
  pulls cached resume_text and runs the full Source pipeline against
  any candidate-req combo. One-click from Match results. Idempotent
  within 90s window (prevents duplicate submissions from retry clicks).
- ✅ **Outcomes dashboard** — `/app/outcomes.html` with locked/unlocked
  calibration status, awaiting outcomes queue, history timeline.
- ⏳ **Schedule mode (Phase E.3)** — IN PROGRESS this session. Path A
  (in-app only). Path B (Google Calendar via MCP) planned for later.
- ❌ Interview, Offer, Onboard, Perform, Develop, Retain, Depart — not built

### Layer 5 (Agency Surface): ~50%
- ✅ Pipeline, settings, taxonomy, trends, match, outcomes pages
- ✅ Engine Scorecard panel surfaces 8-dim math
- ✅ Print view for client submission packets
- ⚠️ Free vs Pro tier gating exists but billing not wired
- ❌ Outreach Generator (Phase C #6) — designed, not built

### Layer 6 (Company Surface): 0%
Unchanged.

### Layer 7 (Market Intelligence): ~5%
Unchanged.

### Architecture hygiene (cross-cutting, shipped 2026-05-19)
- ✅ Prompt module + versioning (`api/_prompts.py`)
- ✅ Toolkit registry with Pydantic schemas (`api/_toolkit.py`)
- ✅ `call_ai_json` JSON-repair wrapper with retry strategy
- ✅ Reevaluate idempotency (90s window) prevents duplicate submissions
- ✅ Vercel function timeout extended to 300s
- ✅ Frontend timeout-aware error handling (distinguishes "Failed to
  fetch" from real failure vs successful-but-cut-off)

## What's left in each phase (rewritten 2026-05-11)

### Phase A: Foundation - Port the brain
**Status:** ✅ COMPLETE (2026-05-11).

The taxonomy + matching engine are first-class database tables. Every
operation writes structured data to them. Intake parser emits canonical
skills. Candidate eval extracts structured skills with recency/depth.
All compliance/audit infrastructure is live. **All 8 dimensions of the
rubric are now scored by deterministic code, not prompts.**

The matching engine lives in `api/_matching_engine.py` as a pure
Python module with no FastAPI or DB dependencies. It accepts structured
inputs and returns structured scores. The module is callable from any
lifecycle stage (Match, Interview, Offer, Retain, Develop, Depart).

**Acceptance test (verified):** Same candidate + same JD evaluated
twice produces identical composite score within rounding. Compositions
reproducible across runs.

**What's still loose around Phase A but doesn't block:**
- AI prompt could emit seniority vector + culture + presentation
  scores directly instead of being derived. Would improve accuracy of
  Dims 2/5/7 but not change determinism. Lower priority.
- Backfill script not run on early reqs (pre-req_skills writes).
- Medical device / automotive / aerospace taxonomy domains can grow.

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

The next concrete task right now is **start Phase E** (lifecycle modes
beyond Source). Phase A is complete and the matching engine is ready
to be called from any lifecycle stage. The strongest candidates for
the first lifecycle mode build are:

1. **Match mode** (`/api/match/batch`): given a req and N candidates,
   rank them using the full 8-dim engine. Direct Phase C #4 (Batch)
   build with deterministic scoring underneath. Highest immediate
   value to the agency Pro tier.
2. **Interview mode**: structured rubric capture for interview feedback
   that feeds Phase B1 calibration with richer outcome signals than
   just placement/lost.
3. **Retain mode**: post-placement health checks scored by the same
   engine running quarterly against the placed candidate's role.
   Defensible only after first placements are logged with skills.

Match mode is the highest-leverage starter because it consumes existing
agency data (candidates already evaluated) and creates a Pro-tier
feature immediately.

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

*Last meaningful update: 2026-05-19. Reflects Phase A finish, Match mode (E.1),
Match → Source connector (E.2), Outcomes dashboard, prompt module + toolkit + JSON
repair architecture hygiene. Phase E.3 (Schedule mode) in progress.*

*Earlier history: 2026-05-11 — Live Intelligence Stream
shipped, Pro CI shipped, Trends/clustering shipped, Close Out Req
shipped, duplicate cleanup applied (47 → 23 open reqs), Phase B1
calibration scaffolded, Phase B2 endpoints live, Phase A matching
engine finishing identified as the next priority.*
