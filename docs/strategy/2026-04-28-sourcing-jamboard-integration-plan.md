# Sourcing Jamboard Integration Plan

**Author:** Jason Shotwell
**Date:** 2026-04-28
**Status:** RETIRED — superseded by Path C redistribution (see header note below)
**Predecessor:** `2026-04-28-talent-os-flywheel-roadmap-v2.md` (Days 1-30 sequencing item)
**Sibling:** `2026-04-28-competitive-intelligence-integration-plan.md` (same shape, just shipped)

---

## RETIREMENT NOTE (added 2026-04-28, end of session)

This plan was written, reviewed, and then explicitly retired in the same
session. The retirement decision and the rationale are worth preserving
because they're a non-obvious product judgment.

### Why this plan was retired

After Jason reviewed the plan, he flagged that the proposed 11-section
Sourcing Jamboard would overlap heavily with existing tools:

- 7 of 11 sections overlapped HEAVILY or TOTALLY with what we already ship:
  alternate_titles (totally duplicates JD parser alt_titles), skills_adjacency
  (totally duplicates transferable_skill_clusters), geographic_opportunities
  (totally duplicates market360.talent_hotspots), creative_boolean_strings
  (totally duplicates Boolean Builder), platform_strategies and
  underutilized_channels (heavy overlap with watering_holes and pro_xrays),
  passive_candidate_triggers (partial overlap with objection_playbook).
- Building a 6th Pro tab (after Briefing, Booleans, CI, Discovery future,
  and Jamboard) would have created a "kitchen sink" feel that erodes
  customer trust in the product's focus.
- Tab proliferation reduces concentration of value. When a feature is its
  own tab, only customers who click see it. When it's added to an existing
  tab, every user of that tab sees it.

Three options were considered:

  Path A — Build Jamboard as planned (11 sections, ignore overlap)
  Path B — Build Jamboard as 4-5 net-new sections only
  Path C — Distribute the net-new ideas into existing tools, no new tab

Jason chose Path C.

### Where each net-new idea went

| Original Jamboard section | Final destination |
|---|---|
| dei_strategies | Standalone DEI Strategy integration (Days 1-30 in v2 roadmap, separate work) |
| career_switchers | Augmented Pro Skill Briefing as `career_switcher_archetypes` field — Pro tier |
| hidden_talent_pools | Augmented Pro Boolean Extensions as `hidden_talent_pools` field — Pro tier |
| passive_candidate_triggers | Parked for Stage 2 (Engage / Outreach) |
| timing_strategies | Parked for Stage 2 (Engage / Outreach) |

### What actually shipped from this plan

In the same session as plan retirement, Jason and Claude executed Path C:

1. **PRO_INTAKE_PROMPT** extended with a "CAREER SWITCHER ARCHETYPES" section
   and a `career_switcher_archetypes` field in the JSON schema. Prompt instructs
   the model to identify 3-5 role-to-role transitions (from_role, to_role,
   transferable_skills, where_to_find, pitch_angle, transition_difficulty)
   with strict honesty rules (no fabricated success rate %, transferable_skills
   must intersect canonical_skills, max 5 archetypes).

2. **PRO_BOOLEAN_PROMPT** extended with a "HIDDEN TALENT POOLS" section and
   a `hidden_talent_pools` field in the JSON schema. 4-6 non-obvious source
   categories (pool_name, why_target, platforms, search_tips) with strict
   no-overlap rule against existing pro_xrays and no fabricated response rate %.

3. **`_run_pro_skill_briefing`** consumer code updated to return a dict
   `{briefing: [...], archetypes: [...]}` instead of just the briefing list.
   Endpoint storage block updated to persist both fields under their canonical
   names (`parsed.pro_skill_briefing` and `parsed.career_switcher_archetypes`).

4. **`renderProSkillBriefingCard`** updated to accept a third `archetypes`
   argument and render a new "Non-obvious source pools" section after the
   skill rows when archetypes are present. Visual treatment: bordered cards
   with difficulty badge (easy/moderate/hard), from-role → to-role title,
   pitch angle in accent-bordered callout, transferable skills as green
   chips, where-to-find as bordered list.

