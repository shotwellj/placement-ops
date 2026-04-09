# Mode: Submit

> Generate a professional submission package to send to the client/hiring manager.

## Trigger

`/placement-ops submit`

## What This Produces

A complete submission package containing:

1. **Cover Memo** — One-page brief to the hiring manager explaining why this candidate fits
2. **Tailored CV** — ATS-optimized PDF (generated in prep mode)
3. **Match Scorecard** — Visual summary of the 8-dimension evaluation

## Input

The recruiter provides:
1. A **candidate** + **role** combination that has already been evaluated and prepped
2. OR a candidate file + JD (will trigger evaluate → prep → submit in sequence)

## Pre-Flight

1. Load the evaluation report from `reports/`
2. Load the prep output (tailored CV, match analysis)
3. Load `templates/submission-memo.md`
4. Load `templates/match-scorecard.md`
5. Load `config/profile.yml` for recruiter contact info

## Section 1 — Cover Memo

The cover memo is what you send to the hiring manager BEFORE or alongside the resume. It's your pitch.

### Structure

```markdown
# Candidate Submission: [Candidate First Name] for [Role Title]

**Submitted by**: [Recruiter Name], [Agency]
**Date**: [YYYY-MM-DD]
**Match Score**: [X.X / 5.0]

---

## Why This Candidate

[2-3 sentences. Lead with the single strongest match point. Connect the
candidate's specific experience to the role's biggest need. This is NOT
a generic "strong candidate with N years experience" — it's a specific
argument for why THIS person fits THIS role.]

## Key Qualifications

1. **[JD Requirement #1]**: [Specific evidence from resume — company, project, metric]
2. **[JD Requirement #2]**: [Specific evidence]
3. **[JD Requirement #3]**: [Specific evidence]

## What to Explore in the Interview

[1-2 sentences about what the HM should dig into. This shows you've
actually read the JD and understand what they're looking for, not just
keyword-matched.]

## Logistics

- **Current location**: [City, State]
- **Work authorization**: [Visa status]
- **Availability**: [Start date / notice period]
- **Compensation expectations**: [Range — only if client has shared budget]

---

*[Recruiter Name] | [Email] | [Phone]*
```

### Cover Memo Rules

- **Maximum one page.** Hiring managers don't read long submissions.
- **No fluff.** "Strong communicator" and "team player" say nothing. Use specifics.
- **Lead with the money shot.** The single most compelling match point goes first.
- **Acknowledge gaps proactively.** If there's a gap, address it briefly: "While Jane hasn't used Kubernetes directly, her 3 years managing Docker-based deployments and her AWS ECS experience demonstrate container orchestration fluency."
- **Don't oversell.** If the candidate is a 4.0, don't write a 5.0 cover memo. Credibility > hype.

## Section 2 — Match Scorecard

A clean, visual summary. Load from `templates/match-scorecard.md`:

```markdown
## Match Scorecard: [Candidate] → [Role]

| Dimension | Score | Assessment |
|-----------|-------|-----------|
| Technical Match | ⭐⭐⭐⭐ | 8/10 requirements met directly |
| Seniority Fit | ⭐⭐⭐⭐⭐ | Exact level match |
| Location/Remote | ⭐⭐⭐⭐⭐ | Remote role, remote candidate |
| Comp Alignment | ⭐⭐⭐½ | Within 10%, negotiable |
| Culture Signals | ⭐⭐⭐ | Neutral — data insufficient |
| Gap Severity | ⭐⭐⭐⭐ | Gaps are nice-to-haves |
| Presentation Risk | ⭐⭐⭐⭐ | Strong communicator |
| Fill Probability | ⭐⭐⭐⭐ | Competitive but realistic |

**Composite: 4.1 / 5.0 — SUBMIT**
```

## Section 3 — Package Assembly

Combine into a single output:

1. Cover memo → `output/{candidate-slug}-for-{company-slug}-memo.md`
2. Tailored CV → already at `output/{candidate-slug}-for-{company-slug}-{date}.pdf`
3. Match scorecard → `output/{candidate-slug}-for-{company-slug}-scorecard.md`

Optionally, if `package_format: pdf` in profile.yml, combine all three into a single PDF.

## Post-Submit (Mandatory)

1. Update `data/pipeline.md`:
   - Candidate status → "Submitted"
   - Date submitted
   - Notes: "Package sent to [HM name if known]"
2. Log the submission in `data/submissions.tsv`:
   ```
   2026-04-08\tJane Smith\tAnthropic\tSenior ML Engineer\tSubmitted\t4.1
   ```
3. Set a follow-up reminder: "Follow up with [client] in 3 business days if no response"
4. Print the package file paths so the recruiter can attach them to an email
