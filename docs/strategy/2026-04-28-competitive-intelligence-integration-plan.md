# Competitive Intelligence Integration Plan

**Author:** Jason Shotwell
**Date:** 2026-04-28
**Status:** Planning doc — no code yet
**Companion:** `2026-04-28-talent-os-flywheel-roadmap-v2.md` (this is Days 1-30, Week 1 of v2 sequencing)

---

## TL;DR — what changed during research

The v2 roadmap claimed Competitive Intelligence was a "9/10 prompt sitting on disk ready to integrate." After reading the actual source, that framing was wrong and needs correction:

- **There is no AI prompt.** The CandidatIQ implementation is a hard-coded Python lookup table for 10 FAANG-tier companies (Meta, Google, Amazon, Microsoft, Apple, Netflix, Stripe, Airbnb, Uber, Salesforce) plus a boolean-string templating function.
- **The lookup approach won't work for SourcingNav.** Real Boolean Builder output produces companies like AUSGAR Tech, Leidos, Anduril, SeeScan, DataAnnotation, Heron Systems. ~95% of real searches would hit zero matches in a 10-company lookup.
- **What IS portable:** the structural shape (per-company analysis with hiring velocity, salary intel, remote policy, common skills, benefits, growth stage), the JSON contract the frontend expects, and the boolean-templating function that works against arbitrary company names.

**Revised integration approach:** build a real AI-driven Competitive Intelligence endpoint that takes Boolean Builder's company clusters as input and produces per-company intelligence using an LLM call. Reuse the JSON shape from CandidatIQ (so the frontend pattern is reusable), reuse the boolean-templating logic (which is sound), but throw away the static lookup and replace it with a structured AI prompt.

This is a 2-3 day build, not a port.

---

## What was read and where

| File | Purpose | Key finding |
|---|---|---|
| `~/Desktop/untitled folder/candidatIQ/frontend/components/CompetitiveIntelligence.tsx` (356 lines) | Frontend React component | Calls 4 endpoints. Defines the JSON contract via TypeScript `CompanyInsight` interface. |
| `~/Desktop/untitled folder/candidatIQ/app/routes/competitive_intelligence.py` (567 lines) | Backend FastAPI router | Hard-coded 10-company lookup. 5 endpoints. NO AI calls anywhere. |
| `~/Desktop/untitled folder/candidatIQ/app/routes/intelligence_engine.py` (817 lines) | Different file the v2 doc pointed to | Three unrelated tools (Look-Alike, Talent Graph, Comp Simulator). NOT Competitive Intelligence. The v2 doc was wrong about which file held it. |

**Footnote for future-Jason:** the v2 roadmap's asset inventory needs a correction. `intelligence_engine.py` and `competitive_intelligence.py` are different files holding different features. The v2 doc conflated them.

---

## What the existing CandidatIQ implementation actually does

### Frontend (CompetitiveIntelligence.tsx)

**State:**
- `selectedCompanies: string[]` — defaults to `['Meta', 'Google', 'Amazon']`
- `roleTitle: string` — defaults to `'Senior Software Engineer'`
- `insights: CompanyInsight[]` — populated by analyze button
- `marketSummary, salaryComparison, marketTrends` — auto-loaded on mount

**The core JSON contract (CompanyInsight):**
```typescript
{
  company: string;
  hiring_velocity: 'aggressive' | 'moderate' | 'slow';
  open_positions: number;
  avg_time_to_fill: string;          // "45 days"
  common_skills: string[];           // top 5
  salary_range: { min, max, equity };
  benefits_highlights: string[];
  remote_policy: string;             // "Hybrid (3 days office)"
  growth_stage: string;              // "Mature" | "Growth" etc
  company_size: number;
  engineering_percent: number;       // 0.35 = 35% engineers
}
```

**4 backend endpoints called:**
1. `GET /api/competitive-intel/companies` — list available companies (the 10 hard-coded)
2. `GET /api/competitive-intel/market-trends/{role_title}` — aggregated trend data
3. `GET /api/competitive-intel/salary-comparison/{role_level}` — sorted salary list
4. `POST /api/competitive-intel/analyze-competitors` — main analysis call (companies + role -> per-company insights)