5. **`renderProBooleanExtensionsCard`** extended to read `ext.hidden_talent_pools`
   and render a new "Hidden talent pools" section between pro_xrays and
   extended_mentor_notes. Visual treatment: bordered cards with pool_name
   header, why_target rationale, platforms as blue chips, search_tips in
   monospace accent-bordered code-style block.

6. **max_tokens caps bumped** from 3500 → 7000 on both PRO_INTAKE and
   PRO_BOOLEAN endpoints because smoke testing showed the augmented prompts
   produce ~5300 (PRO_INTAKE) and ~6100 (PRO_BOOLEAN) output tokens.
   Without the bump, both prompts would have truncated mid-output and
   returned malformed JSON.

### Smoke-test verification

Both prompts were smoke-tested against the Skydio req
(`a9783797-79c5-4808-a138-037874d81057`) with real Haiku 4.5 calls before
UI work. Results:

  career_switcher_archetypes: 5/5 archetypes returned, all role-specific,
  zero fabricated stats, all from_role values specific (not generic
  "Engineer"), all transferable_skills meaningfully intersect canonical_skills,
  all where_to_find values name concrete companies/communities.

  hidden_talent_pools: 6/6 pools returned, all role-specific, zero fabricated
  response rate %, zero overlap with pro_xrays venues, all platforms
  specific (not generic "LinkedIn"), search_tips include runnable booleans.

Quality issues at deploy: 0.

### What got NOT shipped

- No new endpoint (no `/api/intake/sourcing-jamboard`)
- No new UI tab or card
- No `requisitions.jamboard_json` cache (the column stays provisioned but
  unused — it might still be valuable later if a different feature wants
  per-req caching)
- No DEI Strategy section (separate planned integration, not part of Path C)
- No timing_strategies section (parked for Stage 2)
- No passive_candidate_triggers section (parked for Stage 2)

### Diff vs the original plan in this doc

The plan below describes the standalone-feature approach Jason rejected.
It's preserved verbatim because:

1. The architectural research (that the CandidatIQ "9.5/10 prompt" was
   actually hardcoded Python with mock-data fallback) is reusable next time
   we audit a CandidatIQ asset.
2. The overlap audit framework is reusable for future feature decisions.
3. The fact that 4 of 11 sections were already produced natively by the
   JD parser is an underappreciated architectural strength of SourcingNav
   that should be documented somewhere.

The plan IS the historical record, not a forward-looking artifact.

---

## ORIGINAL PLAN BELOW (RETIRED — preserved for reference)

## TL;DR — what changed during research

V2 roadmap claimed Sourcing Jamboard was a "9.5/10 prompt sitting on disk." After reading the source:

- **There is no AI prompt.** Like Competitive Intelligence, the CandidatIQ implementation is hardcoded Python with role-keyword switches and FAANG-tier defaults. 916 lines, 11 generation functions, all `def` (not `async def`).
- **The "9.5/10" rating was based on output structure, not implementation quality.** The 11 categories produced (alt titles, hidden pools, skills adjacency, career switchers, platform strategies, DEI, channels, geo, passive triggers, timing, booleans) are genuinely valuable to a senior recruiter. The Python that produces them is brittle role-keyword matching that breaks on niche searches.
- **The TSX is incomplete.** It renders only 5 of the 11 sections. Comment in source: `{/* Continue with remaining sections... */} {/* I'll add the rest in the next chunk */}`. The other 6 sections have no UI code to port.
- **JAMBOARD_FIX.md confirms it's mock data in production.** Nov 2025 fix note: "Returns mock sourcing intelligence data."

**Revised approach:** Build a real AI-driven Sourcing Jamboard endpoint that takes the canonical_skills + role context + watering holes from the existing JD parser output and produces structured intelligence using a single Haiku 4.5 call. Reuse the JSON contract structure (11 sections) for frontend portability. Build the UI for all 11 sections fresh because the TSX only had 5.

This is essentially **CI integration round 2** with different output structure.

---

## What was read and where

