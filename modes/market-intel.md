# Mode: Market Intel

> Competitive intelligence engine. Finds every company hiring in your location + industry + adjacent industries, analyzes their postings, and produces hygiene data so you know exactly where you stand.

## Trigger

`/placement-ops market-intel`

## Why This Matters

### For Agencies (Placement-Ops)
You need to know: Who's hiring? What are they paying? How fast are roles filling? Which companies are worth your time vs. wasting it? This mode turns public job posting data into a competitive landscape map that helps you prioritize clients, justify fees, and find market gaps.

### For Companies (People-Ops)
You need to know: Are your postings competitive? Is your comp in range? Who's poaching your people? How does your time-to-fill compare? This mode scans YOUR posted roles against every competitor in your market and tells you where you're winning and where you're losing the talent war.

## Pre-Flight

1. Load `config/integrations.yml` (for data source API keys)
2. Load `config/portals.yml` (154 companies to scan)
3. Load `config/profile.yml` (your niche / your company)
4. Load `taxonomy/skills.yml` (for skill extraction from postings)

## Input

```yaml
market_intel_request:
  # REQUIRED — defines the competitive landscape
  target:
    # EITHER specify your company (company mode):
    company: "Anthropic"
    # OR specify a niche (agency mode):
    niche: "ML/AI Engineering"

  # Location scope
  locations:
    - "San Francisco, CA"
    - "New York, NY"
    - "Remote US"
  radius_miles: 50

  # Industry + adjacent industries to scan
  industries:
    primary: "Frontier AI"
    adjacent:
      - "AI Infrastructure"
      - "Big Tech (AI divisions)"
      - "Enterprise SaaS (ML teams)"
      - "Fintech (data/ML teams)"
      - "Healthtech (ML teams)"

  # Roles to track
  roles:
    - "Machine Learning Engineer"
    - "Data Engineer"
    - "AI Engineer"
    - "ML Platform Engineer"
    - "Data Scientist"
    - "Research Scientist"
    - "Engineering Manager (ML/Data)"

  # Time window
  lookback_days: 90
```

## Analysis Sections

### 1. Competitive Landscape Map

Identify ALL companies hiring for your roles in your locations:

```
COMPETITIVE LANDSCAPE — ML/AI Engineering · SF + NYC + Remote
══════════════════════════════════════════════════════════════

  TOTAL COMPANIES HIRING:     87
  TOTAL OPEN ROLES:          342
  YOUR LOCATION:              SF (hub — 58% of roles)

  BY INDUSTRY:
  ┌──────────────────────┬───────┬───────┬──────────────────┐
  │ Industry             │ Cos   │ Roles │ Avg Comp (P50)   │
  ├──────────────────────┼───────┼───────┼──────────────────┤
  │ Frontier AI Labs     │ 8     │ 68    │ $260K            │
  │ AI Infrastructure    │ 14    │ 56    │ $235K            │
  │ Big Tech (AI div)    │ 12    │ 82    │ $250K            │
  │ Enterprise SaaS (ML) │ 22    │ 64    │ $210K            │
  │ Fintech (data/ML)    │ 16    │ 42    │ $215K            │
  │ Healthtech (ML)      │ 8     │ 18    │ $195K            │
  │ Other                │ 7     │ 12    │ $190K            │
  └──────────────────────┴───────┴───────┴──────────────────┘

  TOP 15 COMPANIES BY POSTING VOLUME (Last 90 Days):
  ┌────┬──────────────────┬───────┬────────┬──────────┬──────────┐
  │ #  │ Company          │ Roles │ Comp   │ Velocity │ Industry │
  ├────┼──────────────────┼───────┼────────┼──────────┼──────────┤
  │ 1  │ Meta AI          │ 42    │ $250K  │ ↑↑↑      │ Big Tech │
  │ 2  │ Anthropic        │ 28    │ $270K  │ ↑↑       │ Frontier │
  │ 3  │ OpenAI           │ 24    │ $280K  │ ↑        │ Frontier │
  │ 4  │ Databricks       │ 22    │ $240K  │ ↑↑↑      │ AI Infra │
  │ 5  │ Google DeepMind  │ 18    │ $260K  │ →        │ Big Tech │
  │ 6  │ Stripe           │ 16    │ $235K  │ ↑        │ Fintech  │
  │ 7  │ Scale AI         │ 14    │ $230K  │ ↑↑       │ AI Infra │
  │ 8  │ Amazon (ML)      │ 14    │ $220K  │ →        │ Big Tech │
  │ 9  │ Snowflake        │ 12    │ $225K  │ ↑        │ AI Infra │
  │ 10 │ Cohere           │ 11    │ $250K  │ ↑↑↑      │ Frontier │
  │ ...│                  │       │        │          │          │
  └────┴──────────────────┴───────┴────────┴──────────┴──────────┘

  VELOCITY KEY: ↑↑↑ = 50%+ increase · ↑↑ = 25-50% · ↑ = growing · → = flat · ↓ = declining
```

### 2. Compensation Hygiene (Company Mode)

