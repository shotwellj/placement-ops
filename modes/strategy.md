# Mode: Strategy

> Client-facing talent strategy reports. Turn hiring conversations into workforce planning partnerships.

## Trigger

`/placement-ops strategy`

## Why This Matters

Recruiters fill roles. Talent engineers advise on *what roles to create*. This mode generates strategic hiring plans for your clients — org structure analysis, hiring sequence recommendations, talent gap assessments, and growth roadmaps. It's the deliverable that turns you from a vendor into a strategic partner.

When you hand a VP of Engineering a document that says "here's how to grow your ML team from 8 to 15, in what order, and why" — you're no longer competing on fee percentage. You're competing on insight.

## Pre-Flight

1. Load `config/profile.yml`
2. Load `data/pipeline.md`
3. Load `data/placements.tsv`
4. Load `data/calibration.yml`
5. Load `taxonomy/skills.yml`
6. Load `taxonomy/competencies.yml`

## Input: What You Need from the Client

Before generating a strategy report, gather:

```yaml
strategy_input:
  company: Anthropic
  contact: Sarah Chen (VP Engineering)
  date: 2026-04-08

  # Current state
  current_team:
    total_headcount: 8
    roles:
      - title: Senior MLE
        count: 3
        skills: [PyTorch, distributed training, RLHF]
      - title: Data Engineer
        count: 2
        skills: [Spark, Airflow, Snowflake]
      - title: ML Platform Engineer
        count: 1
        skills: [Kubernetes, MLflow, AWS]
      - title: Research Scientist
        count: 1
        skills: [NLP, transformers, evaluation]
      - title: Engineering Manager
        count: 1
        skills: [people management, hiring, roadmap]

  # Where they want to go
  growth_target: 15          # desired headcount
  timeline: "12 months"
  budget_range: "$2.5M-$3.5M annual comp"
  strategic_goals:
    - "Ship production agent system by Q4"
    - "Build evaluation infrastructure"
    - "Reduce model iteration cycle from 2 weeks to 3 days"

  # Constraints
  constraints:
    - "Mostly remote, some SF preferred"
    - "No H1B sponsorship capacity right now"
    - "Need at least 2 senior hires before scaling junior"
```

## Strategy Report Sections

### 1. Current State Assessment

Analyze the existing team against their goals:

```
CURRENT STATE ASSESSMENT — Anthropic ML Team
══════════════════════════════════════════════════════════════════

Team Composition (8 people):
  🟢 Strong:    Model training (3 Senior MLEs)
  🟢 Strong:    Data infrastructure (2 DEs)
  🟡 Thin:      ML Platform (1 person — bus factor risk)
  🟡 Thin:      Research (1 person — bottleneck for eval work)
  🔴 Gap:       No dedicated agent/LLM engineer
  🔴 Gap:       No ML evaluation specialist
  🔴 Gap:       No second engineering manager (span too wide at 15)

Skill Coverage (mapped against stated goals):

  Goal: "Ship production agent system by Q4"
    ✅ Have: PyTorch, distributed training, Kubernetes
    ⚠️ Thin: LangChain/agent frameworks, production serving
    ❌ Missing: Agent evaluation, tool-use patterns, guardrails

  Goal: "Build evaluation infrastructure"
    ✅ Have: NLP fundamentals, basic eval experience
    ⚠️ Thin: Statistical rigor, automated eval pipelines
    ❌ Missing: Dedicated eval engineering, benchmark design

  Goal: "Reduce iteration cycle"
    ✅ Have: Airflow, Spark, basic MLOps
    ⚠️ Thin: CI/CD for ML, experiment tracking at scale
    ❌ Missing: Feature store, real-time serving infrastructure

OVERALL: Team is strong on core ML but lacks the infrastructure
and evaluation layer needed to hit Q4 goals.
```

### 2. Competency Gap Analysis

Using the competency framework, identify gaps beyond technical skills:

```
COMPETENCY GAP ANALYSIS
══════════════════════════════════════════════════════════════════

Technical Competencies:
  System Design Thinking    ██████████  Covered (3 seniors + EM)
  Production Engineering    ███████░░░  Gap (need ML Platform depth)
  Research Methodology      █████░░░░░  Gap (only 1 researcher)
  Evaluation Design         ██░░░░░░░░  Critical gap
  Agent Architecture        ░░░░░░░░░░  Not present — needed for Q4

Leadership Competencies:
  People Management         █████░░░░░  1 EM for 7 ICs (max span)
  Technical Direction       ████████░░  Strong (senior-heavy team)
  Cross-Functional Comm     ███████░░░  Adequate
  Hiring Capability         ████░░░░░░  Need hiring managers, not just EM
  Mentorship Capacity       ████░░░░░░  Seniors busy, no bandwidth for juniors yet

→ KEY INSIGHT: Don't hire juniors until you have 2+ seniors with
  mentorship bandwidth. Premature junior hiring slows everyone down.
```

