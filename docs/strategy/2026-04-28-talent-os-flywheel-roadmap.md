# SourcingNav: The Talent OS Flywheel

**Author:** Jason Shotwell
**Date:** 2026-04-28
**Status:** Strategic roadmap, internal
**Companion docs:** `2026-04-28-shadow-ai-detection-prd.md` (parked HireGuard scope — explicitly NOT part of SourcingNav)

---

## The thesis in one paragraph

Every recruiting tool today owns one stage of the employee lifecycle. ATS owns intake. Sourcing tools own discovery. Scheduling tools own coordination. Performance tools own evaluation. Each one produces data the others never see. **SourcingNav's bet is that the moat isn't any single stage — it's the cross-stage calibration data that no point solution can replicate.** When the system can see that a placement made via "Senior ML Engineer with PyTorch" boolean strings led to a promotion within 18 months at three different companies, the next time someone sources for that role the system knows which boolean tier actually predicts success — not just which one returns more profiles. Each stage produces signal that makes every other stage smarter. That's the flywheel.

## What this doc is and isn't

**Is:** Sequenced roadmap for the next 6-18 months of SourcingNav. Each lifecycle stage gets the same six-question treatment. Stages we're closest to get real depth. Stages further out get directional sketches that hold the shape but don't pretend to detailed plans.

**Isn't:** A pitch deck. A feature list. A complete spec. A commitment to ship in any specific order — sequencing depends on data, customer signal, and what the existing system reveals as it accumulates intakes.

**Explicitly out of scope:** HireGuard / shadow AI detection (separate product, separate buyer, parked in companion doc). Compliance/governance tooling for AI deployments (AIR Blackbox scope). Anything that isn't a recruiter or hiring manager workflow.

---

## The six questions, applied to every stage

Each stage gets these answered in the same order:

1. **Problem.** What recruiter/HM pain does this stage solve?
2. **Unique data.** What does this stage produce that no point solution captures?
3. **Cross-stage flywheel.** How does this stage's data make other stages smarter?
4. **MVP shape.** Smallest shippable version that starts contributing data.
5. **Dependencies.** What must exist before this stage is buildable.
6. **Effort honestly.** Days/weeks/months estimate, with the parts that are uncertain explicitly marked.

If a stage section can't answer Q2 and Q3 substantively, that's the signal it's not differentiated and shouldn't be built.


---

## Stage 1: Source — DONE (Phase 1 launched)

**Status: shipped. Phase B3 just completed. Live at sourcingnav.com.**

**Problem.** Recruiters spend the first hour of every search building boolean strings, identifying watering holes, and inferring company clusters. That work is repetitive, manual, and wasted because the next search starts from zero again.

**Unique data.** Per-JD signature graph: canonical skills (with severity), functional aliases, adjacent crossover roles, watering hole types, poaching target companies, difficulty score, comp range. Stored in `jd_signatures` table. 32 signatures as of today. Cross-company calibration begins at this stage — same role at different companies surfaces in clusters automatically.

**Cross-stage flywheel.** This is the foundation layer. Every other stage queries against signatures. When match (Stage 3) needs to score a candidate, it pulls the signature for the role being matched against. When interview (Stage 4) needs to know which signal differentiates good vs great hires, it joins outcomes back to signatures. When perform (Stage 7) needs to know which sourcing patterns predict success, it groups by signature features.

**MVP shape.** Already shipped: free-tier intake parser + boolean builder + clustering. Pro tier: skill triage briefing + boolean extensions + watering hole X-rays. Phase B3: signature graph + clustering + market intel page.

**Dependencies.** None — this is the root of the tree.

**Effort honestly.** Done. ~3 weeks of work over the past month. Remaining within this stage: parser prompt tightening (verbose-skill JD style produces wordy competency strings — Phase B3 keyword extraction patches this downstream but the real fix is upstream), Phase B4 skill mesh co-occurrence, eventual archetype promotion flow (Phase B3 Part E).

**Maturity:** N=32 signatures, 6 coherent clusters, 11 outliers. Honest about the corpus size in the public market intel page. Reliability of cluster patterns improves significantly past N=100 — that's the next inflection point for this stage.

---

## Stage 2: Schedule — NEAR-TERM (next 4-8 weeks)

**Status: not started. Closest next stage to build.**

