# Location Intelligence: Talent Density Mapping + Relocation Economics

**Author:** Jason Shotwell
**Date:** 2026-04-28
**Status:** Captured insight, not yet sequenced
**Companion:** `2026-04-28-talent-os-flywheel-roadmap-v2.md` (this is a Layer 3 feature on the long-term roadmap)

---

## The insight (from Jason, end of Day 3 CI work)

The CI integration we just shipped tells recruiters which **companies** to source from. It doesn't tell them which **cities** to source from, or how to economically justify a relocation pitch when the talent density doesn't match the role's location.

Concrete example from Jason: an embedded software role posted in Irvine, CA, but the actual density of qualified embedded engineers is in Austin, TX. The recruiter needs:

1. Empirical proof that Austin has 3-5x more qualified candidates than Irvine for this skill cluster
2. Economic math that closes the candidate against the geographic friction (cost of living, tax burden, mortgage rates, commute economics)
3. A specific dollar-figure relocation pitch that the candidate can show their spouse

No existing recruiting tool solves this well. Gem, Bullhorn, LinkedIn Recruiter all assume the candidate is already where the role is. Compensation tools (Pave, Levels.fyi) give comp data but don't translate it into "is moving worth it."

This is a **Layer 3 (Talent Intelligence) feature with strong product-market fit** because every senior recruiter feels the pain weekly.

---

## What the feature would actually do

For any requisition the recruiter has parsed, the system surfaces:

**Section 1: Talent density map**

Top 5-7 metro areas where the skill cluster actually concentrates. For each metro:

- Estimated number of qualified engineers (using GitHub commits + Scholar publications + company team-page member counts as proxies, NOT a "we crawled LinkedIn" claim)
- Top 5 companies in that metro hiring this skill cluster
- Cost-per-source estimate (based on density and recent hiring velocity)
- A confidence flag (HIGH for cities with rich public signal, LOW for cities with thin data)

**Section 2: Relocation economics for the requisition's offered comp**

For each candidate metro, calculate the "real" value of moving to the role's location:

- Cost of living delta vs role location (using a credible source like NerdWallet or Numbeo with citation, NOT made-up numbers)
- State + local tax burden delta (Texas to California is a significant negative, Texas to Florida is neutral)
- Median home price delta + current 30-year mortgage rate impact on monthly housing cost
- Daycare / childcare cost delta (huge factor for senior engineers in their 30s/40s)
- Implied total economic gap (or surplus) the offer needs to overcome

**Section 3: The relocation pitch**

A pre-built recruiter script tailored to each candidate metro:

> "Your $200K base in Austin is roughly equivalent to $250K base in Irvine after California taxes. Our offer is $290K base. That's a real $40K/yr improvement after all the math, plus cleared embedded work for a defense customer (career capital that doesn't exist in Austin). Here's the breakdown your spouse can review: [embedded comp comparison link]"

This is the **actual deliverable that closes candidates** — not the talent map, but the spreadsheet-with-narrative that turns "I don't want to move" into "let me think about it."

**Section 4: Auto-flag when the offer doesn't beat staying put**

If the recruiter's offered comp doesn't exceed the candidate's likely current comp + cost-of-living adjustment, flag it. Suggest specific levers:
- Push the offer up by $X
- Add a sign-on bonus to cover moving costs
- Negotiate a remote-friendly arrangement
- Reposition the role to a higher-density metro

---

## How this connects to the existing flywheel

**Inputs (already exist):**
- `parsed.core.location` — the role's location
- `parsed.must_have_skills` — the skill cluster to map
- `parsed.comp_snapshot.base_range` — the offered comp
- `parsed.market360.poaching_targets` — companies, which often correlate with metros

**What's needed (not yet built):**

1. **Talent density signal layer.** Per-metro counts of engineers matching a skill cluster. Best built from public data:
   - GitHub commits + location field in profile (free, but profile location is messy)
   - Conference speaker lists (PyData, KubeCon, DEF CON) by metro
   - USPTO patent filings by inventor location
   - Crunchbase company team pages by HQ city
   - Public meetup attendance / community member counts

2. **Cost-of-living + tax data.** Static-ish data, refreshed quarterly:
   - Numbeo or BestPlaces for COL index
   - SmartAsset or NerdWallet for state/local tax
   - Freddie Mac PMMS for mortgage rates (weekly update)
   - Care.com for childcare costs
   - All cite-able to a public source. NEVER fabricate.

3. **Relocation calculator service.** Pure function: given (origin_metro, dest_metro, offered_comp, family_size_optional), return economic delta dict.

4. **AI-generated pitch script.** Given the calculator output + role context, generate the recruiter pitch. This is the AI-powered piece. Honesty rules same as CI prompt.

---

## Why this is Layer 3, not Layer 1

Layer 1 features (recruiter-facing, fillable today) are about helping the recruiter do their job for THIS req. Layer 3 features (talent intelligence) are about giving the recruiter market-level insight that informs strategy across MANY reqs.

Talent density mapping is genuinely Layer 3:
- Useful even before a specific search is open ("which metros should we be sourcing from for ML engineers next quarter?")
- Compounds with use (more users = more aggregate data = better density estimates)
- Saleable to executives, not just individual recruiters
- Defensible — competitors would need both the data AND the cost-of-living/tax integration AND the recruiter-pitch UX

---

## Where this fits in the v2 roadmap