### 3. Recommended Org Structure (Target State)

Design the ideal team at the target headcount:

```
RECOMMENDED ORG STRUCTURE — 15 People
══════════════════════════════════════════════════════════════════

                    VP Engineering (Sarah Chen)
                           │
            ┌──────────────┼──────────────┐
            │              │              │
      EM - Training    EM - Platform   Tech Lead - Agents
      (EXISTING)       (HIRE #5)       (HIRE #1 — critical)
            │              │              │
    ┌───┬───┤        ┌─────┤        ┌─────┤
    │   │   │        │     │        │     │
  Sr  Sr  Sr       Sr   ML Plat   Sr    Agent
  MLE MLE MLE     DE    Eng      MLE    Eng
  (E) (E) (E)    (E)   (HIRE    (HIRE  (HIRE
                        #3)      #2)    #4)
                   │
                  DE     Eval Eng   Agent Eng   Jr MLE
                  (E)    (HIRE #6)  (HIRE #7)   (HIRE #8-9)

  (E) = Existing    HIRE #N = Recommended hire order

TOTAL: 15 people across 3 pods
  Training Pod:     4 people (existing, stable)
  Platform Pod:     4 people (need EM + ML Platform Eng + Eval Eng)
  Agent Pod:        4 people (all new — highest priority)
  Leadership:       3 people (add 1 EM, promote 1 to Tech Lead)
```

### 4. Hiring Sequence (The Roadmap)

Order matters. Each hire unlocks the next:

```
HIRING SEQUENCE — 7 New Hires Over 12 Months
══════════════════════════════════════════════════════════════════

PHASE 1: Foundation (Months 1-3) — 2 hires
─────────────────────────────────────────────
  HIRE #1: Senior Agent/LLM Engineer (Tech Lead track)
    Why first: Unblocks Q4 agent system goal. No one on team
    has production agent experience. This person defines the
    architecture everyone else builds on.
    Comp range: $220-260K base + equity
    Scarcity: HIGH — 90th percentile demand, limited supply
    Time to fill: 30-45 days (start sourcing immediately)
    Fee estimate: $44-52K (at 20%)

  HIRE #2: Senior MLE (Agent-focused)
    Why second: Pairs with Hire #1 to form the agent pod core.
    Two seniors can start building while you hire around them.
    Comp range: $200-240K base + equity
    Scarcity: MEDIUM-HIGH
    Time to fill: 25-35 days
    Fee estimate: $40-48K

  Phase 1 total fee opportunity: $84-100K

PHASE 2: Infrastructure (Months 4-6) — 2 hires
─────────────────────────────────────────────────
  HIRE #3: Senior ML Platform Engineer
    Why now: Agent system needs serving infra. Existing ML
    Platform Eng is a single point of failure.
    Comp range: $200-240K base + equity
    Scarcity: MEDIUM
    Time to fill: 25-35 days
    Fee estimate: $40-48K

  HIRE #4: Agent Engineer (mid-level)
    Why now: Hire #1 and #2 have defined the architecture.
    Now you can hire someone to build under their direction.
    Comp range: $160-190K base + equity
    Scarcity: MEDIUM (emerging role, growing supply)
    Time to fill: 20-30 days
    Fee estimate: $32-38K

  Phase 2 total fee opportunity: $72-86K

PHASE 3: Scale (Months 7-12) — 3 hires
─────────────────────────────────────────────────
  HIRE #5: Engineering Manager (Platform Pod)
    Why now: Team is at 12, EM span is breaking. Need a second
    manager before adding juniors.
    Comp range: $220-260K base + equity
    Scarcity: MEDIUM
    Time to fill: 35-50 days (managers take longer)
    Fee estimate: $44-52K

  HIRE #6: ML Evaluation Engineer
    Why now: Eval infra goal. Seniors have bandwidth to mentor.
    Comp range: $170-200K base + equity
    Scarcity: LOW-MEDIUM (niche but growing)
    Time to fill: 30-40 days
    Fee estimate: $34-40K

  HIRE #7: Junior MLE (1-2 hires)
    Why last: Team structure is set. Mentorship capacity exists.
    Junior hires are highest ROI when the foundation is solid.
    Comp range: $140-170K base + equity
    Scarcity: LOW (large candidate pool)
    Time to fill: 15-25 days
    Fee estimate: $28-34K each

  Phase 3 total fee opportunity: $106-126K

═══════════════════════════════════════════════════════════════
TOTAL FEE OPPORTUNITY: $262-312K over 12 months
  Your projected revenue from this single client relationship.
═══════════════════════════════════════════════════════════════
```