**Problem.** Once a candidate is interested, the back-and-forth scheduling — recruiter → candidate → hiring manager → calendar conflicts → reschedule — eats 2-5 days per candidate. Existing tools (Calendly, Goodtime, Prelude) own this stage but they're disconnected from sourcing context.

**Unique data.** Time-to-first-interview by role type, by company size, by candidate seniority. Reschedule frequency and reasons. Drop-off rate between "interested" and "first interview." This is operational data nobody currently captures because it lives across calendar tools and ATS notes.

**Cross-stage flywheel.** Drop-off rate between sourcing and first interview tells us which roles are easy vs hard to fill at the *engagement* level (not the supply level). Reschedule patterns reveal which companies have hiring manager attention bandwidth issues — relevant for the deal-room (which clients are real vs flaky). Time-to-first-interview by signature feature (e.g., "cleared defense embedded roles take 3.2x longer than commercial AI training roles") feeds back to Stage 1's difficulty score with empirical grounding.

**MVP shape.** Two-level scoping for v1. Level A: candidate-side calendar link generation tied to a requisition. Level B: hiring manager calendar integration so the system can find slots automatically. **Recommend Level A only for MVP** — it's a wrapper around Calendly's API or similar, ships in days, and produces the time-to-first-interview data that makes the data flywheel valuable. Level B is real ops integration and 4-6 weeks of work that doesn't have to happen in v1.

**Dependencies.** Need a "candidate" entity in the schema (currently we have requisitions and signatures but no candidate model). Need a way for a recruiter to mark a candidate as "ready to schedule" — either a checkbox in the pipeline UI or a state machine on a candidate record.

**Effort honestly.**
- Schema additions (candidates, scheduling_events tables): 2-3 days
- Calendly/similar integration for Level A: 3-5 days
- UI for "schedule with candidate" flow on the pipeline page: 3-4 days
- Total: ~2 weeks for the MVP. Level B integration ATS-side adds 4-6 weeks.

**Risk.** Calendar integrations are notorious for edge cases (timezones, daylight savings, recurring meetings, OAuth token refresh). Budget 1.5x.

---

## Stage 3: Match — NEAR-TERM (parallel with Schedule, or right after)

**Status: not started. Logical second-priority after Schedule.**

**Problem.** Recruiters source 50 candidates, present 5, place 1. The 45 not-presented are usually rejected based on pattern matching ("this profile doesn't look quite right") that's hard to verbalize and impossible to learn from. Existing matching tools (Eightfold, hireEZ) score profiles against JDs but don't expose the reasoning.

**Unique data.** Per-candidate match score *with reasoning grounded in the signature*. Track which match scores led to first interview, which led to offer, which led to placement. Within 12 months of accumulated data per signature, the match score becomes empirically calibrated to actual outcomes.

**Cross-stage flywheel.** This is the most flywheel-rich stage. Match scores feed Stage 4 (interview) by tagging which skills to probe deepest. Match outcomes (placed vs not-placed) feed back to Stage 1's signature graph as ground truth — over time the system learns which signature features actually predict placement success. Match data over many companies tells us which skill substitutions are real vs theoretical (e.g., "Vue.js → React" works 83% of the time at startups, 41% of the time at FAANG-tier companies).

**MVP shape.** Take a parsed signature + a candidate profile (LinkedIn export, resume text, GitHub URL). Score against the signature using the canonical_skills + adjacent_crossover features. Output: tier-stratified match (Tier 1 fit / Tier 2 phone-screen / Tier 3 ramp-up) with skill-level reasoning grounded in JD quotes — same truthfulness pattern we use in the parser. Free tier: text-paste candidate profile, get a match score. Pro tier: bulk scoring, GitHub auto-fetch, calibration to historical placements.

**Dependencies.** Stage 2 OR a manual candidate entity. Need outcome tracking — at minimum a "placed yes/no" boolean per candidate per req. Without outcomes, match scoring is just LLM opinion; with outcomes, it becomes empirically calibrated.

**Effort honestly.**
- Schema additions (candidates, candidate_evaluations tables): 3-4 days
- Match scoring AI prompt + integration: 1 week
- UI for paste-candidate-get-match flow: 4-5 days
- Bulk scoring (Pro tier): 1 additional week
- **Total MVP: ~3 weeks. Calibration loop adds another 4-6 weeks but doesn't block launch.**