Compare YOUR posted comp ranges against the market:

```
COMP HYGIENE CHECK — Your Postings vs. Market
══════════════════════════════════════════════════════════════

  YOUR POSTED ROLES:

  ┌──────────────────────┬────────────┬────────────┬─────────┬─────────┐
  │ Your Role            │ Your Range │ Market P50 │ Delta   │ Grade   │
  ├──────────────────────┼────────────┼────────────┼─────────┼─────────┤
  │ Staff ML Engineer    │ $240-300K  │ $265K      │ +13%    │ 🟢 A    │
  │ Engineering Manager  │ $220-260K  │ $245K      │ +6%     │ 🟢 B+   │
  │ ML Eval Engineer     │ $160-200K  │ $185K      │ +8%     │ 🟢 B+   │
  │ Data Engineer        │ $170-210K  │ $195K      │ +8%     │ 🟢 B+   │
  │ Agent Engineer       │ $180-220K  │ $230K      │ -4%     │ 🟡 C+   │
  │ Junior MLE           │ $130-160K  │ $155K      │ +3%     │ 🟢 B    │
  └──────────────────────┴────────────┴────────────┴─────────┴─────────┘

  ⚠️  ALERT: Agent Engineer range ($180-220K) is BELOW market P50 ($230K).
      Competitive top of range ($220K) is at market P35.
      This explains why this role has been open 8 days with only 4 candidates.
      → RECOMMENDATION: Increase range to $200-250K to hit P50-P65.

  ✅  Staff ML Engineer range is strong — above P60. Good for scarce talent.

  OVERALL COMP COMPETITIVENESS SCORE: B+ (74/100)
  You're competitive on 5/6 roles. Fix the Agent Eng range.
```

### 3. Posting Quality Analysis (Company Mode)

Analyze YOUR career page postings for effectiveness:

```
POSTING QUALITY AUDIT — Your 6 Live Roles
══════════════════════════════════════════════════════════════

  ┌──────────────────────┬──────┬──────┬───────┬──────┬─────────┐
  │ Role                 │ Comp │ JD   │ SEO   │ DEI  │ Overall │
  │                      │ Vis? │ Qual │ Score │ Lang │ Grade   │
  ├──────────────────────┼──────┼──────┼───────┼──────┼─────────┤
  │ Staff ML Engineer    │ ✅   │ A    │ 82    │ ✅   │ A       │
  │ Engineering Manager  │ ✅   │ B+   │ 75    │ ✅   │ B+      │
  │ ML Eval Engineer     │ ❌   │ B    │ 58    │ ✅   │ C+      │
  │ Data Engineer        │ ✅   │ A    │ 88    │ ✅   │ A       │
  │ Agent Engineer       │ ✅   │ C    │ 45    │ ❌   │ C       │
  │ Junior MLE           │ ✅   │ B    │ 70    │ ✅   │ B       │
  └──────────────────────┴──────┴──────┴───────┴──────┴─────────┘

  COMP VISIBILITY: 83% of your postings show comp (vs. 64% market avg) ✅
    → This is a competitive advantage. Candidates strongly prefer visible ranges.

  JD QUALITY ISSUES:
    ⚠️  Agent Engineer JD is 2,400 words — 40% longer than optimal.
        Market best practice: 800-1,200 words. Cut the "nice-to-have" section.
    ⚠️  ML Eval Engineer missing comp range — candidates skip roles without ranges.
        Roles without comp get 30-40% fewer applications.

  SEO SCORE:
    ⚠️  Agent Engineer (45/100) — title "Agent Systems Engineer" doesn't match
        how candidates search. Most search "AI Engineer" or "LLM Engineer."
        → RECOMMENDATION: Rename to "AI/LLM Engineer — Agent Systems"
    ⚠️  ML Eval Engineer (58/100) — role title too niche. Consider
        "ML Engineer — Evaluation & Testing" for broader discovery.

  DEI LANGUAGE:
    ⚠️  Agent Engineer JD uses gendered language: "he will lead..."
        → RECOMMENDATION: Use "they" or "the engineer will lead..."
```

### 4. Time-to-Fill Benchmark

```
TIME-TO-FILL — Your Roles vs. Market Average
══════════════════════════════════════════════════════════════

  ┌──────────────────────┬──────────┬────────────┬──────────┐
  │ Role Type            │ Your TTF │ Market Avg │ vs Mkt   │
  ├──────────────────────┼──────────┼────────────┼──────────┤
  │ Staff MLE            │ 45 days  │ 52 days    │ 🟢 -13%  │
  │ Engineering Manager  │ 32 days  │ 48 days    │ 🟢 -33%  │
  │ ML Eval Engineer     │ 18 days* │ 40 days    │ ⏳ early │
  │ Data Engineer        │ 12 days* │ 30 days    │ ⏳ early │
  │ Agent/LLM Engineer   │ 8 days*  │ 45 days    │ ⏳ early │
  │ Junior MLE           │ 5 days*  │ 22 days    │ ⏳ early │
  └──────────────────────┴──────────┴────────────┴──────────┘

  * = still open, showing days so far

  🟢 Your EM hiring is 33% faster than market — strong process signal.
  ⚠️  Staff MLE at 45 days is still within normal range but approaching
      the "stale posting" threshold (60 days). Pipeline has 3 candidates.
```