| File | Lines | Purpose | Key finding |
|---|---|---|---|
| `~/Desktop/untitled folder/candidatIQ/frontend/components/SourcingJamboard.tsx` | 511 | Frontend React | 11-section JSON contract, but renders only sections 1-5. Sections 6-11 have no render code. |
| `~/Desktop/untitled folder/candidatIQ/backend/sourcing_jamboard.py` | 916 | Backend (hardcoded Python) | 11 `def` functions producing per-section static data. Zero AI calls. Role-keyword switches like `if "embedded" in title_lower`. |
| `~/Desktop/untitled folder/candidatIQ/app/routes/jamboard_routes.py` | 185 | FastAPI routing | Wraps the backend functions. `JamboardRequest` model exists. Has a `generate_fallback_jamboard` function — confirming the production behavior is mock-when-anything-breaks. |
| `~/Desktop/untitled folder/candidatIQ/JAMBOARD_FIX.md` | — | Bug notes from Nov 2025 | Confirms production was "Returns mock sourcing intelligence data." Original 404 was a routing bug they patched with mock fallback. |
| `placement-ops/api/index.py` | 5497 | Our backend | NO existing jamboard code. Schema has `requisitions.jamboard_json TEXT` column already provisioned (someone planned ahead). |

**Footnote:** the v2 roadmap doc said the file was at `~/Desktop/untitled folder/candidatIQ/SourcingJamboard.tsx` but it's actually at `frontend/components/SourcingJamboard.tsx`. Same kind of path drift we caught in the CI plan. Worth noting.

---

## What the existing CandidatIQ implementation actually does

### The 11-section JSON contract (the structural value)

```typescript
interface JamboardData {
  alternate_titles: {
    seniority_levels: string[];        // ['Senior X', 'Lead X', 'Staff X', ...]
    functional_aliases: string[];       // ['Firmware Eng', 'Embedded Eng', ...]
    industry_specific: string[];        // ['Avionics Eng', 'Flight Software Eng', ...]
    total_count: number;
    search_strategy: string;            // human-readable summary
  };

  hidden_talent_pools: Array<{
    pool_name: string;                  // 'Bootcamp Grads', 'OSS Contributors', etc.
    why_target: string;
    platforms: string[];
    search_tips: string;
    avg_response_rate: string;          // '25-35%'
    diversity_boost: boolean;
  }>;

  skills_adjacency: {
    skill_adjacencies: {
      [skill_name]: {
        can_learn_from: string[];
        adjacent_skills: string[];
        why_transferable: string;
        learning_curve: string;
      };
    };
  };

  career_switchers: Array<{
    from_role: string;
    to_role: string;
    success_rate: string;
    transferable_skills: string[];
    where_to_find: string[];
    training_needed: string;
    why_successful: string;
    pitch_angle: string;
  }>;

  platform_strategies: Array<{
    platform: string;
    strategy: string;
    tactics: string[];
    response_rate: string;
    cost: string;
    best_time: string;
  }>;

  dei_strategies: { ... };              // sections 6-11 have data shape but no UI
  underutilized_channels: [...];
  geographic_opportunities: { ... };
  passive_candidate_triggers: [...];
  timing_strategies: { ... };
  creative_boolean_strings: { ... };
}
```

### What's USEFUL to port

- **The JSON contract structure.** All 11 sections genuinely add value to a senior recruiter. We'd reuse the field names so the frontend shape is portable.
- **The category framing.** "Hidden talent pools" and "career switchers" are framings recruiters intuitively understand. We're not inventing taxonomy; we're inheriting it.

### What's USELESS to port

- **The hardcoded role-keyword switches.** `if "embedded" in title_lower` returns wrong-or-empty data for the 80% of roles that aren't embedded.
- **The static FAANG-skewed defaults.** Same bug as CI: hardcoded Meta/Google/Stripe data fails on defense contractors, niche startups, biotech, etc.
- **The "avg_response_rate: '25-35%'" claims.** These numbers are made up. Shipping fabricated stats to recruiters who'll fact-check them is a credibility-killer.
- **The static "training_needed: 'Online courses'" answers.** Generic boilerplate.

### Why this is essentially CI again

| | CI | Sourcing Jamboard |
|---|---|---|
| Original implementation | Hardcoded 10-FAANG lookup | Hardcoded role-keyword switches |
| AI present? | No | No |
| Useful structural framing? | Yes (per-company analysis shape) | Yes (11-section sourcing intelligence shape) |
| Real prompt? | We wrote it | We need to write it |
| Frontend portable? | Yes (TSX interface) | Partially — 5 of 11 sections rendered |
| Pro tier? | Yes | Yes |
| Click-to-run pattern? | Yes | Yes |

This is the same play. Apply CI's pattern.