**Risk.** The hard part isn't building the matcher. It's getting recruiters to log outcomes. Without outcome data, the calibration moat never builds. Need to design the UI so logging "placed/not-placed" is one click, ideally automatic when a placement_guarantee_tracker fires elsewhere.

---

## Stage 4: Interview — MID-TERM (3-6 months out)

**Status: not started. Ships after Schedule + Match are stable.**

**Problem.** Interview loops produce inconsistent signal. Different interviewers ask different questions, score on different rubrics, and weight differently. The "team debrief" is supposed to harmonize this but in practice it's the loudest interviewer's opinion.

**Unique data.** Per-question signal-to-decision correlation. Which interview questions actually predict offer / placement / 12-month retention vs which are noise that everyone asks because they always have. Difficulty calibration of questions across companies — same question is "easy" at one company and "differentiating" at another, and that difference IS the signal.

**Cross-stage flywheel.** Interview outcomes (passed loop, placed, retained) feed back to Stage 3 (match) — if the matcher said "Tier 1 fit" and the candidate failed at onsite, that's calibration data. Question-level efficacy data tells Stage 1 (sourcing) which skills to weight highest in the boolean strings (the skills that actually differentiate at interview, not the skills the JD claims to want).

**MVP shape.** Interviewer-side scorecard tool tied to a requisition. Each interviewer logs scores against a rubric the system generates from the JD (using the existing Pro Skill Briefing — we already produce the tier 1/2/3 skill list, the rubric is just that with score fields). Capture: who interviewed, what they asked, what they scored, what they decided. Aggregate scoring + variance analysis.

**Dependencies.** Stage 3 (match) provides the candidate context. Stage 2 (schedule) provides the interview slot context. Both should exist before this is meaningful. Could be built earlier as a standalone scorecard tool but loses the flywheel value.

**Effort honestly.** ~6-8 weeks. The hard parts are not the data model — it's the team-collaboration UX (multiple interviewers, real-time-ish updates, debrief view), which is genuinely difficult product work.

**Risk.** Companies have entrenched interview tools (Greenhouse scorecards, Lever interview kits). Replacing them is hard. Strategic question to answer before building: do we replace, or do we capture data BEHIND existing tools by integrating?

---

## Stage 5: Offer — MID-TERM (after Interview)

**Status: not started. Sketch level, not detailed plan.**

**Problem.** Offer construction is opaque: comp benchmark, equity, sign-on, leveling — all of it negotiated under information asymmetry. Existing tools (Levels.fyi, Pave) provide salary data but don't capture WHICH offers actually got accepted.

**Unique data.** Per-offer acceptance rate by structure (base/equity/sign-on mix), by company stage, by candidate level. Counter-offer frequency and what wins them. Offer-to-acceptance time as a leading indicator of competing offers.

**Cross-stage flywheel.** Offer data feeds Stage 1 (sourcing) by sharpening comp_snapshot accuracy with empirical acceptance data, not just market data. Counter-offer patterns by company tell Stage 3 (match) which clients are at-risk for placements falling through.

**MVP shape.** TBD — depends on whether this becomes an offer-construction tool, an offer-tracking tool, or just a logging feature. Most likely answer: lightweight "offer made / offer status" tracking inside the deal-room, with structured fields for the offer components.

**Dependencies.** Stage 3 + Stage 4. Logging without context produces useless data.

**Effort honestly.** Genuinely uncertain. ~4-6 weeks for a tracking-only version. ~3 months if it becomes an offer-construction tool with comp benchmarking.

**Strategic question.** Is this a SourcingNav stage or a partnership? Pave / Levels / Salary.com already own comp data. Maybe we don't need to build this stage — we just need to consume their API and add the flywheel loop on top.

---

## Stage 6: Onboard — LATER (6-12 months out)

**Status: not started. Sketch level. Significant question whether this is in scope at all.**

**Problem.** First-30-day attrition (rare-but-disastrous) and first-90-day productivity (the real bar) are barely measured. New hires drop out, get reassigned, or quietly underperform — and that signal never reaches the recruiting org.

**Unique data.** First-30-day retention by role type, by sourcing pattern, by interview signal. Time-to-first-meaningful-contribution. Which onboarding patterns predict 12-month retention.

**Cross-stage flywheel.** This is where the flywheel gets really powerful. If we can see that Tier 1 matches who came through a specific boolean string had 94% 90-day retention while Tier 1 matches via a different string had 71%, that's calibration data the next sourcing run can use.