### 5. Competitor Talent Movement

Track where talent is flowing in your market:

```
TALENT FLOW — ML/AI Engineering (Last 90 Days)
══════════════════════════════════════════════════════════════

  TOP SOURCES (Where are people coming FROM):
    1. Meta AI          → Lost 12 ML engineers (layoffs + voluntary)
    2. Google DeepMind  → Lost 8 (mostly to startups)
    3. Amazon           → Lost 6 (to Anthropic, OpenAI, Databricks)
    4. Startups (misc)  → Lost 15 (funding dried up)

  TOP DESTINATIONS (Where are people going TO):
    1. Anthropic        → Gained 14 (strongest magnet right now)
    2. OpenAI           → Gained 11
    3. Databricks       → Gained 8
    4. Scale AI         → Gained 6
    5. Cohere           → Gained 5

  YOUR COMPANY:
    Gained: 5 (in line with your hiring plan)
    Lost: 1 (Sarah Kim risk — see retention dashboard)
    Net: +4

  INSIGHT: Meta and Google are net exporters of ML talent right now.
  These are your best sourcing pools. Former Big Tech engineers are
  50% more likely to accept startup offers than 12 months ago.
```

### 6. Industry-Adjacent Opportunity Map (Agency Mode)

Identify companies in adjacent industries that might need your services:

```
ADJACENT INDUSTRY OPPORTUNITIES
══════════════════════════════════════════════════════════════

  Your primary: Frontier AI + AI Infrastructure
  Adjacent industries showing ML hiring signals:

  FINTECH (42 open ML roles):
    Stripe (16), Block (8), Plaid (6), Brex (4), Ramp (4), Affirm (4)
    → You have Stripe as a client. Warm intro to ML hiring mgr at Block?

  HEALTHTECH (18 open ML roles):
    Tempus (6), Recursion (4), Flatiron (3), PathAI (3), Viz.ai (2)
    → Growing fast. Less recruiter competition. Higher margins possible.

  AUTONOMOUS/ROBOTICS (22 open ML roles):
    Waymo (8), Cruise (5), Nuro (4), Figure AI (3), Covariant (2)
    → Overlapping skill sets with your MLE candidates. Cross-sell opportunity.

  DEFENSE/GOVTECH (12 open ML roles):
    Palantir (5), Anduril (4), Shield AI (3)
    → Requires clearance often. Niche within niche = premium fees.

  TOTAL ADDRESSABLE MARKET: 94 additional roles across adjacent industries
  → Represents $1.8M-$2.4M in potential placement fees
```

## Delivery

### Reports Generated

```
reports/market-intel-{YYYY-MM-DD}.md          # Full report
reports/comp-hygiene-{company}-{date}.md      # Company-specific comp audit
reports/posting-audit-{company}-{date}.md     # Posting quality report
data/synced/competitor-postings.tsv           # Raw competitor data
data/synced/comp-benchmarks.yml               # Market comp data
data/synced/market-signals.yml                # Hiring signals
```

### Data Sources (Prioritized)

1. **Portal Scanner** (built-in) — Scrapes career pages from portals.yml
2. **Google Jobs API** (via SerpAPI) — Broadest coverage, comp ranges when posted
3. **LinkedIn Jobs** (via Proxycurl) — Company-specific posting data
4. **Levels.fyi** (WebSearch) — Verified comp data by company + level
5. **Glassdoor** (WebSearch) — Comp ranges, interview data, reviews
6. **Crunchbase** (API) — Funding events that predict hiring surges
7. **Built In** (WebSearch) — Startup-specific comp and culture data
8. **Public SEC filings** (WebSearch) — Headcount data for public companies

### How to Read the Data

```
CONFIDENCE LEVELS:
  🟢 HIGH    — Data from API or verified source (Levels.fyi, Greenhouse API)
  🟡 MEDIUM  — Data from scraping with cross-reference (Google Jobs + careers page)
  🟠 LOW     — Single-source data or estimated (WebSearch only, no verification)
  ⚪ INFERRED — Calculated from adjacent data points (e.g., comp estimated from
               company tier + role level when not posted)

All numbers are labeled with confidence. Never present inferred data as fact.
```

## Post-Intel

1. Save full report to `reports/market-intel-{date}.md`
2. Save comp data to `data/synced/comp-benchmarks.yml`
3. Save competitor postings to `data/synced/competitor-postings.tsv`
4. Feed signals into `modes/forecast.md` (hiring predictions)
5. Feed comp data into `modes/benchmark.md` (candidate positioning)
6. Feed posting analysis into `/placement-ops strategy` (client advisory)
7. For company mode: flag any comp ranges that are below P40 to retention dashboard
8. Print summary: "{N} companies, {M} roles, {K} actionable insights"