### 5. Comp Benchmarking Summary

For each recommended hire, include market data:

```
COMP BENCHMARKS — Bay Area / Remote (2026)
══════════════════════════════════════════════════════════════════

Role                      P25      P50      P75      P90
─────────────────────────────────────────────────────────────────
Sr Agent/LLM Engineer    $200K    $230K    $260K    $290K
Senior MLE               $190K    $215K    $245K    $275K
Sr ML Platform Eng       $185K    $210K    $240K    $265K
Agent Engineer (Mid)     $145K    $165K    $185K    $210K
Engineering Manager      $210K    $240K    $265K    $295K
ML Eval Engineer         $160K    $180K    $200K    $225K
Junior MLE               $130K    $150K    $170K    $190K

Note: Ranges include base salary only. Total comp (base + equity +
bonus) typically adds 30-60% at funded startups.

Client's budget of $2.5-3.5M covers 7 hires at P50-P75.
Recommendation: Pay P75 for Hire #1 (critical role, scarce talent).
Pay P50 for Hires #6-7 (more available, less critical).
```

### 6. Risk Assessment

```
RISK ASSESSMENT
══════════════════════════════════════════════════════════════════

🔴 HIGH RISK: Agent Tech Lead (Hire #1)
  This is the linchpin hire. If this takes 60+ days or the wrong
  person is hired, Q4 goal is at risk. Mitigation: start sourcing
  now, consider retained search for this role.

🟡 MEDIUM RISK: Single EM until Month 7
  Current EM will be managing 11 people by Phase 2. Risk of
  burnout and attrition. Mitigation: promote a senior to
  "Tech Lead" (no direct reports but architectural authority)
  as an interim measure.

🟡 MEDIUM RISK: H1B constraint
  Removes ~30% of senior ML talent pool. Mitigation: prioritize
  candidates with existing work authorization. Expand remote to
  include US-based talent outside SF.

🟢 LOW RISK: Junior hiring (Phase 3)
  Large candidate pool, straightforward roles. Low risk of not
  filling. Main risk is hiring too early before mentorship
  capacity exists.
```

### 7. Proactive Candidate Matching

Cross-reference with your bench:

```
CANDIDATES READY NOW
══════════════════════════════════════════════════════════════════

For Hire #1 (Sr Agent/LLM Engineer):
  ✅ Alice Chen — 4.3 composite, agent experience at previous role
  ⚠️ No other candidates in bench. Need to source.
  → ACTION: Run /placement-ops scan for agent engineer roles
    to identify passive candidates at competitor companies.

For Hire #2 (Senior MLE):
  ✅ John Doe — 4.1 composite, strong PyTorch, production ML
  ✅ Carol Davis — 3.9, slightly junior but high upside
  → ACTION: Prep John first. Evaluate Carol against updated JD.
```

## Delivery Format

Generate the strategy report as a markdown file:

```
reports/strategy-{company}-{YYYY-MM-DD}.md
```

Also generate a one-page executive summary suitable for email:

```
output/strategy-summary-{company}-{YYYY-MM-DD}.md
```

The executive summary should be:
- One page max
- 3 key findings
- The recommended first 2 hires
- Total fee opportunity
- A call to action ("Let's schedule 30 minutes to walk through the full plan")

## Post-Strategy

1. Save full report to `reports/strategy-{company}-{YYYY-MM-DD}.md`
2. Save executive summary to `output/strategy-summary-{company}-{YYYY-MM-DD}.md`
3. Log strategy engagement in `data/pipeline.md` as a strategic touchpoint
4. Create pipeline entries for each recommended hire (status: "Forecasted")
5. Flag immediate sourcing needs (Phase 1 roles)
6. Update `data/calibration.yml` with any new company intelligence gathered
7. Print the money shot: "Total fee opportunity: $X-$Y over Z months"