**MVP shape.** Likely a customer-side integration with the company's HRIS (Workday, Rippling, BambooHR) plus a manager-side "is this hire working out?" pulse check. Genuinely complex.

**Dependencies.** All previous stages. Customer trust at a level we don't yet have. HRIS integrations.

**Effort honestly.** ~3-6 months for a real version. Probably not the right next investment unless a specific Pro customer asks for it.

**Strategic question.** Do we build this, or do we partner with HRIS providers to consume their data? Building means deep integration work. Partnering means we don't own the data.

---

## Stages 7-10: Perform → Develop → Retain → Depart — VISION-LEVEL

**Status: directional only. No detailed plans.**

These four stages share a common shape: they're about what happens to a placed employee over the multi-year arc of their tenure. The flywheel value is enormous (calibrating "who's actually thriving" vs "who looked good on paper") but the integration cost is also enormous (HRIS, performance review systems, equity vesting tracking, exit interviews).

**Honest assessment:** these stages are 18+ months out at minimum. Probably 3+ years for full coverage. They're real and they matter for the long-term moat — but trying to scope them today is fortune-telling.

**Cross-stage flywheel signal even at sketch level:**
- Perform: 12-month performance review scores by sourcing pattern → calibrates Stage 1
- Develop: promotion rate by hire signature → calibrates Stage 3
- Retain: 24-month retention by source → calibrates Stage 1 + Stage 4
- Depart: regretted vs unregretted attrition by signature → calibrates everything

