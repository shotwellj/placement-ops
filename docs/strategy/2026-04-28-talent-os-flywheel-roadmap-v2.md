# SourcingNav: The Talent OS Flywheel — v2

**Author:** Jason Shotwell
**Date:** 2026-04-28 (v2 revision later same day)
**Status:** Strategic roadmap, internal
**Supersedes:** `2026-04-28-talent-os-flywheel-roadmap.md` (v1 — kept for version history)
**Companion docs:** `2026-04-28-shadow-ai-detection-prd.md` (parked HireGuard scope, explicitly NOT part of SourcingNav)

---

## Why v2 exists

V1 of this roadmap captured the 10-stage employee lifecycle flywheel with a 6-question framework per stage. Solid foundation. But it was written without integrating context from two strategic-analysis sessions earlier in April: a Noon AI / Gem reverse-engineering deep-dive and a comprehensive testing pass on 7 elite-quality recruiting AI prompts that already exist on disk.

Those sessions produced sharper thinking on three fronts that v1 underweighted:

1. **The Layer 1 / Layer 2 / Layer 3 framing.** What v1 called "later stages" is actually a separate product layer with a different buyer (HR, not recruiting). V2 makes this explicit and treats it as a strategic axis, not just a sequence.

2. **The preference graph as moat.** Noon's actual moat isn't their custom-trained model. It's the labeled accept/reject pairs they capture. SourcingNav can replicate this without RLHF or fine-tuning by using prompt-time few-shot injection. This was missing from v1.

3. **Five elite prompts already on disk, not yet integrated.** Competitive Intelligence (9/10), Candidate Fit Analyzer (9.5/10), Sourcing Jamboard (9.5/10), Outreach Generator v2 (7/10 with known fixes), DEI Strategy (9/10). All scored against real JDs. All sitting at `~/Desktop/CandidatIQ-V3/backend/prompts/` and `~/Desktop/untitled folder/candidatIQ/`. V1 didn't surface this asset inventory at all.

V2 keeps v1's structural bones and adds these dimensions explicitly so the next session of work has the full picture.

---

## The thesis, sharpened

Every recruiting tool today owns one stage of the employee lifecycle. ATS owns intake. Sourcing tools own discovery. Scheduling tools own coordination. Performance tools own evaluation. Each one produces data the others never see.

**SourcingNav's bet is that the moat isn't any single stage — it's the cross-stage calibration data that no point solution can replicate, anchored by a unified skill taxonomy that connects every layer.**

Three things compound across that taxonomy:

- **The recruiter's preference graph** (every accept/reject they make becomes structured labeled data)
- **Cross-company outcome signal** (which sourcing patterns at Company A predicted retention success at Company B, etc.)
- **Layer-spanning fact propagation** (a rejection reason from Stage 4 updates Stage 1's signature graph for the next search)

When the system can see that a placement made via "Senior ML Engineer with PyTorch" boolean strings led to a promotion within 18 months at three different companies, the next time someone sources for that role the system knows which boolean tier actually predicts success — not just which one returns more profiles. **Each stage produces signal that makes every other stage smarter.** That's the flywheel. And every layer of the product reinforces it.

---

## What this doc is and isn't

**Is:** Sequenced roadmap for the next 6-18 months of SourcingNav, with explicit awareness of: (a) the 3-layer product structure, (b) the elite assets already on disk, (c) the preference-graph moat insight, (d) the trust/fraud surface, (e) ATS integration strategy.

**Isn't:** A pitch deck. A feature list. A complete spec. A commitment to ship in any specific order — sequencing depends on data, customer signal, and what existing assets reveal as they're integrated.

**Explicitly out of scope:** HireGuard / shadow AI detection (separate product, separate buyer, parked in companion doc). Compliance/governance tooling for AI deployments (AIR Blackbox scope). Anything that isn't a recruiter, hiring manager, HR ops, or recruiting-org-leadership workflow.


---

## The three-layer product structure

V1 implied this. V2 makes it explicit because it changes how features get sequenced.

### Layer 1: Talent Acquisition (recruiter-facing — built today as SourcingNav)

The user is a recruiter, sourcer, or agency owner. Their job is to fill open roles. The product helps them find, evaluate, engage, and place candidates. This is what we've been building. Stages 1-5 of the flywheel sit here primarily.

**Buyer profile:** Solo recruiter ($30-49/mo), agency owner ($300-500/mo for 3-20 seats), eventually in-house TA team ($49/seat for 3-50 seats).

**What's live today:** Free-tier intake (JD parser + boolean builder), Pro intake (5-tier booleans + skill triage), public market intel page, internal trends page (Pro), pipeline view (basic), retention email, signature graph + clustering (Phase B3 complete).

### Layer 2: Talent Management (HR-facing — vision, not built)

The user is a People Ops leader, HR business partner, or talent management head. Their job is to develop, retain, and re-deploy existing employees. The product helps them map skills inside the company, identify growth paths, predict flight risk, plan succession, and run internal mobility before posting external reqs. Stages 6-10 of the flywheel sit here.

**Buyer profile:** Mid-market HR leader ($499/mo for company under 200 employees), enterprise HR ($2k-10k/mo). Not the same buyer as Layer 1.

**Product surface:** Eventually `/people-ops` or similar, with the same database underneath but a different UI optimized for org-chart navigation, skills heatmaps, and employee-record management.

**What's live today:** Nothing. The taxonomy infrastructure that powers Layer 1 also powers Layer 2 — but no Layer 2 features are built.

### Layer 3: Talent Intelligence (cross-cutting — fragments live)

The user is an executive, founder, or analyst. Their job is to understand the talent market for strategic decisions: what to pay, where to hire, which skills are emerging, when to train vs hire. The product produces aggregated, cross-customer insight derived from Layer 1 and Layer 2 data.

**Buyer profile:** Either packaged as a tier on Layer 1/2, or sold separately as a market intelligence subscription ($50k-500k/yr enterprise).

**What's live today:** The public market intel page is a tiny fragment of this — corpus-wide blocker skills, top poaching companies, difficulty distribution, emergent role clusters. The internal trends page is the same data with member-level resolution. Both are early-signal at N=32; the Layer 3 product proper requires N>500 and probably cross-customer aggregation to be commercially defensible.

### Why this framing matters for sequencing

If we treat Layer 2 as "later stages of the same product," we'll build it on top of the recruiter UI and confuse the buyer. If we treat it as a separate product surface that shares a database, we keep the customer story clean and can sell each independently.

**Decision rule: shared database, separate UI surfaces, separate SKUs.** The taxonomy is the connective tissue. The product surfaces are sold to different buyers.


---

## Asset inventory: what exists, where, and what's not yet integrated

This section didn't exist in v1 and it should have. Future sessions need this to avoid losing context.

### Already integrated into SourcingNav (live at sourcingnav.com)

| Asset | Source | Where it lives now | Quality |
|---|---|---|---|
| JD Parser | CandidatIQ V3 prompts | `/api/intake` endpoint, free tier | 8.5/10 (validated) |
| Boolean Builder | CandidatIQ V3 prompts | `/api/intake` endpoint, Pro tier (5-tier expansion) | 9/10 (validated) |
| Skill triage briefing | Built fresh today | `/api/intake` endpoint, Pro tier | New, untested at scale |
| Per-search universe approach | "Reverse Gem" architecture | Boolean Builder generates queries instead of indexing | Aligned but not enforced |
| Truthfulness pass (no fabricated quotes) | Late-April fix | Sequenced play + skill alternatives output | Live |
| Em-dash ban + banned phrases | Outreach v2 fix | Not yet integrated (no outreach feature live) | Spec captured |
| Signature graph + clustering | Phase B3 (today) | jd_signatures table, cluster_runs table | Live, N=32 |
| Public market intel page | Today | `/market-intel` | Live |
| Pro tier locked-view + cap freemium | Today | Server-gated Pro features | Live |

### Sitting on disk, not yet integrated (the elite-prompt backlog)

This is the biggest asset class and the one v1 missed. All five were tested against real JDs in late April and scored 7-9.5/10.

| Tool | Disk location | Quality | Where it goes in the flywheel |
|---|---|---|---|
| Competitive Intelligence | `~/Desktop/untitled folder/candidatIQ/` (CompetitiveIntelligence.tsx + intelligence_engine.py) | 9/10 | Stage 1 (Source) — auto-chains from Boolean Builder's company clusters |
| Candidate Fit Analyzer | Inferred from project history (no single .txt prompt; logic was in CandidatIQ frontend + taxonomy) | 9.5/10 | Stage 3 (Match) — taxonomy does deterministic, AI does judgment |
| Sourcing Jamboard | `~/Desktop/untitled folder/candidatIQ/SourcingJamboard.tsx` (511 lines) | 9.5/10 | Stage 1 (Source) — hidden talent pools, platform strategies, watering holes |
| Outreach Generator v2 | Spec captured today, prompt was in outreach v2 test artifact | 7/10 (needs em-dash ban + deep personalization fix) | Stage 2 (Engage / outreach) |
| DEI Strategy | Spec captured in v2 test artifact | 9/10 | Stage 1 (Source) — inclusive sourcing channels, bias mitigation by stage |

**Plus the broader CandidatIQ codebase asset class:**

- 11 frontend components, 6,387 lines of React/TypeScript (JDParser360, AutonomousAgent, BooleanVariantsAdvanced, AIMentorPanel, KanbanBoard, MarketMapping, AlternativeTitles, etc.)
- 141 Python backend files (intelligence_engine, outreach_agent, market_mapping_enhanced, prompt_templates, enrichment_routes, candidate_graph_api, datagraph_routes)
- Data collectors for Apollo, GitHub, Hunter, Kaggle, PDL
- CandidatIQ V3: Stripe payments, magic-link auth, Chrome extension sidepanel, multi-provider AI service (Claude + OpenAI + Ollama)

### Specifications captured but never implemented

| Spec | Captured in | Status |
|---|---|---|
| Outreach engine (A/B testing, edit learning, decision points after no-response) | Late-April session | Doc exists, no code |
| Smart response system (intent classification, prep brief on calendar invite) | Late-April session | Doc exists, no code |
| 3-engine consolidation (Intake → Source → Engage) | Late-April session | This roadmap |
| Freemium tier structure with caps | Late-April session | Partially live (Pro gates exist, cap tracking exists, but not all five Pro features are wired) |

### Built but never deployed (broken / lost)

| Asset | Where | Why it's broken |
|---|---|---|
| Knowledge Hub "Protect Yourself" section | Committed to placement-ops repo | Vercel deployment confusion in late April. Status of deployment unclear as of today. |

**Action item from this inventory:** Audit `sourcingnav.com/ui/knowledge-hub.html` to verify whether the Protect Yourself section is live. If not, redeploy.


---

## The preference graph as moat (genuinely new in v2)

This insight came from the late-April Noon AI reverse-engineering session and was missing from v1's framing of calibration.

**Noon's actual moat isn't the model.** They market themselves as "the first AI model custom trained for recruiters" but every public source describes the same mechanism: RLHF on top of a base LLM, with per-firm reward models. The model is interchangeable. The moat is the labeled preference dataset they accumulate per firm.

**SourcingNav can replicate the same moat without RLHF.** Every time a recruiter marks a candidate as accept/reject with a reason, store it as a structured pair: `(role_id, candidate_a_features, candidate_b_features, winner, reason_text)`. After 20-30 decisions, inject the most relevant past decisions as few-shot examples in the rubric-grading prompt:

```
Past decisions by this recruiter for similar roles:
- HIRED: 8 yrs Python, no ML degree, indie projects → "values builders over credentials"
- REJECTED: 12 yrs at FAANG, PhD, no shipped products → "too academic"
- HIRED: 5 yrs, bootcamp grad, 3 production systems → "results > pedigree"

Now grade this candidate: [...]
```

This is **prompt-time personalization instead of training-time.** Works on day one (Gem's win), captures recruiter taste over time (Noon's win), costs zero extra infrastructure.

**Why this matters strategically:**

1. The recruiter's preference graph is the part of SourcingNav that competitors literally cannot copy. Gem can build per-firm tuning on top of their data layer, but they can't extract Jason Shotwell's individual taste from a competitor product.

2. It compounds with use. Other features get more useful as more people use them (network effects). The preference graph gets more useful as a single recruiter uses it (depth effects). Both compounding modes matter.

3. It naturally extends to Layer 2. The same "what does this hiring manager actually value" learning that improves Match for Stage 3 also improves growth-path recommendations for Layer 2 employees.

**Implementation at minimum-viable level:** A `preference_pairs` table with the schema above. Capture decisions in the pipeline view. Inject into the next AI call's prompt as few-shot examples. Total work: ~3-4 days. Massively underweighted in v1.

---

## The "Reverse Gem" architectural commitment (genuinely new in v2)

V1 implied per-search universe construction as MVP shape for Stage 1 but didn't commit to it as architectural principle. V2 commits.

**We do not build a global profile index. We build per-search candidate universes.**

For any given search, the universe of qualified humans is discoverable through public, structured sources that don't require crawling LinkedIn:

- **GitHub API** (free, 5K req/hr authenticated) — every engineer with a real career has commits
- **Google Scholar / Semantic Scholar API** (free) — every ML engineer worth hiring has co-authored or cited
- **Crunchbase free tier + LinkedIn company pages** — for "people who worked at X" via public team pages
- **Conference speaker lists** (PyData, KubeCon, DEF CON, RSA, RecSys) — public, structured, indicates expertise level
- **Patents (USPTO API, free)** — for senior IC and research roles
- **Substack / personal blogs / X bios** — for thought leaders
- **Public Slack / Discord member lists** in technical communities

**For any typical search: 500-5,000 candidates in 2-10 minutes. Not 800M. But 100% relevant.**

This is the reframe: **Gem indexes humanity. SourcingNav indexes the role.**

**Why this is architectural and not just tactical:**

1. **Legal:** Per the late-April analysis of hiQ Labs v. LinkedIn — public scraping isn't a CFAA violation but ToS violations are enforceable as breach of contract. Per-search universe construction via published APIs (GitHub, Scholar, USPTO, etc.) is in clean legal territory. Plus optional Google Programmable Search Engine (PSE) as the trust buffer for LinkedIn x-ray queries.

2. **Cost:** A global crawl is $50M+ infrastructure. Per-search universe is ~$70/mo per recruiter at moderate use, mostly Apollo/Hunter for emails.

3. **Quality:** Curated by domain logic beats generic keyword match. The Boolean Builder + Sourcing Jamboard already do the curation logic. We just need to wire the API adapters.

4. **Compliance:** Every score has explainable evidence per criterion. Aligns with EU AI Act Article 14 (human oversight), NYC Local Law 144 (bias audit), Colorado SB 24-205. AIR Blackbox plays here as the trust layer.

**Implementation status:** The query generation logic exists (Boolean Builder + Sourcing Jamboard). The actual API adapters (GitHub, Scholar, etc.) and the candidate-fetching pipeline don't exist yet. This is roughly a 3-4 week build for the first 3 adapters (GitHub, Scholar, public-search), and is what would unlock real per-search candidate generation as a feature instead of just query string output.


---

## The 10 stages, revisited with v2 context

V1's six-question framework holds. V2 adds: which existing assets plug in, how Layer-2/3 surfaces consume each stage's data, and the preference-graph hooks.

### Stage 1: Source — DONE (Phase B3 complete) + 3 elite prompts ready to integrate

**Status:** Live for the deterministic + JD-parsing parts. Three elite prompts (Competitive Intelligence 9/10, Sourcing Jamboard 9.5/10, DEI Strategy 9/10) are tested and sitting on disk, ready to integrate.

**What the integration looks like:**

- **Competitive Intelligence** auto-chains from the Boolean Builder's company clusters. The recruiter pastes a JD once. Boolean Builder identifies Tier 1 + Tier 2 companies. Competitive Intelligence runs against those companies automatically and surfaces hiring velocity, poaching difficulty, salary intel, remote policy, talent flow map. **No additional UI needed beyond an expanded results card.**
- **Sourcing Jamboard** runs as a tab on the requisition detail view: hidden talent pools, platform strategies, career switchers, watering holes, passive candidate triggers, geographic opportunities, timing strategies. **Pro tier feature.**
- **DEI Strategy** runs as a tab on the requisition detail view: communities/conferences/sourcing strings per group, bias mitigation by stage, inclusive JD suggestions, metrics to track. **Pro tier feature, optional toggle.**

**Effort honestly:** ~2 weeks for all three. The prompts exist, the JSON shapes exist. Just wiring + UI.

**Cross-stage flywheel:** Stage 1's signature graph already feeds clustering. Once Stages 2-4 generate outcome data (placed/not, response rate, retention), the signature graph gets calibrated against actual outcomes and Stage 1's predictions get sharper.

**What's still missing in Stage 1:**
- JD parser prompt tightening (the verbose-skill issue from today)
- Phase B3 Part E: cluster promotion to taxonomy (Phase B-finishing item)
- The actual GitHub/Scholar/PSE API adapters (Reverse Gem implementation)
- Mobile responsive polish

### Stage 2: Schedule + Outreach — NEAR-TERM (consolidated from v1's Stage 2)

**V2 reframe:** V1 treated Schedule and Outreach as separate concerns. They're operationally separate but the data flywheel treats them together — they're both "the engagement layer."

**Components:**

1. **Outreach Generator (v2 version with em-dash ban + deep personalization).** The 7/10 quality is a known-fix issue: enforce specific personalization detail (not "caught my attention"), ban em-dashes and AI-fingerprint phrases, enforce word counts per channel. The fixes are spec'd, not coded.

2. **Outreach engine** with A/B testing on style, edit-learning (recruiter's edits become few-shot examples for next message), decision points after no-response (HM intro / breakup / new channel / pause 30 days), per-candidate cooldown, suppression list checks. **This is the spec'd-but-not-built feature from late April.**

3. **Smart response system.** Detect intent from candidate replies (8 types: Interested-scheduling, Warm-needs-nurture, Soft-decline, Hard-decline, Deferred, Comp-question, Ghosted, etc.). Auto-action mapped per type. Calendar integration with prep-brief-attached.

4. **Calendar (built incrementally).** Phase 1: manual paste of Calendly link when system detects positive response. Phase 2: native booking page reading availability from Google Calendar. Phase 3: full Gem-style automation.

**Cross-stage flywheel:** Outreach edits become preference-graph data. Response rates by message style + role type + company become A/B test data. Reschedule frequency feeds Stage 1's difficulty score. Time-to-first-interview by signature feature becomes a market-intel data point for Layer 3.

**Effort honestly:** Outreach v2 prompt fix = 1 day. Outreach engine MVP (single recipient, A/B test, edit log, decision points) = 2-3 weeks. Smart response system + calendar Phase 1 = 1-2 weeks. Total: 4-6 weeks for a complete Stage 2 MVP.

**Strategic question:** Build native or partner? Cal.com is open-source and free. Calendly has API. Native booking is sticky (switching cost goes up once meetings live in SourcingNav) but takes longer. Recommend Phase 1 manual + Phase 2 native sequencing.

### Stage 3: Match — NEAR-TERM (with elite prompt ready to integrate)

**Status:** The Candidate Fit Analyzer prompt scored 9.5/10 against three test candidates (strong/decent/weak). It scored honestly: 88, 62, 41. It's the strongest tested asset and it's not yet integrated.

**What the integration looks like:**

- Recruiter adds a candidate to a requisition (paste profile, link to LinkedIn/GitHub, etc.)
- Taxonomy runs deterministic skill match (exact / adjacent / parent / blocker). Cheap, no tokens.
- AI Fit Analyzer receives the taxonomy's pre-computed scores PLUS the candidate profile + parsed JD
- AI adds: seniority fit, domain relevance, career trajectory, flight risk, outreach strategy, comp estimate, interview prep
- Output: 0-100 fit score with skill match breakdown, recommendation (SUBMIT/PASS), interview questions, outreach angle

**Why this is the most flywheel-rich stage (per v1):**
- Match outcomes (placed vs not-placed) calibrate Stage 1's signature graph
- Match data over many companies tells us which skill substitutions are real (Vue→React works 83% at startups, 41% at FAANG)
- Match scores tagged with which skills to probe deepest feed Stage 4 (Interview)
- Match data is the entry point for the preference graph — every accept/reject becomes a preference pair

**Effort honestly:**
- Schema additions (candidates table, candidate_evaluations table): 3-4 days (overlaps with Stage 2 candidate entity)
- Match scoring API endpoint with taxonomy + AI integration: ~1 week
- UI for paste-candidate-get-match flow: 4-5 days
- Bulk scoring (Pro tier): +1 week
- Preference graph wiring (capture accept/reject as structured pairs): 3-4 days

Total MVP: ~3-4 weeks. Calibration loop adds 4-6 weeks but doesn't block launch.

**Risk repeat from v1:** Outcome logging UX. If logging "placed/not-placed" takes more than 1 click, recruiters won't do it, calibration moat never builds.

### Stage 4: Interview — MID-TERM (with closed-loop learning)

**V2 addition:** The smart response system attaches a prep brief to the calendar invite automatically. The brief contains: candidate summary, fit score, strengths, risks to probe, comp estimate, interview questions, outreach history. **This is the genuinely-smarter-than-Gem feature.** Gem schedules. SourcingNav schedules and prepares.

**Per-question signal-to-decision correlation** is the long-game intelligence layer. Which interview questions actually predict offer / placement / 12-month retention vs which are noise. After enough data, the system can recommend: "Skip this question — its signal-to-decision correlation is 0.04. Replace with this one — correlation is 0.61."

**Effort:** ~6-8 weeks for the full scorecard tool with team-collaboration UX. Strategic question still open: replace Greenhouse scorecards or integrate behind them?

### Stage 5: Offer — MID-TERM

(Mostly unchanged from v1. Remaining open question: build vs partner with Pave / Levels.fyi.)

### Stage 6: Onboard — LATER (Layer 2 entry point)

**V2 reframe:** This is where Layer 1 hands off to Layer 2. Onboard is the bridge. Recruiting cared about getting the hire in the door. Layer 2 cares about whether they're succeeding. The same candidate signature that drove sourcing now becomes an employee skills record.

**Bridge feature: Silver Medalist Rediscovery (high-leverage, 2-week build).**

Per the Gem teardown, this is the highest-ROI feature any recruiter ever uses. Scale AI fills 70% of roles from rediscovery.

**MVP:**
1. Recruiter exports their ATS rejected-candidates list as CSV
2. Upload to SourcingNav
3. New JD comes in
4. System runs the JD against the CSV using the existing intake + match pipeline
5. Surfaces past rejections that match the new role with reasoning ("Sarah was rejected for the Backend Sr role because of K8s gap. New role doesn't require K8s. Re-engage with this angle.")
6. Output: ranked list with re-engagement script

**Why this is the right bridge:**
- 2-week build, zero new infrastructure
- Layer 1 customers immediately want it
- Output uses Layer 2's "we already have data on this person" thinking
- Demonstrates the taxonomy's value — same person, different role, different fit
- Doesn't require ATS API integration — CSV upload is enough for v1

### Stages 7-10: Perform / Develop / Retain / Depart — VISION (Layer 2 + Layer 3)

**V2 reframe (less hand-wavy than v1):**

Each stage produces specific data with specific Layer 2 / Layer 3 use cases:

- **Perform (Stage 7):** 12-month performance review scores per employee → calibrates Stage 1's signature features against actual production performance, not interview signal. Layer 2 use: "Which employees are top-performers in roles that match this open req?"
- **Develop (Stage 8):** Promotion rate by hire signature + skill development logs. Layer 2 use: growth-path recommendations. "James knows TensorFlow. PyTorch is adjacent (0.6). He could cross-train in 4 weeks." Layer 3 use: "Hire vs train" math at company level.
- **Retain (Stage 9):** Flight risk model in reverse (the same model from Stage 3's Candidate Fit Analyzer but applied to current employees). Equity cliff timing + skill demand + tenure flags. Layer 2 use: "Sarah is a flight risk in 60 days. Here's the retention plan."
- **Depart (Stage 10):** Regretted vs unregretted attrition by hire signature. Exit interview signal feeds back to Stage 1: "Don't hire engineers from Company X for senior IC roles — 70% of them leave within 18 months." Layer 3 use: cross-customer benchmarks on retention by source.

**Honest assessment unchanged from v1:** these stages are 18+ months out at minimum. Probably 3+ years for full coverage. Real, defensible long-term moat. But scoping in detail today is fortune-telling.


---

## ATS integration strategy (genuinely new in v2)

V1 punted on this entirely. V2 makes a sequenced commitment.

**Strategic principle: ATS-optional, not ATS-dependent.** Solo recruiters use SourcingNav as their primary system. In-house teams use SourcingNav alongside their ATS. Both should work day one.

**Sequence:**

**Phase 1 (launch): CSV export + paste flows.** Recruiter downloads candidate package from SourcingNav as CSV, uploads it to their ATS manually. Recruiter exports past-rejection list from ATS as CSV, uploads to SourcingNav for silver medalist rediscovery. Zero integration work, full functionality.

**Phase 2 (month 2-3 after Stage 3 Match MVP): Greenhouse + Lever API integration.** These two cover the majority of in-house TA teams. Both have well-documented APIs. Push: candidate creation, fit scorecard, submission memo, prep brief, activity log. Pull: stage changes, rejection reasons.

**Phase 3 (month 4-6): Bullhorn integration.** Bullhorn's API is older and complex but it's what 80%+ of staffing agencies use. Unlocks the agency market. Reach: 120K independent recruiters in US, 400K globally.

**Phase 4 (month 6+): Webhook listeners for bidirectional sync.** ATS pushes stage changes + rejection reasons back to SourcingNav. Closes the calibration loop automatically. Stage 4 (Interview) outcomes feed back to Stage 3 (Match) prediction quality.

**What ATS sync DOES for the calibration moat:**

When the ATS tells SourcingNav "client rejected Sarah because of Kubernetes gaps," the calibration engine updates the taxonomy: Kubernetes moves from "preferred" to "required" for this client. Next search is smarter. The taxonomy compounds across customers anonymously — "for clients in this industry, K8s rejections happen 40% more often than the JD claims" becomes a Layer 3 insight.

**For solo recruiters with no ATS:** SourcingNav becomes their system. Pipeline dashboard, candidate management, outreach tracking, scheduling all live in SourcingNav. Highest free-to-paid conversion path because once their pipeline lives in SourcingNav, switching costs are high.

---

## Trust + fraud surface (genuinely new in v2)

V1 didn't address this. The transcripts surfaced three real attack vectors that recruiters and candidates are facing right now:

1. **Fake companies posting fake jobs** to collect personal data, charge "training fees," or run advance-fee schemes
2. **AI agents charging candidates monthly fees** to find jobs (often pure scams targeting unemployed people)
3. **Deepfake candidates using AI overlays in interviews** — the person who interviews isn't the person who gets hired

**SourcingNav's role:** Trust and verification platform for both sides of the recruiting interaction.

**Layer 1 (recruiter-facing) features:**

- **Candidate verification layer in Engage engine.** When a candidate responds and schedules, surface verification checkpoints: confirm LinkedIn matches resume, confirm GitHub activity matches claimed experience, cross-reference stated employer against public records. The Candidate Fit Analyzer already pulls specific project details — if a candidate claims to have built search ranking at Airbnb but has zero public signal (no GitHub, no papers, no conference talks), flag it.
- **Interview integrity scoring.** Recruiter logs notes after a call. System compares the depth of technical answers against the candidate's profile. If someone scored 88 on Fit Analyzer but couldn't answer basic questions about their resume work, surface a red flag.

**Public-facing content (already partially built):**

- **Knowledge Hub "Protect Yourself" section.** Free resource teaching candidates how to spot fake job postings, verify recruiters, recognize AI agent scams, report fraud. Plus deepfake detection guidance for hiring teams.

**Status:** Section was committed to placement-ops repo in late April but Vercel deployment confusion means it may or may not be live. **Action: audit and redeploy if needed.**

**Strategic positioning:** If SourcingNav verifies recruiters with "verified" badges, candidates would prefer to engage with SourcingNav-sourced outreach over random LinkedIn InMails. That's a trust moat that compounds with usage.

**Effort:** Verification features are Phase 5+ (after the 90-day plan below). The Knowledge Hub piece is already done (modulo deployment audit).

---

## Sequencing recommendation v2 (revised in light of asset inventory)

V1 proposed: Days 1-30 polish, Days 31-60 Stage 2 (Schedule) MVP, Days 61-90 Stage 3 (Match) MVP, Day 91+ either Stage 4 or market-led pivot.

**V2 revises this because the asset inventory changes the math.** Five elite prompts (Competitive Intelligence, Sourcing Jamboard, DEI Strategy, Candidate Fit Analyzer, Outreach v2) are sitting on disk with quality scores ≥7/10. Integrating them is faster than building Stage 2 from scratch and produces immediately-shippable Pro features.

### Days 1-30: Integrate the elite prompts + Phase B3 finishing + Silver Medalist Rediscovery

**Goal:** Multiply the Pro tier's value-per-seat by 3-5x by wiring in 4 already-built prompts + ship the Silver Medalist Rediscovery as a "wow" feature.

- **Week 1:** Integrate Competitive Intelligence into intake (auto-chains from Boolean Builder's company clusters). 9/10 prompt, ~2-3 days work.
- **Week 1-2:** Integrate Sourcing Jamboard as a tab on requisition detail. 9.5/10 prompt, ~3-4 days work.
- **Week 2:** Integrate DEI Strategy as optional tab on requisition detail. 9/10 prompt, ~2-3 days work.
- **Week 2-3:** Build Silver Medalist Rediscovery MVP (CSV upload + JD-vs-candidates scoring with re-engagement scripts). ~2 weeks.
- **Week 3-4:** Phase B3 finishing — JD parser prompt tightening, Phase B3 Part E cluster promotion, mobile responsive on app pages. Drive N from 32 to 100.

**Shippable outcome:** Pro tier looks dramatically more valuable. A new visitor to sourcingnav.com sees 5 Pro features (intake-with-CI, full Skill Briefing, Pro Booleans, Sourcing Jamboard, DEI Strategy, Silver Medalist Rediscovery) instead of 2.

### Days 31-60: Stage 3 (Match) MVP with preference graph + Outreach v2 prompt fix

**Goal:** Wire in Candidate Fit Analyzer (the 9.5/10 prompt) and lay the preference graph foundation.

- **Week 5-6:** Schema additions (candidates, candidate_evaluations, preference_pairs). Match scoring API integrating taxonomy + AI Fit Analyzer. UI for paste-candidate-get-match flow.
- **Week 7:** Preference graph wiring — every accept/reject in the pipeline view becomes a structured pair. Few-shot injection into next AI call.
- **Week 7-8:** Outreach v2 prompt fix (ban em-dashes, deep personalization enforcement). Single-message generation MVP, no automation yet.

**Shippable outcome:** Match is live. Pro recruiters can score candidates against requisitions with honest scoring + interview prep. The preference graph captures their judgment passively.

### Days 61-90: Stage 2 (Engage) — Outreach engine + smart response + calendar Phase 1

**Goal:** The full A/B-testing outreach engine + smart response system + native scheduling.

- **Week 9-11:** Outreach engine MVP (sequences, A/B testing, edit-learning, decision points after no-response).
- **Week 11-12:** Smart response system (intent classification of replies, auto-action mapping, prep-brief-on-calendar-invite).
- **Week 12-13:** Calendar Phase 1 (manual paste + native pipeline tracking).

**Shippable outcome:** End-to-end recruiting workflow lives in SourcingNav. Source → Match → Engage → Schedule → Track. The flywheel turns.

### Days 91+: ATS integration + Stage 4 (Interview) OR market-led pivot

By day 90 the elite-prompt backlog is fully integrated, the preference graph is collecting data, and there's signal from Pro customers about what to build next. Default path: Phase 2 ATS integration (Greenhouse + Lever) opens the in-house TA market. Alternative: customer-led prioritization of Stage 4 features.

### What's deliberately NOT in this 90-day plan

- Layer 2 (Talent Management) features beyond Silver Medalist Rediscovery
- Layer 3 (Talent Intelligence) beyond what's already live
- HireGuard / shadow AI detection (parked separately)
- ATS API integration beyond CSV (Phase 2+ post day-90)
- Trust verification features (Phase 5+)
- The actual "Reverse Gem" GitHub/Scholar/PSE adapters (Phase 5+, optional based on customer demand)
- Stage 5-10 builds


---

## The flywheel diagram, updated

```
                                    ┌─────────────────────────────────────────────┐
                                    │     Cross-Stage Calibration Layer            │
                                    │     (the moat — N years to replicate)        │
                                    │                                              │
                                    │   • Recruiter preference graphs (per-user)   │
                                    │   • Cross-company outcome signal (anonymous) │
                                    │   • Taxonomy adjacency learning (compound)   │
                                    └─────────────────────────────────────────────┘
                                              ▲                       ▲
                                              │ feedback              │ feedback
                                              │                       │

LAYER 1 (RECRUITER-FACING) — SourcingNav
   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
   │  SOURCE  │───▶│ ENGAGE   │───▶│  MATCH   │───▶│INTERVIEW │───▶│  OFFER   │
   │ (Stage 1)│    │ (Stage 2)│    │ (Stage 3)│    │ (Stage 4)│    │ (Stage 5)│
   │ DONE +   │    │ Days     │    │ Days     │    │  3-6 mo  │    │  3-6 mo  │
   │ 3 elite  │    │ 61-90    │    │ 31-60    │    │          │    │          │
   │ prompts  │    │          │    │ + pref   │    │          │    │          │
   │ Days 1-30│    │          │    │ graph    │    │          │    │          │
   └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                          │
                                                                          ▼
LAYER 2 (HR-FACING) — eventually /people-ops
   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
   │  DEPART  │◀───│  RETAIN  │◀───│ DEVELOP  │◀───│ PERFORM  │◀───│ ONBOARD  │
   │(Stage 10)│    │ (Stage 9)│    │ (Stage 8)│    │ (Stage 7)│    │ (Stage 6)│
   │  18mo+   │    │  18mo+   │    │  18mo+   │    │  18mo+   │    │ Silver   │
   │          │    │          │    │          │    │          │    │ Medalist │
   │          │    │          │    │          │    │          │    │ MVP =    │
   │          │    │          │    │          │    │          │    │ Days 1-30│
   └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘

LAYER 3 (INTELLIGENCE) — packaged as feature on Layer 1/2 or separate SKU
   Cross-cutting. Public market intel page is fragment 1. Future: enterprise market intel ($50k-500k/yr).
```

The forward arrows are workflow direction. The feedback loops back to "Cross-Stage Calibration" are where the moat lives. Most existing recruiting tools live in one box and have no feedback loops. SourcingNav's bet is that the loops are the whole product, and that the taxonomy + preference graph + cross-company signal compound into a defensible moat over time.

---

## Strategic risks worth naming (revised)

V1 named 5 risks. V2 keeps the spirit but updates them.

**Risk 1: Building stages without customer signal.**
The roadmap above is plausible. It's not validated. Before committing to Stage 2 outreach engine, we need at least 3 Pro customers who explicitly say "I'd pay more if you did outreach + scheduling too." Otherwise it's speculative build with limited capital.

**Risk 2: Outcome capture UX kills the calibration moat.**
Match calibration depends on knowing which placements stuck. Schedule analytics depend on knowing when interviews actually happened vs got rescheduled. **Preference graph depends on accept/reject capture being one click.** If logging takes more than 1-2 clicks per event, customers won't do it, the flywheel never spins. UX design for outcome capture is as important as the AI features.

**Risk 3: Two-product confusion (Layer 1 vs Layer 2).**
The recruiter-facing surface (SourcingNav) and the HR-facing surface (people-ops, eventually) are different products with different buyers. Easy mistake: try to make one surface serve both, which serves neither well. **Decision rule from this doc: shared database, separate UI surfaces, separate SKUs.**

**Risk 4: Anchoring on N=100 as the breakthrough.**
N=100 is when single-stage clustering gets reliable. It's NOT when cross-stage calibration gets reliable — that needs ~12 months of tracked outcomes per stage. Don't over-promise on the calibration moat in customer conversations until the data backs it. **Same risk applies to the preference graph — it needs 20-30 decisions per recruiter before it meaningfully personalizes.**

**Risk 5: Solo founder + multi-stage product = priority traps.**
Each stage built means more code to maintain. Stage 5 onwards becomes scale-impossible without team. Realistic plan: Stages 1-3 solo, then either fundraise / hire OR commit to a depth-not-breadth strategy and own the source/match niche better than anyone.

**Risk 6 (new in v2): Asset inventory drift.**
Five elite prompts are sitting on disk in three different directories. If integration is delayed too long, code bitrots, the original developer (Jason) loses recall of what each one does, and the asset effectively decays to zero. Prioritize integration in the first 30 days specifically because of this.

**Risk 7 (new in v2): Trust + fraud surface as a parallel competitor.**
The deepfake interview problem is being solved right now by adjacent vendors (BioCatch, IDfy, ID.me). If they extend into recruiting workflows before SourcingNav adds verification features, the trust positioning becomes harder. Not urgent, but on the radar.

---

## What I'd do tomorrow

V1 said: define the candidates table schema + draft Stage 2 customer interview script.

**V2 says:** different two actions, both ~2 hours of work each.

1. **Audit the deployment status of the Knowledge Hub "Protect Yourself" section.** Check if it's live at sourcingnav.com/ui/knowledge-hub.html. If not, redeploy. This is loose end from late April.

2. **Pull Competitive Intelligence prompt from disk into the placement-ops repo.** Copy from `~/Desktop/untitled folder/candidatIQ/CompetitiveIntelligence.tsx` + `intelligence_engine.py`, extract the prompt logic, draft an integration plan for chaining it after the Boolean Builder's company cluster output. This is the smallest concrete first step toward Days 1-30 of the new sequencing plan.

These two together: ~4 hours. Both produce a tangible deliverable. Both unblock the next 30 days of work.

---

## Open questions for next strategy revision

These aren't action items. They're open questions where the answer would change the roadmap. Worth thinking about explicitly between now and v3.

1. **What's the smallest customer that justifies building the Outreach engine (Stage 2)?** Number of Pro customers, $ MRR threshold, specific request count.
2. **Does Layer 2 launch as a separate SourcingNav surface, or as an entirely separate product?** Could be SourcingNav People Ops, could be a different brand. Different go-to-market.
3. **At what N does the public market intel page become commercially valuable as Layer 3?** N=100? N=500? N=1000? Different N triggers different go-to-market for that asset.
4. **Is the preference graph a per-user feature or per-organization feature?** A solo recruiter wants their own graph. A 10-person agency might want a shared one. Pricing implications.
5. **Do we ever monetize the public market intel page directly, or is it always a marketing asset?** Could become a Substack-style premium intel subscription if we get the right audience.
6. **What's the relationship to the Claude Code skill system in placement-ops?** The 16 modes in the original placement-ops repo are still there. Some recruiters might prefer terminal-based workflows. Keep, deprecate, or eventually offer both?
7. **Is the bigger threat an existing recruiting tool building "the flywheel," or a new entrant doing it?** Affects competitive strategy and whether to fundraise vs bootstrap.

---

## Diff vs v1 (what changed and why)

For future-Jason or any reader comparing the two docs:

| Change | Why |
|---|---|
| Added explicit Layer 1 / 2 / 3 product framing | Was implicit in v1, makes sequencing clearer |
| Added asset inventory section | V1 didn't surface the elite-prompt backlog at all, biggest oversight |
| Added preference graph as moat (Noon insight) | V1 treated calibration as company-level only, missed per-user moat |
| Added "Reverse Gem" architectural commitment | V1 implied per-search universe, v2 commits |
| Added trust/fraud surface | V1 didn't address this attack vector |
| Added ATS integration sequence | V1 punted entirely |
| Added Silver Medalist Rediscovery as Stage 6 bridge | V1 treated Stage 6 as light sketch, this is a high-leverage 2-week build |
| Added Smart Response System + prep-brief-on-calendar | V1 was vague on Stage 2, this is the genuinely-smarter-than-Gem feature |
| Revised 90-day sequencing | V1 assumed greenfield builds. V2 prioritizes integrating already-built elite prompts because asset inventory shows they exist. |
| Added new risks (asset drift, trust competitors) | Surfaced from updated context |
| Updated tomorrow-morning actions | V1 actions were Stage 2-focused. V2 actions are loose-end audit + first elite-prompt integration step. |

---

*Doc complete. Companion: `2026-04-28-shadow-ai-detection-prd.md` for the parked HireGuard scope. Predecessor: `2026-04-28-talent-os-flywheel-roadmap.md` for v1 history.*