A 5th endpoint `POST /api/competitive-intel/generate-competitive-strategies` exists but isn't called from this TSX. It's used by an autonomous agent flow elsewhere.

### Backend (competitive_intelligence.py)

**No LLM calls.** Pure dict lookup.

**The COMPANY_DATA dict structure (truncated example):**
```python
'Meta': {
    'size': 86482,
    'engineering_percent': 0.35,
    'hiring_velocity': 'moderate',
    'avg_open_positions': 1200,
    'avg_time_to_fill': '45 days',
    'remote_policy': 'Hybrid (3 days office)',
    'growth_stage': 'Mature',
    'benefits': ['Equity/RSUs', '$25K relocation', 'Free meals', '6 months parental leave'],
    'top_skills': ['React', 'Python', 'System Design', 'ML/AI', 'Distributed Systems'],
    'salary_ranges': {
        'software_engineer': {'min': 180000, 'max': 350000, 'equity': '50-200K/yr'},
        'senior_engineer':   {'min': 250000, 'max': 500000, 'equity': '100-400K/yr'},
        'staff_engineer':    {'min': 400000, 'max': 900000, 'equity': '200-800K/yr'}
    }
}
```

**The boolean-templating function (from `/generate-competitive-strategies`):**

This part is genuinely useful. Given a competitor name + role title + skills, it generates:

- **Macro X-ray** — wide net "ex-Company" + role/skills + seniority filter
- **Micro X-ray #1** — exact title + company + JD skills + seniority
- **Micro X-ray #2** — adjacent titles + company + JD skills
- **Standard X-ray** — title or adjacent + company + all JD skills
- **GitHub strategy** — company + programming languages from JD + followers:>50

It uses real role-title parsing (detects senior/staff/principal/manager) and only includes GitHub language filters if the JD actually contains programming languages. This logic is sound and worth porting.

**What's NOT useful:** the salary tiers and benefits info are static FAANG data. They don't apply to defense, biotech, climate tech, or 99% of the companies that show up in real Boolean Builder output.

---

## What the SourcingNav side already produces

Per `api/index.py:1133`, the Boolean Builder output already includes:

```json
"company_clusters": {
  "tier_1_direct_competitors": ["Company1", "Company2", "Company3"],
  "tier_2_adjacent": ["Company4", "Company5", "Company6"]
}
```

These get extracted into the signature graph (`api/index.py:3290`):

```python
company_clusters = booleans.get("company_clusters") or {}
# tier1_count, tier2_count get logged as factors
# tier_1_direct_competitors, tier_2_adjacent get stored as factors
```

**This is the clean handoff point.** Boolean Builder produces 6-12 real company names per requisition. Competitive Intelligence consumes them.