**Strategic question for ALL four:** the recruiting buyer (Stage 1's customer) doesn't necessarily own this data — it lives in HR. The buyer for stages 7-10 is People Ops / HR Analytics. That's a different sale, possibly a different SKU, possibly a different surface. From the memory, the existing roadmap mentions a `people-ops.html` surface at $499-2k/mo — that's where these stages probably live. Worth keeping that distinction sharp: SourcingNav is recruiter-facing, the lifecycle data product is HR-facing, they share a database but they're different products.


---

## The flywheel in one diagram (text version)

```
                           ┌─────────────────────────────────────────────┐
                           │           Cross-Stage Calibration            │
                           │       (the moat — N years to replicate)      │
                           └─────────────────────────────────────────────┘
                                       ▲                       ▲
                                       │                       │
                                       │ feedback              │ feedback
                                       │                       │
   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
   │  SOURCE  │───▶│ SCHEDULE │───▶│  MATCH   │───▶│INTERVIEW │───▶│  OFFER   │
   │ (Stage 1)│    │ (Stage 2)│    │ (Stage 3)│    │ (Stage 4)│    │ (Stage 5)│
   │   DONE   │    │  4-8wk   │    │ 6-10wk   │    │  3-6mo   │    │  3-6mo   │
   └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                          │
                                                                          ▼
   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
   │  DEPART  │◀───│  RETAIN  │◀───│ DEVELOP  │◀───│ PERFORM  │◀───│ ONBOARD  │
   │(Stage 10)│    │ (Stage 9)│    │ (Stage 8)│    │ (Stage 7)│    │ (Stage 6)│
   │ 18mo+    │    │ 18mo+    │    │ 18mo+    │    │ 18mo+    │    │ 6-12mo   │
   └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

The forward arrows are the obvious workflow direction. The feedback loops back to "Cross-Stage Calibration" are where the moat lives. Every placement event, every interview score, every attrition event becomes a row that calibrates the predictions of every previous stage.

Most existing recruiting tools live in one box and have no feedback loops. SourcingNav's bet is that the loops are the whole product.

---

## Sequencing recommendation

If forced to pick the next 90 days of work, here's the honest order:

**Days 1-30: Polish + Stage 1 maturation**
- Ship JD parser prompt tightening (fix verbose-skill issue at source rather than patching downstream)
- Phase B3 Part E: cluster promotion to taxonomy (closes Phase B3 fully)
- Mobile responsive on app pages
- Drive N from 32 to 100+ via outreach to existing signups + content marketing
- *Why:* Stage 1 is the foundation; cracks in the foundation get expensive to fix later.

**Days 31-60: Stage 2 (Schedule) MVP**
- Schema additions (candidates, scheduling_events)
- Calendly-style integration for candidate-side scheduling
- "Schedule" action in pipeline UI tied to a requisition
- *Why:* Schedule is the cheapest stage to build that produces meaningful flywheel data. Time-to-first-interview by signature is immediately useful as content/marketing material. Sets up Stage 3.

**Days 61-90: Stage 3 (Match) MVP**
- Schema additions (candidates already exist from Stage 2, add candidate_evaluations)
- Match scoring against signature, LLM-graded with truthfulness guardrails
- Outcome tracking UI (one-click "placed/not-placed")
- *Why:* Match is the most flywheel-rich stage. Even at low volume the calibration data starts compounding. This is where SourcingNav's defensibility starts to feel real to a sophisticated customer.

**Days 91+: Either Stage 4 (Interview) OR market-led pivot**
- By day 90 you'll have signal from Pro customers about which next stage they'd pay for. Build that one.
- If no clear customer signal, default to Stage 4 because it's the next logical workflow step.

**What's deliberately not in this 90-day plan:**
- Stages 5-10 (too early)
- HireGuard / shadow AI detection (parked)
- Public market intel page polish (already shipped, can iterate slowly)
- Mobile responsive on the marketing site (low priority compared to app workflow polish)

---

## Strategic risks worth naming

**Risk 1: Building stages without customer signal.**
The roadmap above is plausible. It's not validated. Before committing to Stage 2, we need at least 3 Pro customers who explicitly say "I'd pay more if you did scheduling too." Otherwise we're building speculatively with limited capital.

**Risk 2: The flywheel needs outcomes data that customers don't naturally log.**
Match calibration depends on knowing which placements stuck. Schedule analytics depend on knowing when interviews actually happened vs got rescheduled. If logging takes more than 1-2 clicks per event, customers won't do it, and the flywheel never spins. UX design for outcome capture is as important as the AI features.

**Risk 3: Two-product confusion.**
The recruiter-facing surface (SourcingNav) and the HR-facing surface (people-ops, eventually) are different products with different buyers. Easy mistake: try to make one surface serve both, which serves neither well. Easy fix: keep the database shared, keep the surfaces separate, sell them as two SKUs.

**Risk 4: Anchoring on N=100 as the breakthrough.**
N=100 is when single-stage clustering gets reliable. It's NOT when cross-stage calibration gets reliable — that needs ~12 months of tracked outcomes per stage. Don't over-promise on the calibration moat in customer conversations until the data backs it.

**Risk 5: Solo founder + multi-stage product = priority traps.**
Each stage built means more code to maintain. Stage 5 onwards becomes scale-impossible without team. Realistic plan: Stages 1-3 solo, then either fundraise / hire OR commit to a depth-not-breadth strategy and own the source/match niche better than anyone.

---

## What I'd do tomorrow

If I had to pick one concrete action for tomorrow morning that follows from this doc: **define the schema for the `candidates` table** and stub out the Stage 2 candidate entity. Not the full Schedule build — just the data model, so every other downstream decision can reference it. ~2 hours of work that unblocks Stage 2 + Stage 3 + Stage 4 simultaneously.

Followed by: **draft the Stage 2 customer interview script** to validate that scheduling pain is real for the existing Pro customer (info@nostalgicskinco.com or the 3 launch signups if any of them re-engage). 30 minutes. Without that validation, building Stage 2 is speculation.

That's it. Strategic doc shouldn't end with a 50-item to-do list. It should end with two specific actions for tomorrow.

---

## Appendix: questions worth answering before next strategy revision

These aren't action items — they're open questions where the answer would change the roadmap. Worth thinking about explicitly between now and the next time this doc gets revised.

1. **What's the smallest customer that justifies building Stage 2?** (Number of Pro customers, $ MRR threshold, specific request count)
2. **What's the relationship between SourcingNav and the eventual people-ops product?** Same DB? Separate surfaces? Sold together or apart?
3. **What's the failure mode if cross-stage calibration data is too noisy at low N to be useful?** (Honest: it might just take longer than we think)
4. **Is there a stage 5-10 that's actually high-leverage at low N?** (E.g., Depart — exit interview signal might be valuable even at low volume because regretted attrition is high-information)
5. **What's the bigger threat — an existing recruiting tool building "the flywheel," or a new entrant doing it?** (Affects competitive strategy)
6. **Do we ever monetize the public market intel page directly, or is it always a marketing asset?**

---

*Doc complete. Companion: `2026-04-28-shadow-ai-detection-prd.md` for the parked HireGuard scope.*
