# Mode: Benchmark

> Compare a candidate's profile against market data to determine positioning strength and comp leverage.

## Trigger

`/placement-ops benchmark`

## What This Does

Takes a candidate's skill profile and tells you:
1. How rare is this person in the current market?
2. What should they be earning?
3. Which companies would fight hardest for them?
4. Where does this candidate sit vs. the talent pool?

This powers smarter fee negotiations ("this is a top-10% candidate, my fee reflects that") and helps you set candidate expectations.

## Input

The recruiter provides:
- A candidate file from `candidates/`
- Optionally: a specific role or market to benchmark against

## Workflow

### Step 1 — Skill Profile Summary

Extract the candidate's skill profile from their resume:

```yaml
profile:
  name: Jane Smith
  archetype: IC-MLE
  years_experience: 7
  core_skills:
    - Python (expert, current)
    - PyTorch (production, current)
    - TensorFlow (production, recent)
    - Kubernetes (production, current)
    - Apache Spark (project, dated)
  domain_expertise:
    - Recommendation Systems
    - Real-time ML Serving
  leadership_signals:
    - Mentored 3 junior engineers
    - Led architecture redesign
  notable:
    - "50M daily predictions system"
    - "90% latency reduction on serving migration"
```

### Step 2 — Market Scarcity Analysis

Use web search to assess supply/demand for this skill combination:

```markdown
## Market Scarcity: Jane Smith

### Skill-by-Skill Demand

| Skill | Job Postings Requiring It | LinkedIn Profiles With It | Supply/Demand Ratio |
|-------|--------------------------|--------------------------|-------------------|
| PyTorch + Production ML | ~12,000 open roles | ~45,000 profiles | 3.75:1 (candidate's market) |
| Kubernetes + ML | ~8,000 open roles | ~25,000 profiles | 3.13:1 (candidate's market) |
| Rec Systems + Senior | ~3,000 open roles | ~8,000 profiles | 2.67:1 (candidate's market) |

### Combination Rarity

The intersection of PyTorch + Kubernetes + Recommendation Systems + 5+ years
narrows the candidate pool to approximately **2,000-4,000** people in the US.

**Scarcity Rating: HIGH** — This candidate has a skill combination that fewer than
5,000 people in the US possess. Expect multiple competing offers if actively looking.

### What This Means for You (the recruiter)

- **Fee leverage**: HIGH. This is a hard-to-fill profile. Push for retained or 25%.
- **Speed required**: HIGH. If she's on the market, she'll have offers in 2-3 weeks.
- **Exclusivity value**: Present as exclusive for 2 weeks to close faster.
- **Client pitch**: "This is a top-5% candidate in ML Engineering. There are fewer than 4,000 people with this skill stack in the US."
```

### Step 3 — Compensation Benchmark

```markdown
## Compensation Benchmark

### Market Data (sourced from Levels.fyi, Glassdoor, Blind)

| Level | Base Salary | Total Comp | Source |
|-------|------------|-----------|--------|
| Senior MLE (IC4) at FAANG | $180-220K | $300-450K | Levels.fyi |
| Senior MLE (IC4) at Unicorn | $170-200K | $250-350K | Levels.fyi |
| Senior MLE at Mid-Stage Startup | $160-190K | $200-280K | Glassdoor |
| Senior MLE at Series A-B | $150-180K | $180-250K | Estimated |

### This Candidate's Position

Jane's target of $180-220K base is:
- **At market** for FAANG/Unicorn Senior MLE
- **Above market** for mid-stage startups
- **Premium** for Series A-B companies

### Recommended Positioning

| Client Type | Expected Base | Your Fee (20%) | Negotiation Room |
|-------------|-------------|----------------|-----------------|
| FAANG/Tier 1 | $195K | $39,000 | Limited — standard bands |
| Unicorn | $185K | $37,000 | $10-15K flex on equity |
| Growth Stage | $175K | $35,000 | Can push base with equity tradeoff |

### Estimated Fee Range: $35,000 — $44,000
```

### Step 4 — Company Fit Heat Map

Which companies in your pipeline would value this candidate most?

```markdown
## Company Fit Heat Map

| Company | Open Role Match | Skill Overlap | Comp Fit | Scarcity Leverage | Overall |
|---------|----------------|--------------|---------|-------------------|---------|
| Anthropic | Senior MLE | 92% | ⚠️ At top of band | HIGH | 🔥🔥🔥🔥 |
| Databricks | ML Platform Eng | 85% | ✅ Within band | HIGH | 🔥🔥🔥🔥 |
| Stripe | Senior DS | 70% | ✅ Within band | MEDIUM | 🔥🔥🔥 |
| Scale AI | Staff MLE | 88% | ✅ Below band | HIGH | 🔥🔥🔥🔥🔥 |
| Netflix | ML Engineer | 80% | ⚠️ Wants more | LOW | 🔥🔥 |

**Top recommendation**: Submit to Scale AI first — best skill overlap +
comp headroom + they're hiring aggressively.
```

### Step 5 — Talent Pool Positioning

Where does this candidate rank vs. the broader market?

```markdown
## Talent Pool Position

Based on skill depth, experience, and market scarcity:

  Top 1%  ██
  Top 5%  ████████  ← Jane Smith is HERE
  Top 10% ██████████████
  Top 25% ████████████████████████
  Top 50% ████████████████████████████████████████

**Percentile: Top 5%** for Senior ML Engineers in the US market.

Factors driving this:
- Production recommendation systems experience (rare)
- Real-time serving at scale (50M predictions/day)
- Full-stack ML (training + serving + infra)
- Strong communication (can sell herself in interviews)

Factors holding back from Top 1%:
- No published research or patents
- No FAANG on resume
- Spark experience is dated
```

## Post-Benchmark (Mandatory)

1. Save to `reports/benchmark-{candidate-slug}-{YYYY-MM-DD}.md`
2. Update the candidate file with the scarcity rating and comp benchmark
3. If the benchmark reveals strong fits in your pipeline, suggest running `/placement-ops evaluate`
4. Print the one-line summary: "Jane Smith: Top 5%, $35-44K fee potential, submit to Scale AI first"