V2 named four sequencing buckets:

- **Days 1-30:** Integrate Competitive Intelligence + Sourcing Jamboard + DEI Strategy + Silver Medalist Rediscovery + Phase B3 finishing → CI shipped today, others pending
- **Days 31-60:** Stage 3 Match MVP + preference graph + Outreach v2 fix
- **Days 61-90:** Stage 2 Engage + outreach engine + smart response + calendar Phase 1
- **Days 91+:** ATS integration OR Stage 4 OR market-led pivot

**Recommendation: park Location Intelligence at Days 91+ as a Layer 3 candidate.** Not because it's lower-value than Stage 2 Engage — it might actually be higher-value to senior recruiters — but because:

1. The data sources need ~3-4 weeks of adapter work (GitHub location parsing, Numbeo API, mortgage rate feed, tax data refresh pipeline)
2. The relocation pitch UX deserves its own design pass, not a bolted-on tab on the intake page
3. Stage 2 (Engage) is the higher-priority bottleneck per Days 1-90 (recruiters need outreach + scheduling integrated to reduce tool-switching)

If a Pro customer specifically asks for it before Day 91, that's a strong signal to fast-track. Otherwise, hold.

---

## What "MVP" looks like if we did fast-track this

If we wanted to ship this in 1 week instead of 4, the skinny version:

**Skinny MVP scope:**
- ONE data source: Numbeo CoL index + a published state-tax-burden table (manual quarterly refresh, no live API)
- Static talent density table for top 30 metros (manual researcher pass — Jason knows this market, could write the table from domain knowledge)
- Calculator function that takes (role_metro, offered_base, candidate_current_metro_optional) → relocation_delta_dict
- Single AI call generating a 3-paragraph relocation pitch
- Surface as a click-to-run card on intake (matches CI pattern)
- Pro tier only

**What this skinny version misses:**
- Real per-skill-cluster density (talent map is metro-wide, not skill-specific)
- Family-size-aware childcare math
- Mortgage rate live feed (would use a frozen snapshot)
- Confidence flags on every metric

**Why the skinny version is still valuable:**
- The CALCULATOR is the differentiator, not the data feeds. Numbeo + tax tables + offered comp gets you 80% of the math.
- Recruiters care about closing candidates, not data perfection.
- A click-to-run "show me the math" feature is genuinely novel in recruiting tools today.

**Estimated work:** 1 week if we used static-ish data, 4 weeks if we built the live data pipelines. Skinny version is a real option if customer signal demands it.

---

## Risks worth naming

1. **Fabricated relocation math.** If the AI ever generates "Austin to Irvine costs $X more" without citing a source, we've created a tool that recruiters will get burned by when candidates fact-check. The honesty rules from the CI prompt MUST carry over: every dollar figure cites a source or flags low confidence.

2. **Legal/regulatory risk on comp claims.** California's SB 1162 and similar pay transparency laws complicate any tool that publishes salary ranges. We'd need to disclaim "estimates only, verify with official sources" prominently.

3. **Cost-of-living data licensing.** Numbeo's free tier is rate-limited. Bestplaces.net data isn't licensed for commercial reuse. If this becomes a real product surface, we'd need a licensing review (similar to AIR Blackbox's compliance frame).

4. **Skill-density estimation is hard.** GitHub commits + location is noisy. A claim like "Austin has 3,000 embedded Linux engineers" is harder to defend than "Austin has high public signal for embedded Linux developers based on conference talks, patents, and active GitHub repos." Frame as qualitative density, not exact counts.

---

## What I'd do tomorrow (if this became priority 1)

Not a recommendation to do this now — just capturing the path so future-Jason has it.

1. **Day 1-2:** Pull Numbeo + state tax table + a snapshot of mortgage rates into a static `data/cost_of_living.json` file. Write the calculator function. Smoke-test against 3 city pairs.

2. **Day 3-4:** Build the AI prompt for relocation pitch. Test against 3 candidate scenarios (single tech worker, married with kids, executive comp band). Verify it cites sources and flags uncertainty.

3. **Day 5:** Wire as a click-to-run card on intake. Free tier sees locked placeholder. Pro tier gets full pitch.

4. **Day 6-7:** Manual QA against 5 real reqs, iterate on prompt, ship.

That's the skinny version. The full version with talent density + live data pipelines is a 3-4 week build.

---

## How to know this is worth building

Three customer-signal triggers that would justify pulling this forward:

1. **At least 3 Pro customers explicitly request it** in the first 30 days post-launch (CI feature is the early signal generator — recruiters who use CI heavily are the same ones who'd use Location Intelligence)
2. **A specific high-value search** where geographic friction is the dominant blocker (defense contractor in low-density metro, niche skill cluster, executive role with relocation budget)
3. **A specific enterprise prospect** (Fortune 500 in-house TA team) where this is named as a desired feature in an intro call

If none of these triggers fire by Day 90, it's not the next thing to build. Park.

---

## Why this captured matters even if we don't build it

If this is anywhere on Jason's roadmap, it deserves to be in version control. The pattern of "great insight in a chat → never written down → lost in compaction" is exactly what v2's asset inventory was created to fight. Capturing it here keeps the roadmap honest about what's known and prevents re-discovering it from scratch in 4 months.

---

*Doc complete. Next concrete action: nothing required today — this lives in version control as a future-roadmap artifact. Revisit when one of the three customer-signal triggers fires.*
