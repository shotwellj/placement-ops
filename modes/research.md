# Mode: Research

> Deep-dive on a company — culture, comp data, hiring patterns, interview process, and key contacts.

## Trigger

`/placement-ops research`

## Input

The recruiter provides a company name. Optionally a specific role or team.

## What This Produces

A company intelligence brief that helps you:
1. Decide whether to pursue this client
2. Prep candidates for their specific interview process
3. Price your fee correctly
4. Know who to contact

## Research Sections

### Section 1 — Company Overview

Use web search to gather:

| Field | Source |
|-------|--------|
| HQ location, offices | Company website |
| Employee count | LinkedIn |
| Funding stage / valuation | Crunchbase, PitchBook |
| Recent news | Google News |
| Revenue (if public) | SEC filings, press releases |
| Tech stack | Job postings, StackShare, engineering blog |
| Engineering blog | Google search |

### Section 2 — Compensation Intelligence

| Field | Source |
|-------|--------|
| Salary ranges for your niche roles | Levels.fyi, Glassdoor, Blind |
| Equity structure (RSU, options, refresh) | Levels.fyi, Blind |
| Signing bonus patterns | Blind, Glassdoor |
| Comp reputation (top/mid/low market) | Aggregate assessment |
| Total comp for target level | Calculated |

**Fee Estimate**: Based on midpoint TC × your contingency rate.

### Section 3 — Hiring Patterns

| Signal | What to Look For |
|--------|-----------------|
| Current open roles | Scan their careers page |
| Volume trend | Are they posting more or fewer roles vs. 3 months ago? |
| Time-to-fill | How long do their postings stay up? |
| Repeat postings | Same role reposted = struggling to fill = opportunity |
| Agency usage | Do they work with agencies? (Check LinkedIn for agency placements) |
| Internal recruiting team | How big is their TA team? (LinkedIn) |
| Hiring manager | Who leads the team you'd submit to? |

### Section 4 — Interview Process

| Field | Source |
|-------|--------|
| Interview stages | Glassdoor interviews, Blind, levels.fyi |
| Technical assessment type | Glassdoor (coding, system design, ML design, take-home) |
| Interview duration | Glassdoor |
| Common questions | Glassdoor, Blind |
| Difficulty rating | Glassdoor |
| Offer timeline | Glassdoor |
| Known interviewers | LinkedIn (team members) |

### Section 5 — Recruiter-Specific Intel

| Question | Assessment |
|----------|-----------|
| Do they work with agencies? | Yes/No/Unknown + evidence |
| Who's the TA contact? | Name + LinkedIn if findable |
| What's their ATS? | Greenhouse/Ashby/Lever/Workday/Other |
| MSA required? | If known |
| Typical fee tolerance | Based on comp level + market |
| Competition | Which other agencies are likely submitting? |
| Relationship status | New prospect / Warm / Active client / Past client |

### Section 6 — Recommendation

```markdown
## Should You Pursue This Client?

**Verdict**: [YES / WORTH A SHOT / PASS]

**Why**: [2-3 sentences with specific reasoning]

**Estimated value**: [Fee per placement × expected placements per year]

**Next action**: [Specific — e.g., "Email the Head of TA on LinkedIn" or
"Submit a candidate to their open Senior MLE req to start the relationship"]
```

## Post-Research (Mandatory)

1. Save the brief to `reports/research-{company-slug}-{YYYY-MM-DD}.md`
2. If the company isn't in `config/portals.yml`, offer to add it
3. If a careers URL was discovered, save it to portals.yml
4. Update `data/pipeline.md` if the company has reqs worth tracking