---

## What the SourcingNav side already produces

Per the JD parser output (verified in prod via Skydio req `a9783797...`):

```json
{
  "core": { "role_title": "...", "level": "...", "company": "...", "industry": "...", "location": "...", "remote_policy": "..." },
  "must_have_skills": [...],            // prose with rationale (UI display)
  "canonical_skills": [...],            // CLEAN atomic skill names — what we want
  "alt_titles": {                       // ALREADY EXISTS in our parsed_json
    "level_progression": [...],
    "functional_aliases": [...],
    "adjacent_crossover": [...]
  },
  "watering_holes": [                   // ALREADY EXISTS
    { "name": "...", "type": "...", "why": "..." }
  ],
  "market360": {
    "top_hiring_companies": [...],
    "talent_hotspots": [...],
    "poaching_targets": [...]
  },
  "transferable_skill_clusters": [...]  // ALREADY EXISTS
}
```

**Critical insight:** SourcingNav's JD parser already produces 4 of the 11 Jamboard sections natively in `parsed_json`:
- `alt_titles` ↔ `alternate_titles` (3 of the 5 categories)
- `watering_holes` ↔ part of `platform_strategies` + `underutilized_channels`
- `market360.talent_hotspots` ↔ `geographic_opportunities`
- `transferable_skill_clusters` ↔ `skills_adjacency`

**This changes the plan significantly.** The Sourcing Jamboard endpoint shouldn't generate everything from scratch — it should AUGMENT the existing parsed JD with the 7 sections we don't have:
- hidden_talent_pools
- career_switchers
- dei_strategies (overlap with future DEI Strategy integration — see open question below)
- passive_candidate_triggers
- timing_strategies
- creative_boolean_strings (overlap with Boolean Builder — see open question below)
- platform_strategies (overlap with watering_holes)

This is a faster, cheaper integration than CI was. Less prompt work because more data is already there.

---

## The integration plan

### Endpoint design

**New endpoint:** `POST /api/intake/sourcing-jamboard`

**Auth:** Bearer token, Pro tier only (server-side gate before any AI call, same pattern as CI).

**Request shape:**
```json
{
  "req_id": "uuid-of-existing-requisition",
  "diversity_focus": "None" | "Women in Tech" | "Black/Latinx Engineers" | "LGBTQ+ Talent" | "Military Veterans" | "People with Disabilities" | "Career Switchers"
}
```

`diversity_focus` is optional (defaults to "None"). Mirrors the TSX form's diversity dropdown so the AI can tailor DEI strategies if requested.

**Response shape (mirrors JamboardData contract):**
```json
{
  "req_id": "...",
  "role_title": "...",
  "ai_model": "claude-haiku-4-5",
  "honesty_caveat": "...",

  "alternate_titles": { ... },          // sourced from parsed.alt_titles, AUGMENTED by AI
  "hidden_talent_pools": [...],         // AI-generated (no overlap with parsed_json)
  "skills_adjacency": { ... },          // sourced from parsed.transferable_skill_clusters, augmented
  "career_switchers": [...],            // AI-generated
  "platform_strategies": [...],         // sourced from parsed.watering_holes, augmented
  "dei_strategies": { ... },            // AI-generated (only if diversity_focus != "None")
  "underutilized_channels": [...],      // AI-generated
  "geographic_opportunities": { ... },  // sourced from parsed.market360.talent_hotspots, augmented
  "passive_candidate_triggers": [...],  // AI-generated
  "timing_strategies": { ... },         // AI-generated
  "creative_boolean_strings": { ... }   // AI-generated
}
```

### How the AI call works