Additionally, the JD parser produces:
- `role_title` (the requisition's exact title)
- `must_have_skills` (the JD's skill list, ranked)
- `industry`, `level`, `location`, `remote_policy`
- `base_range_min/max` (the hiring company's offered comp)

All of this is stored in the `jd_signatures` table and is available at the moment Competitive Intelligence would run.

---

## The integration plan

### Endpoint design

**New endpoint:** `POST /api/intake/competitive-intel`

**Auth:** Bearer token, Pro tier only (server-side gate before any AI call, same pattern as Pro Skill Briefing).

**Request shape:**
```json
{
  "req_id": "uuid-of-existing-requisition",
  "competitors": ["explicit_list_optional"]   // optional override
}
```

If `competitors` is omitted, the endpoint pulls `tier_1_direct_competitors + tier_2_adjacent` from the requisition's stored signature. This is the auto-chain case — the recruiter doesn't pick competitors, the system uses what Boolean Builder already produced.

If `competitors` is provided, use that list (max 8 to control cost).

**Response shape (mirrors CandidatIQ contract for frontend reusability):**
```json
{
  "req_id": "...",
  "role_title": "...",
  "competitors_analyzed": ["..."],
  "insights": [
    {
      "company": "AUSGAR Technologies",
      "hiring_velocity": "moderate",
      "estimated_engineering_count": "150-300",
      "avg_time_to_fill": "45-60 days (estimated)",
      "common_skills": ["C++", "Embedded Linux", "RTOS", "SECRET clearance"],
      "salary_range": {
        "min": 130000,
        "max": 180000,
        "equity": "Limited (defense contractor)",
        "confidence": "low — derived from public salary data"
      },
      "benefits_highlights": ["Clearance sponsorship", "Government contracting stability"],
      "remote_policy": "On-site (cleared facility)",
      "growth_stage": "Mature",
      "talent_pool_estimate": "Small — cleared embedded engineers in San Diego",
      "poaching_difficulty": "high",
      "poaching_rationale": "Cleared engineers are sticky due to clearance maintenance and few alternatives in region",
      "boolean_strategies": {
        "macro": "site:linkedin.com/in/ ...",
        "micro_1": "...",
        "micro_2": "...",
        "github": "...",
        "xray": "..."
      },
      "mentor_tip": "..."
    },
    ...
  ],
  "market_summary": {
    "total_estimated_talent_pool": "...",
    "competitive_intensity": "high|moderate|low",
    "fastest_to_fill": "...",
    "most_aggressive_hirer": "...",
    "comp_benchmark_vs_jd": "JD pays at 60th percentile of cluster"
  },
  "generated_at": "ISO timestamp",
  "ai_model": "claude-haiku-4-5",
  "honesty_caveat": "Estimates derived from public hiring patterns. Confidence varies by company specificity."
}
```

### How the AI call works (the "real prompt" part)

Single LLM call. Inputs: full company list, role title, must-have skills from JD, JD's offered comp range, location, remote policy.

Prompt skeleton:

```
You are analyzing competitor companies for a recruiter filling a specific role.

REQUISITION:
- Role: {role_title} ({level})
- Industry: {industry}
- Location: {location}, {remote_policy}
- Required skills: {must_have_skills}
- Offered comp: ${base_min}-${base_max}

COMPETITORS TO ANALYZE:
{tier_1_direct_competitors} (tier 1, direct competition)
{tier_2_adjacent} (tier 2, adjacent industries)

For EACH company, produce structured JSON with:
- hiring_velocity (aggressive/moderate/slow) — based on what you know about
  their public hiring trajectory; if unknown, default to "moderate" and flag it
- estimated_engineering_count — RANGE not exact. "150-300" not "247"
- avg_time_to_fill — estimated range, with caveat
- common_skills — what engineers AT THIS COMPANY typically have on their resume
  for THIS specific role (intersect company tech stack with role requirements)
- salary_range — your best public-data estimate WITH confidence flag
  ("high" if FAANG-tier with public levels.fyi data, "low" otherwise)
- benefits_highlights — known cultural benefits (clearance sponsorship,
  equity model, parental leave norms for company size/stage)
- remote_policy — public RTO stance
- growth_stage — Mature / Growth / Early
- talent_pool_estimate — qualitative ("small/medium/large pool of qualified
  engineers given the role + location")
- poaching_difficulty — high/moderate/low with rationale
- poaching_rationale — 1-2 sentences

HONESTY RULES:
- If you don't know something specific about a company, say so in the
  confidence field. Do NOT fabricate hiring velocity numbers or salary
  ranges. Defense contractors, niche startups, and obscure companies
  should get LOW confidence flags, not made-up numbers.
- Salary ranges for non-FAANG must include "confidence": "low" unless
  you have specific public data.
- For the requisition's industry, weight intelligence accordingly:
  defense → clearance/contract stability matter more than equity;
  startup → equity and burn rate matter more than tenure.

Return JSON only. No prose.
```

Then the boolean-templating function (ported from CandidatIQ but stripped of static dependencies) generates the boolean strategies for each company name. That's deterministic, no AI needed.

### What's reused vs new

| Component | Source | Action |
|---|---|---|
| TypeScript JSON contract | CandidatIQ TSX | Reuse the field names (frontend reusability) |
| Boolean-templating function | CandidatIQ Python | Port the logic, strip the `COMPANY_DATA` dependency. Make it pure: `(company_name, role_title, jd_skills) -> boolean_strategies dict` |
| Static company lookup | CandidatIQ Python | **Throw away.** Replace with AI call. |
| Salary tiers static data | CandidatIQ Python | **Throw away.** Let the AI estimate with confidence flags. |
| Market summary aggregation | CandidatIQ Python | Adapt — replace static aggregation with AI-derived comparative summary in same call |

### Where it lives in `api/index.py`

Three additions:

1. **`COMPETITIVE_INTEL_PROMPT` constant** near the existing `PRO_INTAKE_PROMPT` and `PRO_BOOLEAN_PROMPT` constants (around line 850, where the current prompts live). Keeps prompts together for maintenance.

2. **Boolean-templating helper function** `_generate_competitive_boolean_strategies(company_name, role_title, jd_skills, level)` near the existing intake helpers. Pure function, no AI.

3. **Endpoint handler** `@app.post("/api/intake/competitive-intel")` near the existing `/api/intake` endpoint. Pattern:
   ```
   - Auth check (Bearer token)
   - Pro tier gate (return 402 if not Pro)
   - Cap check via existing check_cap()
   - Load req from jd_signatures table
   - Extract competitors from tier_1 + tier_2 (or use override)
   - Call AI via existing call_ai() with COMPETITIVE_INTEL_PROMPT
   - For each insight in AI response, attach boolean strategies via helper
   - Return assembled JSON
   ```

### UI surface decision

**Option A: Expanded results card on intake page** — add a "Competitive Intelligence" tab to the existing requisition results view (next to Boolean Builder, Skill Briefing). Pro users see content; Free users see locked placeholder.

**Option B: Standalone tab on requisition detail page (`/app/req/{id}`)** — separate page, deeper UI, more room for company comparison tables.

**Recommendation: Option A first.** Keeps everything in the intake flow where the auto-chain is most natural. Recruiter pastes JD → sees Boolean Builder output → clicks "Run Competitive Intelligence" → in-place expansion shows per-company analysis. Option B can come later if customers request a dedicated competitive page.

**Why not auto-run during intake?** Because it's a separate AI call ($0.01-0.05) and takes 5-10 seconds. Better to make it explicit so the recruiter chooses to spend the time/cost on it. Lazy-load on click.

### Tier gating

**Free tier:** Sees a locked placeholder card titled "Competitive Intelligence" with `█-block` content showing 2 sample company rows + an upgrade CTA. Same pattern as Pro Skill Briefing.

**Pro tier:** Full functionality. Cap shared with other Pro features (existing `check_cap()` semantics).

---

## Schema changes

**None required for MVP.**

The output is computed on-demand from existing `jd_signatures` data + AI call. We do NOT cache competitive intel results in MVP. Reasoning:

- The output is recruiter-facing only, not feeding the calibration moat
- Competitor data goes stale fast (hiring velocity changes, comp shifts); caching adds invalidation logic with little benefit
- AI cost per run is acceptable (~$0.05 with Haiku 4.5 at 8 companies)

**Phase 2 (post-launch, if usage justifies):** add `competitive_intel_runs` table with columns `id, req_id, generated_at, model, response_json, cost_usd`. Then we can show "last analyzed 3 days ago, refresh?" UX.

---

## Tests that prove it works

1. **Empty competitors fallback test** — req has no `tier_1_direct_competitors` populated → endpoint returns 422 with helpful error, not a 500 crash.
2. **Competitor list cap test** — request with 15 competitors → endpoint analyzes max 8, returns warning in response.
3. **Pro gate test** — free user hits endpoint → returns 402, AI call never fires.
4. **Cap exhaustion test** — Pro user at cap → returns 429, AI call never fires.
5. **AI failover test** — Anthropic key invalid → endpoint returns 503 with structured error (no fallback to Together; same as other Pro endpoints since Together was killed in commit `91a3ea5`).
6. **Real-data E2E test** — run against existing req `e346d266-5681-432f-937b-9d6c7d242d04` (Leidos demo data). Verify the AI returns sensible output for defense contractor competitors. Manually inspect honesty flags.
7. **Boolean string sanity test** — for each insight, verify the 5 boolean strategies actually parse and don't contain syntax errors (no unmatched parens, no literal `AND` between terms per the X-ray constraints in `api/index.py:1145`).

Tests 1-5 are unit tests with mocked AI. Test 6 is a real-AI integration test. Test 7 is a deterministic check on the boolean-templating helper.

---

## Rollout sequencing

**Day 1:**
- Add `COMPETITIVE_INTEL_PROMPT` constant
- Port `_generate_competitive_boolean_strategies` helper from CandidatIQ
- Wire endpoint with Pro gate + cap check
- Smoke test against Leidos demo req

**Day 2:**
- Add UI surface (expanded card on intake results page)
- Add free-tier locked placeholder
- Add Pro upgrade CTA
- Manual QA against 5-10 real reqs in the corpus (defense, AI startup, FAANG, biotech, agency)
- Iterate on prompt based on output quality

**Day 3:**
- Add the 7 tests
- Deploy to production via `vercel --yes --prod`
- Update `app/index.html` to surface the new feature
- Add to homepage feature list at sourcingnav.com root

**Day 4 (optional):** announce on LinkedIn with a real-output screenshot.

---

## What this unlocks

Once shipped, the requisition flow is:

1. Recruiter pastes JD → JD Parser + Boolean Builder run (existing, free + Pro)
2. Recruiter sees company clusters (existing) + can click "Run Competitive Intelligence"
3. AI analyzes each cluster company → produces salary intel, hiring velocity, poaching difficulty, talent pool estimate, boolean strategies per company

The recruiter now has, in 30 seconds:
- A ranked list of who to source from (Boolean Builder)
- A breakdown of what each of those companies pays + their hiring stance (Competitive Intelligence)
- 5 ready-to-run boolean strings per company
- Mentor tips on poaching difficulty per company

This is what Gem charges $5K-25K/year for. SourcingNav delivers it for $49/mo.

---

## What this does NOT solve

- **Real-time hiring velocity.** The AI estimates based on training data + general patterns. For aggressive/moderate/slow accuracy, we'd need a hiring-signal feed (LinkedIn jobs scraping, Crunchbase funding data, news monitoring). That's Phase 5+.
- **Verified salary ranges for non-FAANG.** AI estimates with confidence flags. Real per-company comp data requires Levels.fyi API or similar, $$$ per company, not in MVP.
- **Calibration moat contribution.** Competitive Intelligence output isn't fed back into the signature graph. Recruiter accept/reject of which competitors to actually source from would feed the preference graph (per the v2 roadmap), but that's a separate workstream.

---

## Open questions to resolve before building

1. **Cap multiplier for Competitive Intelligence?** Existing Pro cap is per-intake. Does Competitive Intel count as 1 unit or 1 unit per N competitors analyzed? Suggest: 1 unit per run, regardless of competitor count.
2. **Honesty enforcement strictness?** Should the prompt refuse to estimate salary for companies it doesn't have public data on, or estimate-with-low-confidence? Current draft says estimate-with-low-confidence. Could be stricter.
3. **Free tier preview content?** Should free users see fake `█-block` content, or 1 real company analyzed (with the rest blocked)? The latter might be more compelling for upgrades.
4. **Auto-chain on intake or click-to-run?** Current plan says click-to-run for cost reasons. If we move to auto-chain, increase Pro cap accordingly.
5. **Telemetry?** Track click-through rate from intake → Competitive Intelligence. If <30% click, the feature isn't pulling its weight and should be auto-chained instead.

---

*Plan complete. Next concrete action: write the COMPETITIVE_INTEL_PROMPT constant and port the boolean-templating helper. Estimated 2-3 days end-to-end including UI and tests.*
