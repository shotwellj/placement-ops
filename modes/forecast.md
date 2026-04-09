# Mode: Forecast

> Predict what your clients will need BEFORE the req opens. Proactive talent engineering, not reactive recruiting.

## Trigger

`/placement-ops forecast`

## Why This Matters

Reactive recruiters wait for a req to hit their inbox. Talent engineers predict hiring needs 30-90 days out and have candidates ready before the client even knows they need them. This is the single biggest competitive advantage a recruiter can have.

## Pre-Flight

1. Load `config/portals.yml` — company list with headcount data
2. Load `data/pipeline.md` — historical req patterns
3. Load `data/placements.tsv` — past placement data
4. Load `data/calibration.yml` — client patterns
5. Load `config/profile.yml`

## Hiring Signal Detection

Use web search to gather signals for each high-priority company:

### Signal Categories

| Signal | Strength | Source | What It Means |
|--------|----------|--------|--------------|
| Funding round announced | 🔴 Strong | Crunchbase, TechCrunch | Hiring surge in 3-6 months |
| Revenue/growth milestone | 🔴 Strong | Press releases, earnings | Team expansion coming |
| New product launch | 🟡 Medium | Product Hunt, blog posts | Need specialists for new domain |
| Executive hire (VP Eng, CTO) | 🟡 Medium | LinkedIn | New leader = new hires (their people) |
| Job postings increasing | 🟡 Medium | Portal scan trends | Already hiring, get in now |
| Layoff → 6 months later | 🟡 Medium | News, LinkedIn | Rehiring cycle — leaner, more targeted |
| Office expansion | 🟡 Medium | Commercial real estate news | Physical growth = headcount growth |
| Competitor acquisition | 🟢 Weak | News | May trigger defensive hiring |
| Conference sponsorship | 🟢 Weak | Event sites | Company investing in visibility = growth mode |
| Engineering blog activity | 🟢 Weak | Company blog | Active blog = healthy eng culture = hiring |

### Signal Scoring

Each signal gets a score:

```
signal_strength × recency × company_priority = forecast_score
```

- `signal_strength`: Strong (1.0), Medium (0.6), Weak (0.3)
- `recency`: This week (1.0), This month (0.8), This quarter (0.5)
- `company_priority`: high (1.0), medium (0.6), low (0.3)

## Forecast Output

### 1. Hiring Probability Heat Map

```
HIRING FORECAST — Next 90 Days
══════════════════════════════════════════════════════════════════

Company          Signals                          Score   Prediction
─────────────────────────────────────────────────────────────────
Scale AI         Series E + 3 new MLE posts       0.92    🔴 HIRING NOW
Glean            $200M raise (Feb), VP Eng hire    0.87    🔴 WITHIN 30 DAYS
Databricks       Earnings beat, 12 new posts       0.81    🔴 WITHIN 30 DAYS
Anthropic        Always hiring, 5 new ML posts     0.78    🟡 ONGOING
Ramp             Series D, product launch          0.72    🟡 WITHIN 60 DAYS
Tempus AI        IPO capital, 8 new posts          0.69    🟡 WITHIN 60 DAYS
Cursor           Revenue 10x, small team           0.64    🟡 WITHIN 60 DAYS
Harvey AI        Series C, expanding SF office     0.58    🟢 WITHIN 90 DAYS
Stripe           Stable, 2 backfill posts          0.34    ⚪ LOW PROBABILITY
Netflix          Flat posting volume               0.21    ⚪ LOW PROBABILITY
```

### 2. Expansion Predictions (Existing Clients)

For companies you've already placed at:

```
EXPANSION PREDICTIONS — Existing Clients
══════════════════════════════════════════════════════════════════

Anthropic (placed 2 candidates):
  Last placement: 2026-03-15 (Senior MLE)
  Team growth signal: Hiring manager posted about "growing the team" on LinkedIn
  Related open roles: 3 ML roles currently posted
  Prediction: 80% chance of needing 1-2 more ML engineers within 60 days
  → ACTION: Reach out to HM now. "I see you're growing the team. I have 2 candidates
    who scored 4.0+ against your last req. Want to see them?"

Scale AI (placed 1 candidate):
  Last placement: 2026-02-28 (Data Scientist)
  Expansion signal: 5 new data roles posted since placement
  Prediction: 90% chance of needing more. They're building a whole team.
  → ACTION: Propose a retained search for the remaining 3-4 roles. Bulk deal.
```

### 3. Proactive Candidate Matching

For each forecasted hiring surge, check your bench:

```
PROACTIVE MATCHES
══════════════════════════════════════════════════════════════════

Scale AI (predicted: 2-3 MLE hires within 30 days):
  Ready candidates:
    ✅ Alice Chen — 4.1 composite, prepped, available
    ✅ John Doe — 3.8 composite, needs fresh eval against new JD
    ⚠️ Bob Wilson — 3.2, probably too junior

  Sourcing gap: Need 1-2 more Senior+ MLE candidates for Scale AI pipeline
  → ACTION: Run /placement-ops scan with Scale AI JD keywords to find new candidates

Glean (predicted: Senior DE hire within 30 days):
  Ready candidates:
    ❌ None in bench match DE archetype
  → ACTION: Start sourcing Data Engineers now. You'll have a 2-week head start.
```

### 4. Market-Level Trends

```
MARKET TRENDS — Data/ML/AI
══════════════════════════════════════════════════════════════════

Trending up (more postings vs. last quarter):
  🔺 +34%  AI Engineer (LLM/agent roles)
  🔺 +28%  ML Platform Engineer
  🔺 +22%  Data Engineer (real-time)
  🔺 +15%  ML Engineering Manager

Trending down:
  🔻 -12%  Data Analyst (junior)
  🔻 -8%   Research Scientist (pure research)

Emerging roles (new in last 90 days):
  🆕 "AI Safety Engineer" — appearing at Anthropic, OpenAI, DeepMind
  🆕 "Agent Engineer" — appearing at LangChain, Cursor, Sierra
  🆕 "ML Compliance Engineer" — appearing at regulated industries

Comp trend: +6% YoY for Senior MLE (driven by LLM demand)

→ STRATEGIC INSIGHT: Shift sourcing toward AI Engineers and Agent Engineers.
  These roles are growing fastest and command the highest fees.
```

## Post-Forecast

1. Save to `reports/forecast-{YYYY-MM-DD}.md`
2. Update `data/pipeline.md` with predicted reqs (status: "Forecasted")
3. Generate outreach suggestions for predicted expansions
4. Flag sourcing gaps: roles you'll likely need candidates for but don't have them
5. Print the top 3 actions: "Call Scale AI HM. Source DE for Glean. Evaluate Alice for Databricks."