**ONE LLM call, prompt-engineered for honesty.** Inputs:
- role_title, level, industry, location, remote_policy from `parsed.core`
- canonical_skills (atomic, the right field per yesterday's fix)
- alt_titles (so AI can extend, not duplicate)
- watering_holes (so AI can extend, not duplicate)
- market360.talent_hotspots (so AI can extend, not duplicate)
- transferable_skill_clusters (so AI can extend, not duplicate)
- diversity_focus

Prompt skeleton (sketch — actual prompt designed Day 1):

```
You are a senior technical recruiter with 13+ years of sourcing experience...

REQUISITION CONTEXT:
- Role: ...
- Skills: ...
- Already-identified alt titles (extend, don't duplicate): ...
- Already-identified watering holes (extend, don't duplicate): ...
- Already-identified talent hotspots (extend, don't duplicate): ...

GENERATE A SOURCING JAMBOARD with these sections:
[detailed schema for each section]

HONESTY RULES (mandatory, same shape as CI prompt):
- Do NOT fabricate response rate percentages. If the rate isn't well-known
  for a platform/pool, say "varies, not benchmarked" instead of making up
  "25-35%".
- Do NOT fabricate "success_rate" claims for career switchers. Say
  "anecdotal evidence" or "industry-known pattern" instead of "70%".
- Do NOT include DEI strategies if diversity_focus is "None". Set field to {}.
- Career switcher "from_role" suggestions must be plausibly transferable
  given the canonical_skills. A "from_role: marketing manager" suggestion
  for an embedded firmware role is wrong.

Return JSON only.
```

### What's reused vs new

| Component | Source | Action |
|---|---|---|
| TypeScript JSON contract | CandidatIQ TSX | Reuse field names (frontend portability) |
| Section taxonomy (11 sections) | CandidatIQ Python | Reuse — it's good IA |
| Hardcoded role lookups | CandidatIQ Python | **Throw away.** Replace with AI. |
| Static response rate numbers | CandidatIQ Python | **Throw away.** AI uses honesty flags. |
| Static "training_needed" answers | CandidatIQ Python | **Throw away.** AI generates context-specific. |
| TSX render code (sections 1-5) | CandidatIQ TSX | Inspirational. NOT line-by-line port (Tailwind classes don't fit our stack). |
| TSX render code (sections 6-11) | — | Doesn't exist. We design from scratch. |

### Where it lives in `api/index.py`

Three additions, mirroring CI:

1. **`SOURCING_JAMBOARD_PROMPT` constant** near `COMPETITIVE_INTEL_PROMPT` (around line 1992). Long-form prompt with section-by-section schema and honesty rules.

2. **`SourcingJamboardRequest` Pydantic model** near `CompetitiveIntelRequest` (around line 2159). Fields: `req_id` required, `diversity_focus` optional default "None".

3. **Endpoint handler** `@app.post("/api/intake/sourcing-jamboard")` near the CI endpoint (around line 3628). Pattern:
   - Auth check (Bearer token)
   - Pro tier gate (return 402 if not Pro)
   - Cap check via existing `check_cap()`
   - Load req from DB, extract canonical_skills + alt_titles + watering_holes + market360 + clusters
   - Call AI via `call_ai()` with `SOURCING_JAMBOARD_PROMPT`
   - Parse JSON response with `parse_json_strict`
   - Persist to `requisitions.jamboard_json` column (the column was provisioned but never used) for caching — same pattern CI should have but doesn't yet
   - Return assembled JSON

**No new helper functions needed.** Unlike CI which had the deterministic boolean-templating helper, Jamboard is purely AI-driven. Simpler integration.

### UI surface decision

**Same pattern as CI: expanded card on intake results page, click-to-run.** Card lives at the bottom of the results region, below CI.

**Free tier:** Locked card with `█-block` placeholder showing 3 fake sections (Hidden Talent Pools, Career Switchers, Platform Strategies — the most visually compelling) + upgrade CTA.

**Pro tier (idle):** "Generate Sourcing Jamboard" button + ~10s wait estimate + 1-credit disclosure + diversity_focus dropdown.

**Pro tier (loading):** Spinner + "Building your sourcing intelligence..." (vary the loading message from CI's so it feels distinct).

**Pro tier (success):** Collapsible section accordion. 11 sections, each starts collapsed except the first 2 (alt titles + hidden pools). Each section is a self-contained renderer.

**Tier gating decision matches CI:** auto-chain off (click-to-run for cost control), 1 unit per cap.

---

## Schema changes

**One column already exists; nothing new needed for MVP.**

The schema audit during CI work showed:
```sql
requisitions.jamboard_json TEXT  -- already provisioned, never written to
```

**Plan:** persist Jamboard responses to this column on success (cache), so re-clicking the button shows the cached version instantly without re-firing the AI call. Show a "regenerate" button if the recruiter wants fresh output.

This is **better than CI's design** (CI doesn't cache, every click is a fresh AI call). Worth backporting to CI as a follow-up.

---

## Tier gating + pricing

**Same as CI:** free tier locked, Pro tier full access, 1 unit per generation.

**Cost per generation:** Single Haiku 4.5 call against ~3-4K input tokens (the requisition context) outputting ~2-3K tokens (11-section JSON). Estimated $0.04-0.08 per call. Cheaper than CI because no per-company iteration.

**Cap multiplier:** 1 unit per Jamboard generation. Same as CI.

---

## Privacy + Legal

**Lower-risk than CI.** No external company data, no candidate names. Output is sourcing strategy advice based on the recruiter's own JD. No GDPR exposure beyond what intake already has.

**One thing worth flagging:** the DEI strategies section. If we generate "where to find Black/Latinx engineers", we want the AI to be careful — recommending sources tied to identity-based communities is OK; making generalizations about candidate behavior by demographic is NOT OK. Honesty rule in the prompt should explicitly forbid stereotyping.

---

## Tests that prove it works

Same shape as CI's test list:

1. **Empty parsed_json fallback test** — req has no canonical_skills → endpoint returns 422 with helpful error, not a 500.
2. **Pro gate test** — free user hits endpoint → returns 402, AI call never fires.
3. **Cap exhaustion test** — Pro user at cap → returns 429, AI call never fires.
4. **AI failover test** — Anthropic key invalid → endpoint returns 503 with structured error.
5. **Real-data E2E test** — run against Skydio req `a9783797...`. Verify all 11 sections returned, no fabricated response rate numbers.
6. **Diversity focus test** — request with `diversity_focus: "Women in Tech"` returns populated `dei_strategies` field; with `"None"` returns empty `dei_strategies: {}`.
7. **Cache test** — second call to same req returns cached `jamboard_json` instantly without firing AI.
8. **Honesty caveat test** — response has `honesty_caveat` field set, response rate numbers either populated with specific values OR explicitly say "varies, not benchmarked" (no fabrication).

Same as CI: not setting up pytest infra. Real-data smoke script + manual QA.

---

## Rollout sequencing

**Same 3-day pattern as CI.**

**Day 1 (~4 hours):** Backend.
- Write `SOURCING_JAMBOARD_PROMPT` (longest single prompt task — 11 sections to schema)
- Write `SourcingJamboardRequest` model
- Wire endpoint with Pro gate + cap + cache logic
- Smoke test against Skydio demo req

**Day 2 (~4 hours):** UI.
- Free-tier locked card (~30 min, copy CI pattern)
- Pro idle card with diversity_focus dropdown (~30 min)
- Loading state (~15 min)
- Success state — 11 collapsible sections — this is the bulk of the time (~3 hours)
- Error state (~15 min)
- Wire into `grand.innerHTML` array (~15 min)

**Day 3 (~3 hours):** Smoke + deploy.
- Real-AI smoke script (port CI's smoke script with Jamboard schema)
- `vercel --yes --prod` deploy
- Manual QA against 3-5 real reqs (Skydio + DataAnnotation + AUSGAR + 2 others)
- Iterate prompt if quality issues surface

**Total: ~11 hours, 3 sessions.** Slightly longer than CI because the UI has 11 sections to render vs CI's per-company loop.

---

## What this unlocks

Once shipped, the Pro tier offers FOUR genuine intelligence layers per requisition:

1. **JD Parser + Boolean Builder** (free)
2. **Pro Skill Briefing** (Pro)
3. **Pro Boolean Extensions** (Pro)
4. **Competitive Intelligence** (Pro, just shipped)
5. **Sourcing Jamboard** (Pro, this plan)

That's a stack that genuinely justifies the Pro price. A recruiter looking at this from the outside would see real, distinct value at each tier.

It also positions the next two integrations cleanly:
- **DEI Strategy** (currently planned as separate integration) might just become a deeper version of Jamboard's `dei_strategies` section — worth deciding before building DEI standalone.
- **Silver Medalist Rediscovery** is unrelated to Jamboard but uses the same Pro card pattern.

---

## Open questions to resolve before Day 1

1. **DEI Strategy overlap.** The v2 roadmap planned DEI Strategy as a separate Pro feature integration. Jamboard's `dei_strategies` section overlaps with that. Do we:
   - **Option A:** Ship Jamboard with DEI included, skip the standalone DEI Strategy integration entirely.
   - **Option B:** Ship Jamboard without DEI (set field to null), build DEI Strategy as separate Pro tab that goes deeper.
   - **Option C:** Ship Jamboard with light DEI, build DEI Strategy as separate Pro deep-dive.

   **Recommendation:** Option A. Less surface area, less customer confusion. If a customer needs deeper DEI work, that's a sign to build a richer feature later. Don't fragment the product into 7 Pro tabs that all kind of do the same thing.

2. **Boolean strings overlap.** Jamboard's `creative_boolean_strings` overlaps with our existing Boolean Builder + Pro Boolean Extensions. Do we:
   - **Option A:** Drop `creative_boolean_strings` from Jamboard output (Boolean Builder handles it).
   - **Option B:** Generate "creative" booleans that are different from BB's standard 5 tiers (e.g., GitHub-search-only strings, or watering-hole-x-rays).

   **Recommendation:** Option A. We already have a 5-tier boolean system. Generating a 6th set of similar booleans is noise.

3. **Cache invalidation.** If we cache Jamboard output to `requisitions.jamboard_json`, when do we invalidate? On JD edit? Never (always show cached, force regenerate)? Ttl-based?

   **Recommendation:** Show cached forever, give the recruiter a "Regenerate" button. Same pattern as the CI free re-runs in Pro.

4. **Loading message tone.** CI says "Analyzing competitors..." This should say "Building your sourcing intelligence..." or similar — but what's the right voice? Worth asking Jason at Day 2 design time.

5. **diversity_focus values.** Should we keep CandidatIQ's 7 options exactly, or tailor? Their list has "Career Switchers" as a diversity option, which is debatable.

   **Recommendation:** Drop "Career Switchers" from the diversity dropdown — it's not a protected class — and keep the other 6. Career switchers are still surfaced in the dedicated `career_switchers` section regardless of dropdown selection.

6. **Per-section AI quality bar.** Some sections (timing_strategies, passive_candidate_triggers) might produce bland generic output. If smoke testing reveals this, do we:
   - Tighten the prompt to demand specifics
   - Drop the section from the contract
   - Generate it deterministically from the role_title

   **Defer to Day 3 manual QA.**

---

## What's NOT in this plan (deliberately)

- Prompt full text (Day 1 task)
- UI component code (Day 2 task)
- Smoke script (Day 3 task)
- pytest infra (still saying no)
- A "Jamboard standalone page" (`/app/jamboard.html`) — Jamboard is a tab on the requisition results, not a separate page. CandidatIQ shipped a standalone page; we don't need to.

---

## Diff vs the CI integration plan (what's different)

| | CI plan | Jamboard plan |
|---|---|---|
| Source on disk shape | Static FAANG lookup | Static role-keyword lookup |
| AI in original? | No | No |
| Existing parsed_json fields reused | None | 4 (alt_titles, watering_holes, market360, transferable_skill_clusters) |
| Output sections | Per-company loop (variable) | Fixed 11 sections |
| Cache strategy | None (every click re-fires AI) | Persist to existing `jamboard_json` column |
| UI complexity | Per-company card grid | 11-section collapsible accordion |
| Deterministic helper needed? | Yes (boolean templating) | No (purely AI) |
| Days to ship | 3 | 3 |
| Hours estimate | ~10 | ~11 |
| Cost per call | $0.05-0.10 | $0.04-0.08 |

**Net: this is faster and cheaper than CI was.** More existing infrastructure to leverage, simpler architecture, no helper to debug.

---

## Build trigger

This plan is ready to execute. No external prerequisites (unlike Agentic Candidate Discovery which needs API keys).

Day 1 starts when:
- Jason approves the plan
- Open questions 1, 2, 5 above are answered (DEI overlap, boolean overlap, dropdown options)
- The other open questions can be deferred to Day 2/3

---

*Plan complete. Status: PAUSED for Jason review. Day 1 begins on his approval.*

*Companion docs:*
*- `2026-04-28-talent-os-flywheel-roadmap-v2.md` — strategic context*
*- `2026-04-28-competitive-intelligence-integration-plan.md` — sibling, just shipped*
*- `2026-04-28-agentic-candidate-discovery-integration-plan.md` — sibling, tabled*
*- `2026-04-28-location-intelligence-future-feature.md` — sibling, parked*
