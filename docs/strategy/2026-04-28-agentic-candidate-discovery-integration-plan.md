# Agentic Candidate Discovery Integration Plan

**Author:** Jason Shotwell
**Date:** 2026-04-28
**Status:** Plan only — TABLED to an additional phase. NOT in the 90-day plan.
**Predecessor:** Reverse Gem architectural commitment in `2026-04-28-talent-os-flywheel-roadmap-v2.md`
**Trigger to start building:** API access procured to GitHub + Apollo.io (Jason's stated prerequisite)

---

## TL;DR

Today the Boolean Builder hands the recruiter a query string. The recruiter then has to manually run that query on Google, read profiles, and copy data into a tracker. **Agentic Candidate Discovery closes that loop**: the system actually FETCHES candidates from public sources using the boolean strategies as inputs, runs them through fit scoring, and hands the recruiter a pre-built ranked list of names to evaluate.

This is the v2 roadmap's "Reverse Gem" principle made into a shippable product. Booleans become INPUTS to a discovery pipeline, not the final deliverable.

**Why this is a different product, not just a feature.** CI tells the recruiter who to source from. Boolean Builder tells them HOW to query. Neither produces actual candidate names. The shift from "here's a query" to "here's 50 ranked candidates with public-domain evidence and contact paths" is the moment SourcingNav becomes the system recruiters cannot work without.

**Why we're not building it now.** Three reasons:
1. CI just shipped (April 28). Real customers haven't used it yet. Premature to optimize the next product before learning from this one.
2. API access (GitHub authenticated tier + Apollo.io paid tier) is a hard prerequisite Jason called out explicitly. No prereq, no build.
3. Stage 3 Match (Days 31-60 in v2) has higher flywheel-multiplier value because it generates the preference graph that makes ALL features smarter, including this one.

This plan exists so when API access is procured and the build trigger fires, the architecture is already specified.

---

## What's already in place that this would build on

| Already shipped | Used as | What it provides |
|---|---|---|
| JD Parser (8.5/10) | Source of role context | role_title, must_have_skills (atomic, post-Apr-28-fix), level, location |
| Boolean Builder (9/10) | Source of search universe definition | tier_1_direct_competitors, tier_2_adjacent, watering holes, GitHub language list |
| Competitive Intelligence (just shipped) | Source of per-company poaching strategy | 5 boolean strategies per competitor, recruiting angle, poaching difficulty |
| Atomic skills extractor (`_extract_atomic_skills`) | Skill-cluster matching | 1-3 word skill tokens that can be matched against profile signals |
| jd_signatures table | Persistent role context | Pre-computed signature + canonical_skills available for batch fetches |

**Important:** the existing Pipeline page (`app/pipeline.html`) is where candidates would land. The data model already has a `candidates` concept (per Day 1 audit which noticed `CANDIDATE_EVAL_PROMPT` and `/api/source/evaluate` were started but not finished). This feature is the upstream that populates that pipeline.

---

## What "agentic candidate discovery" actually does

### The user-facing flow

1. Recruiter completes intake. Boolean Builder produces tier_1 + tier_2 companies. CI runs (optional, Pro). Pipeline view is empty.
2. New button on the requisition: **"Discover candidates"** (Pro Agency tier only — see tier gating below).
3. Click fires an async job. Job runs in background, recruiter gets a progress indicator: "Searching GitHub: 3 of 12 companies done. Estimated 4 min remaining."
4. As candidates are discovered, they populate the Pipeline view in real time. Each candidate row shows:
   - Name (or "GitHub: <username>" if real name not available)
   - Source signals (GitHub stars on relevant repos, papers, patents, conference talks)
   - Last public activity date (filters out abandoned profiles)
   - Auto-computed fit score (using existing taxonomy match + AI Fit Analyzer when ready)
   - Contact path (Apollo email if available, GitHub profile URL fallback)
5. Recruiter triages: keeps qualified candidates, marks rejections (which feeds the preference graph from v2 roadmap).
6. For kept candidates, recruiter triggers outreach (Stage 2 feature, future).

### What the system fetches

Per requisition, the discovery agent runs candidate fetches against:

**Source 1: GitHub API (authenticated, ~5K req/hr)**
- For each tier_1 + tier_2 company name: search users with that company in their profile
- Filter by language match (using JD's programming languages from atomic skills)
- Filter by activity (commits in last 12 months)
- Filter by signal threshold (followers > 50, OR has starred repo with > 1K stars in relevant language)
- Pull per-user: top 5 repos, language breakdown, stars on each, README excerpts mentioning relevant skills

**Source 2: Apollo.io API (paid tier)**
- For each user found on GitHub: lookup by `(name, current_company, role_title_pattern)` to find email + LinkedIn URL
- For competitors where GitHub yields zero results (defense contractors with no public code): direct Apollo company search filtered by role_title pattern + skill keywords in description
- Pull per-result: name, current_title, company_history, email_status, LinkedIn URL

**Source 3 (later phases): USPTO Patents, Google Scholar, Conference Speakers**
- Phase 1 ships GitHub + Apollo only. The other sources are documented for future expansion but not in v0 scope.

### What the system does NOT do (out of scope)

- **Send outreach.** That's Stage 2 Engage. This feature stops at "here's the candidate, here's the contact path."
- **Scrape LinkedIn directly.** Apollo provides LinkedIn URL discovery within their licensed data; SourcingNav never crawls LinkedIn pages. This is the legal/trust frame from the Reverse Gem commitment.
- **Persist candidate data forever.** Per-search universes are ephemeral by default — see "Privacy + Legal" section.
- **Make hiring decisions.** Fit scores are advisory. Recruiter retains decision authority. (This is also EU AI Act Article 14 compliance: human oversight on high-impact decisions.)

---

## Architecture

### Sync vs async fetching

**Async, no question.** A single requisition generates 5-12 competitor companies. Each company requires 1-3 GitHub API calls plus 1-N Apollo lookups. Realistic per-req fetch time: 3-10 minutes. Cannot block the intake UI.

**Implementation pattern:**
- New `candidate_discovery_jobs` table with status (queued → running → completed → failed)
- Background worker polls queued jobs and processes them
- Frontend polls `/api/discovery/job/{job_id}/status` every 5 seconds for progress
- As candidates are fetched, they're inserted into a `candidates` table linked to req_id + job_id
- Frontend re-fetches the candidate list each poll, sees new rows appear

**Background worker hosting decision:** Vercel functions have a 60-second timeout on Hobby and 300s on Pro. Per-job work exceeds that. Two options:
- Option A: Use Vercel cron + chunked job processing. Each cron invocation processes one company-fetch chunk, marks progress, exits. Job completes over 5-10 cron ticks. Cheap, no new infra.
- Option B: Spin up a separate Render/Railway worker. Higher infra cost, simpler code.

**Recommend Option A for v0.** Keeps the infra single-vendor (Vercel) and avoids adding a new service to monitor. Migrate to Option B if usage scales.

### Per-search universe vs cached aggregation

**Per-search universe.** This is the Reverse Gem architectural commitment. We don't build a global candidate index. For each requisition, we run targeted fetches against the specific companies + skill cluster the JD calls for.

**Why this matters:**
- Legal: per the v2 roadmap's hiQ Labs analysis, public-API-based discovery is in clean legal territory. Building a global crawl is not.
- Cost: a global GitHub crawl is unlawful per their ToS and would be ~$50K/mo infrastructure. Per-search fetches are ~$0.50-2.00 per req at moderate use.
- Quality: targeted curation beats keyword spray. A defense embedded engineer search done well returns 30 highly-relevant profiles, not 5,000 noisy matches.
- Compliance: every candidate score has explainable evidence per criterion (which repos, which papers). EU AI Act Article 14 friendly.

### Storage: ephemeral by default, persistent on opt-in

**The privacy decision that drives everything else.** GDPR and CCPA both treat "data the recruiter never asked the candidate for" as personal data subject to lawful-basis requirements.

**v0 default behavior:**
- Discovered candidates persist in DB for **30 days post-discovery**, then auto-purge unless promoted
- Candidates are "promoted" by being added to the recruiter's pipeline (manual action)
- Promoted candidates persist indefinitely (legitimate-interest basis: active recruiting workflow)
- Rejected candidates are anonymized after 30 days (skill signature retained for calibration, name + contact stripped)

**This matches the v2 roadmap's Phase A retention decision:** active indefinite, closed 2yr then anonymize, placed 7yr. Discovery candidates are "applicant pre-stage" and get the shortest retention.

**Right-to-be-forgotten:** any candidate found via Apollo retains the Apollo source attribution. If the candidate emails sourcingnav.com requesting deletion, the workflow is: confirm identity, purge from DB, push deletion request to Apollo (Apollo handles their own propagation). Deletion endpoint is a future build, but the data model supports it from day one.

### The candidate fetch pipeline

```
+-------------------+
| Requisition       |
| (already in DB)   |
+--------+----------+
         |
         v
+--------+--------+
| Discovery job   |
| created (status: |
| queued)         |
+--------+--------+
         |
         v
+--------+--------+
| For each tier 1 +|
| tier 2 company: |
+--------+--------+
         |
         +-----> GitHub fetch (per company)
         |       |
         |       +--> filter by language match
         |       +--> filter by activity
         |       +--> filter by signal threshold
         |       +--> emit github_candidates[]
         |
         +-----> Apollo fetch (per company)
         |       |
         |       +--> filter by role_title pattern
         |       +--> filter by current_company match
         |       +--> emit apollo_candidates[]
         |
         v
+--------+--------+
| Dedup / merge   |
| (same person on |
| multiple sources)|
+--------+--------+
         |
         v
+--------+--------+
| Fit scoring     |
| (taxonomy + AI) |
+--------+--------+
         |
         v
+--------+--------+
| Insert into     |
| candidates table|
| linked to req_id|
+-----------------+
```

### The dedup problem

A senior embedded engineer at L3Harris might appear in:
- GitHub (as a contributor to an open-source firmware project)
- Apollo (as their L3Harris employee record)
- Conference speaker list (if they spoke at Embedded World 2024)

We need to merge these into ONE candidate row. The matching key is:
- High confidence: same name + same current company
- Medium confidence: same name + GitHub bio mentions company
- Low confidence: same name + role_title pattern match (defer to recruiter)

**v0: implement high + medium confidence only.** Low-confidence matches surface as separate rows with a "may be the same person" hint. Manual merge in the UI.

---

## Endpoint design

**New endpoints:**

```
POST /api/discovery/start
  Auth: Pro Agency tier required (separate gate from Pro)
  Body: { req_id: string, sources?: ["github", "apollo"], max_candidates?: number }
  Returns: { job_id, estimated_completion_seconds }
  Side effects:
    - Inserts row into candidate_discovery_jobs table
    - Returns immediately; actual work happens in background worker

GET /api/discovery/job/{job_id}/status
  Auth: Pro Agency tier
  Returns: {
    status: "queued" | "running" | "completed" | "failed",
    progress: { companies_done, companies_total, candidates_found },
    eta_seconds,
    error_message?
  }

GET /api/discovery/job/{job_id}/candidates
  Auth: Pro Agency tier
  Query params: limit, offset, min_fit_score, sources_filter
  Returns: { candidates: [...], total_count, page_token? }

POST /api/discovery/candidate/{candidate_id}/promote
  Auth: Pro Agency tier
  Side effects:
    - Adds candidate to recruiter's pipeline for this req
    - Flags as "promoted" (exempts from 30-day auto-purge)
    - Logs event for preference-graph training

POST /api/discovery/candidate/{candidate_id}/reject
  Auth: Pro Agency tier
  Body: { reason: string }
  Side effects:
    - Marks candidate as rejected
    - Logs (candidate_features, role_features, "rejected", reason) for preference graph
    - Triggers anonymization countdown (30 days)
```

**Cron endpoint (internal, not user-facing):**
```
POST /api/internal/discovery/process-queue
  Auth: cron secret header
  Behavior: process up to 1 chunk of work per invocation, mark progress, exit
  Triggered every minute by Vercel cron
```

---

## Schema changes

```sql
CREATE TABLE candidate_discovery_jobs (
  id TEXT PRIMARY KEY,
  req_id TEXT NOT NULL REFERENCES requisitions(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  org_id TEXT NOT NULL REFERENCES orgs(id),
  status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
  sources_json TEXT,                    -- which sources were requested
  max_candidates INTEGER DEFAULT 50,
  candidates_found INTEGER DEFAULT 0,
  companies_total INTEGER,
  companies_done INTEGER DEFAULT 0,
  current_company TEXT,                 -- for progress display
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  failed_at TIMESTAMP,
  error_message TEXT,
  cost_usd REAL DEFAULT 0,              -- accumulated API cost
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_discovery_jobs_status ON candidate_discovery_jobs(status, created_at);
CREATE INDEX idx_discovery_jobs_req ON candidate_discovery_jobs(req_id);

CREATE TABLE candidates (
  id TEXT PRIMARY KEY,
  req_id TEXT REFERENCES requisitions(id),
  job_id TEXT REFERENCES candidate_discovery_jobs(id),
  user_id TEXT NOT NULL,
  org_id TEXT NOT NULL,
  -- Identity
  full_name TEXT,
  github_username TEXT,
  linkedin_url TEXT,
  email TEXT,
  email_confidence TEXT,                -- 'verified' | 'guess' | 'unknown'
  -- Current state
  current_company TEXT,
  current_title TEXT,
  current_location TEXT,
  -- Source signals (JSON for flexibility)
  github_signals_json TEXT,             -- { stars, repos[], languages[], commits_last_year }
  apollo_signals_json TEXT,             -- { previous_companies[], tenure_years, last_updated }
  patent_signals_json TEXT,             -- (future) { patent_count, top_patent_titles[] }
  scholar_signals_json TEXT,            -- (future) { paper_count, h_index, top_papers[] }
  -- Fit
  fit_score INTEGER,                    -- 0-100, computed by taxonomy + AI
  fit_evidence_json TEXT,               -- which signals justified the score
  -- Lifecycle
  status TEXT DEFAULT 'discovered',     -- 'discovered' | 'promoted' | 'rejected' | 'contacted'
  rejection_reason TEXT,
  promoted_at TIMESTAMP,
  rejected_at TIMESTAMP,
  -- Privacy / retention
  data_source TEXT NOT NULL,            -- 'github' | 'apollo' | 'merged' (for source attribution)
  retention_until TIMESTAMP,            -- 30 days from discovery unless promoted
  anonymized_at TIMESTAMP,              -- set when name + contact stripped
  -- Preference graph hooks (for v2 roadmap calibration)
  recruiter_decision TEXT,              -- mirrors status but for explicit pref-graph events
  decision_reason TEXT,
  decision_at TIMESTAMP,
  -- Audit
  discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE INDEX idx_candidates_req ON candidates(req_id, fit_score DESC);
CREATE INDEX idx_candidates_status ON candidates(user_id, status);
CREATE INDEX idx_candidates_retention ON candidates(retention_until)
  WHERE status = 'discovered' AND anonymized_at IS NULL;
```

The `idx_candidates_retention` partial index is what powers the nightly purge job.

---

## Tier gating + pricing

**The pricing reality.** Apollo.io's API is paid per credit. GitHub's authenticated API is free up to 5,000 req/hr but rate limits hit fast at scale. This feature has real marginal cost per recruiter use.

**Proposed tier structure:**

| Tier | Discovery access | Notes |
|---|---|---|
| Free | Locked, "available on Pro Agency $300/mo" | Same locked-card UX as CI |
| Pro Solo ($49/mo) | Locked. Margin doesn't support it. | Drives upgrade to Agency |
| Pro Agency ($300/mo) | 50 candidates per req, 10 reqs/mo | Hard cap. Overage = $0.50/candidate |
| Enterprise (TBD) | Unlimited + email enrichment + bulk | Custom contract |

**Why Pro Agency is the floor.** Apollo charges roughly $0.10-0.50 per matched record depending on tier. 50 candidates per req = $5-25 in API cost. 10 reqs/mo = $50-250 in API cost. At $300/mo subscription, margin is real but tight. Solo at $49/mo would be unprofitable.

**Cost monitoring is required from day 1.** The discovery_jobs table tracks `cost_usd` per job. Admin dashboard surfaces usage and alerts when a single org approaches the cap. Without this monitoring, a buggy retry loop or a viral user could eat API credits in hours.

---

## Privacy + Legal — non-negotiable

This is the section that turns "interesting feature" into "ship-able feature."

### What we will and won't do

| Will do | Won't do |
|---|---|
| Use GitHub's official API with authentication | Scrape GitHub HTML pages |
| Use Apollo's licensed data API | Scrape LinkedIn directly |
| Honor robots.txt on any web fetches (future patent/scholar sources) | Bypass authentication walls |
| Store source attribution per data point | Aggregate into a "global profile index" |
| Implement right-to-be-forgotten on candidate request | Sell candidate data to third parties |
| Auto-anonymize discovered (non-promoted) candidates after 30 days | Retain rejected candidates' personal data indefinitely |
| Provide explainable fit-score evidence per criterion | Make hiring decisions for the recruiter |

### Compliance mapping

- **EU AI Act Article 14 (human oversight):** fit scores are advisory. Recruiter must explicitly promote a candidate to take action. The system never auto-contacts or auto-decides.
- **EU AI Act Article 10 (data governance):** all training data for fit-scoring AI is documented (taxonomy + JD signatures + recruiter decisions). No third-party scraped data feeds AI training.
- **GDPR Article 6(1)(f) (legitimate interest):** active recruiting workflow is a recognized legitimate interest. We document this in our privacy policy.
- **GDPR Article 13/14 (transparency):** when a candidate is contacted via outreach (future Stage 2), the outreach must disclose how we found them.
- **CCPA:** California candidates can request data deletion. We implement the deletion API from day 1.
- **NYC Local Law 144:** if we're used for NYC-based hiring, the bias audit requirement applies. Our compliance is documented via the v2 roadmap's calibration data infrastructure.
- **hiQ v. LinkedIn (2022):** public scraping not a CFAA violation, but ToS violations are enforceable as breach of contract. We don't scrape LinkedIn — we use Apollo's licensed data, which keeps us out of this entirely.

### The dark pattern we explicitly avoid

A recruiting product that fetches candidate data and contacts them WITHOUT showing the recruiter the source attribution is a candidate-spam tool dressed up as AI. We don't ship that.

Every candidate in the discovery results shows:
- "Found via GitHub" / "Found via Apollo" attribution
- Last public activity date
- Why this candidate matched (which skills, which signals)
- Direct link to the public profile that justified the match

The recruiter sees what the system saw. No black box.

---

## Cost model — concrete numbers

### Per-requisition cost at v0 specs

Assume a typical req with 8 competitor companies (5 tier-1 + 3 tier-2):

| Source | Per-company calls | Total calls | Cost |
|---|---|---|---|
| GitHub user search | 1 | 8 | $0 (free tier) |
| GitHub user details | ~5 per company | 40 | $0 (free tier) |
| Apollo company search | 1 | 8 | $0.10 each = $0.80 |
| Apollo person enrichment | ~5 per result, 30 results target | 30 | $0.30 each = $9.00 |
| Fit scoring AI calls | 1 batch call | 1 | $0.05 |
| **Total per req** | — | — | **~$10.00** |

At Pro Agency $300/mo with 10 reqs/mo cap: $100/mo in API costs against $300/mo subscription = **~67% gross margin**. Tight but workable.

If a customer requests 20 reqs/mo (overage): 20 reqs × $10 = $200 in API costs. We charge $0.50/extra candidate × 50 × 10 = $250 overage = $50 net contribution.

### Rate limits to watch

- GitHub authenticated: 5,000 req/hr per token. With 8 companies × 6 calls = 48 calls per req. We can do ~100 reqs/hr per token. Fine for v0.
- Apollo: depends on plan tier. Their "Pro" plan does ~6,000 records/mo for $99. Their "Org" plan does 60K/mo for $499. We'd need Org tier from day one.

**Action item before v0 build:** confirm Apollo Org tier pricing at the time of build. Their pricing has changed twice in the last 18 months.

---

## Data flow for a single discovery job (concrete walkthrough)

Recruiter clicks "Discover candidates" on the Skydio firmware req we've been debugging:

```
1. POST /api/discovery/start { req_id: "a9783797..." }
   -> Inserts job row, returns job_id "disc_abc123"
   -> Frontend shows "Queued..."

2. Cron tick at minute 1: /api/internal/discovery/process-queue
   -> Picks up job disc_abc123
   -> Marks status='running', loads req signature
   -> Reads tier_1 = ['General Atomics', 'AeroVironment', 'Northrop Grumman',
                       'Raytheon', 'Textron']
   -> Reads tier_2 = ['Auterion', 'Skydio', 'L3Harris', 'Collins Aerospace',
                       'Elbit']  (note: Skydio is hiring company, must filter)
   -> Reads atomic skills = ['C/C++', 'RTOS', 'firmware', 'embedded systems', ...]
   -> Filters Skydio out of tier_2 (it's the hiring company per req.parsed.core.company)
   -> companies_to_search = 9 (5 tier_1 + 4 tier_2)
   -> Updates job: companies_total=9, current_company='General Atomics'
   -> Spends ~30 seconds on General Atomics:
      - GitHub search: users with "General Atomics" in profile + language:C++
        -> finds 12 users, filters by activity to 7
      - For top 7: pull repos, languages, commits
        -> emits 7 github_candidate records
      - Apollo search: company="General Atomics" + role contains
        ("firmware" OR "embedded") + level senior+
        -> finds 22 records, takes top 10
      - Apollo enrichment for those 10 records
        -> emits 10 apollo_candidate records
      - Dedup: 3 of the GitHub users matched Apollo records by name+company
        -> 7 + 10 - 3 = 14 unique candidates from General Atomics
   -> Updates job: companies_done=1, candidates_found=14
   -> Exits before 60s timeout

3. Cron tick at minute 2: continues with AeroVironment, ...

4. Frontend polls every 5s. Pipeline view starts populating in real time.

5. After ~9 cron ticks (9 minutes), all companies processed.
   -> Total candidates discovered: ~85 (after dedup across companies)
   -> Fit scoring runs on the batch (1 AI call): scores 0-100 per candidate
   -> Top 50 are persisted (max_candidates cap)
   -> Job status='completed'

6. Recruiter sees pipeline of 50 candidates, sorted by fit score.
   For each: name (or "GitHub: <handle>"), current company, top 3 source
   signals, fit score with evidence, action buttons (promote/reject).
```

Total time: 9-10 minutes. Cost: ~$10. Output: 50 ranked candidates with public-domain evidence.

That's the deliverable.

---

## Honest open questions to resolve before building

1. **Apollo specifically — is it the right partner?** Apollo's data quality varies wildly by industry. Defense contractors and niche startups are often missing entirely. Alternatives: Hunter.io (cheaper, lighter coverage), ContactOut (LinkedIn-scraping, legal grey area), ZoomInfo (enterprise-priced). Decision: probably Apollo for v0, but worth pricing the alternatives at build time.

2. **GitHub data quality for non-engineers.** This feature is great for software roles. Useless for sales, finance, ops, or design roles where engineers' GitHub presence isn't the right signal. Phase 1 should be **engineering-only** (gate by JD's role family). Phase 2 adds Apollo-only flow for non-engineering roles. Don't ship a feature that fails silently on roles outside the engineering ICP.

3. **Fit scoring approach.** Day-1 approach is taxonomy match (deterministic, fast) + a single AI batch call. Alternatives: per-candidate AI calls (costlier but better), no AI at all (just taxonomy + signals). Decision deferred to build time when we know the actual fit-quality bar customers expect.

4. **Cron-chunked workers vs dedicated worker.** Outlined as Option A above. If chunking proves fragile (state-management bugs, partial completion edge cases), migrate to Option B. Not a build-time blocker.

5. **Surface attribution UX.** Should the recruiter see WHICH boolean string matched WHICH candidates? Probably yes for transparency, but adds UI complexity. Phase 1 ships without; add if customers ask for it.

6. **Existing CANDIDATE_EVAL_PROMPT and /api/source/evaluate.** During Day 1 audit, we noticed these exist in the codebase already, partially built. Need to either complete and integrate them into the discovery pipeline, or replace them with discovery-specific scoring. Decision deferred to build time.

7. **Refresh / re-fetch behavior.** A discovered candidate found 3 weeks ago might have changed jobs. Re-running discovery on the same req should: detect duplicates, update changed records, surface NEW candidates. Adds complexity. Phase 2 problem.

8. **Multi-tenant rate limit pooling.** All Pro Agency users share the same GitHub token's 5K/hr limit. If 10 customers run discovery simultaneously, the 11th hits 429. Either: bring-your-own-token (BYOT), or maintain a pool of GitHub apps. BYOT was the wrong call for Together AI keys (per April fix); it might be wrong here too. Pool approach is more work but right.

---

## What MUST happen before we start building

Hard prerequisites, called out by Jason:

1. **GitHub API access.** Specifically: a GitHub App registered for SourcingNav, with appropriate scopes. Free tier sufficient for v0; the GitHub App pattern future-proofs for higher rate limits via app-installation tokens.
2. **Apollo.io API access.** Specifically: Org tier subscription (~$499/mo) with API access enabled. Alternative tiers don't support API.

**Soft prerequisites (recommended but not blocking):**

3. Confirm Apollo's current pricing and rate limits (their pricing page has changed twice in 18 months).
4. Confirm GitHub's data-search-API hasn't changed scope (they deprecated some endpoints in 2024).
5. Customer signal: at least 3 Pro customers explicitly asking for candidate discovery, OR a single enterprise prospect naming it as a desired feature. Without customer signal, this is speculative work.

---

## What this unlocks (strategic frame)

This feature changes SourcingNav's positioning fundamentally:

**Before:** SourcingNav generates the queries. Recruiter does the work.
**After:** SourcingNav delivers ranked candidate lists with public-domain evidence. Recruiter triages.

That's the difference between a $49/mo workflow tool and a $300/mo agency-grade platform. It's also the difference between competing with Boolean Builder competitors and competing with Gem.

It's also the foundation for two future features that depend on it:
- **Stage 2 Engage (Days 61-90 in v2):** outreach engine needs candidates to outreach to. Discovery IS that pipeline.
- **Preference graph (Days 31-60 in v2):** every promote/reject decision becomes a labeled preference pair. This feature generates the data volume that makes the preference graph viable.

---

## Build trigger

When all three are true, start building:

1. ✅ GitHub App registered with appropriate scopes
2. ✅ Apollo.io Org tier subscription active and API key issued
3. ✅ At least 3 Pro customers (or 1 enterprise prospect) have explicitly asked for candidate discovery

When that triggers fires, this plan should be readable cold and produce a working v0 in roughly 3-4 weeks of focused build:
- Week 1: schema + cron worker + GitHub fetch
- Week 2: Apollo fetch + dedup
- Week 3: fit scoring + UI + tier gating
- Week 4: cost monitoring + privacy controls + manual QA

Until that trigger fires, this plan stays in version control as captured architecture.

---

## Diff vs the Reverse Gem section in v2 roadmap

V2 roadmap committed to the per-search-universe principle and named the data sources (GitHub, Scholar, Crunchbase, conferences, patents, blogs, Slack/Discord). It said the implementation would be ~3-4 weeks for the first 3 adapters.

This plan refines:
- **Sources for v0:** GitHub + Apollo only. Patents/Scholar/Conferences deferred to Phase 2. Crunchbase + blogs + Slack/Discord deferred indefinitely (uncertain ROI).
- **Architectural concrete:** async cron-chunked worker, candidates table, ephemeral-by-default retention.
- **Pricing concrete:** Pro Agency $300/mo as the floor tier, with API costs documented.
- **Privacy concrete:** 30-day retention default, source attribution always visible, deletion endpoint from day 1.
- **Trigger concrete:** API access + customer signal, both required.

V2 is right at the principle level. This plan is right at the implementation level. They're consistent.

---

*Plan complete. Status: TABLED. Revisit when build trigger fires.*

*Companion docs:*
*- `2026-04-28-talent-os-flywheel-roadmap-v2.md` — strategic context*
*- `2026-04-28-competitive-intelligence-integration-plan.md` — sibling integration plan (CI shipped April 28)*
*- `2026-04-28-location-intelligence-future-feature.md` — sibling parked feature*
