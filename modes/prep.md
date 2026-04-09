# Mode: Prep

> Generate a tailored resume, match analysis, and interview prep for a candidate being submitted to a specific role.

## Trigger

`/placement-ops prep`

## Input

The recruiter provides:
1. A **candidate file** from `candidates/`
2. A **JD** (pasted or URL) — OR — a **report** from a previous `/placement-ops evaluate` run

If an evaluation report exists, use it. Don't re-evaluate from scratch.

## Pre-Flight

1. Load `modes/_shared.md`
2. Load `config/profile.yml`
3. Load the candidate file + resume
4. Load the evaluation report (if it exists in `reports/`)
5. Load `templates/cv-template.html` for PDF generation

## Section 1 — Resume Tailoring

### Keyword Extraction

Extract 15-20 keywords and phrases from the JD. Categorize them:

| Category | Keywords |
|----------|----------|
| Hard Skills | Python, PyTorch, distributed training, Kubernetes |
| Frameworks/Tools | MLflow, Airflow, dbt, Spark |
| Domain Terms | recommendation systems, A/B testing, real-time serving |
| Soft/Process | cross-functional, stakeholder management, mentorship |

### Rewriting Rules

For each section of the candidate's resume:

1. **Summary**: Rewrite using the JD's exact language. If the JD says "recommendation systems," don't write "personalization algorithms" — use their words.
2. **Experience bullets**: For the first bullet of each role, front-load a JD keyword. Reframe (don't fabricate) achievements using JD vocabulary.
3. **Skills section**: Mirror the JD's skill categories and ordering.
4. **Section headers**: Use standard ATS-parseable headers: Professional Summary, Work Experience, Education, Technical Skills.

### What You CANNOT Do

- Add skills the candidate doesn't have
- Inflate titles or seniority
- Fabricate metrics or achievements
- Change employment dates
- Add companies or roles that don't exist

### What You CAN Do

- Reword existing achievements using JD terminology
- Reorder bullets to lead with the most relevant experience
- Expand abbreviated descriptions with detail from the candidate's file
- Add context that makes existing experience more relevant (e.g., "built ML pipeline" → "designed and deployed production ML pipeline serving 10M+ predictions/day" IF the candidate's file supports this)

## Section 2 — ATS Optimization

The tailored resume MUST follow these rules for Applicant Tracking System parsing:

- Single-column layout (no sidebars, no two-column designs)
- Standard section headers (Professional Summary, Work Experience, Education, Technical Skills)
- All text must be selectable (never rasterized/image-based)
- No critical info in headers or footers
- Keywords distributed across: summary (3-5), first bullet of each role (1-2), skills section (all)
- No tables, text boxes, or graphics that break ATS parsing
- File format: PDF generated from HTML (not Word)

## Section 3 — Match Analysis

Generate a one-page match analysis the recruiter can use internally or share with the client:

```markdown
## Match Analysis: [Candidate] → [Company] — [Role]

### Composite Score: X.X / 5.0 — [STRONG SUBMIT / SUBMIT / MAYBE]

### Top Strengths (why this candidate fits)
1. [Specific strength mapped to JD requirement]
2. [Specific strength mapped to JD requirement]
3. [Specific strength mapped to JD requirement]

### Gaps & Mitigations
1. [Gap]: [Why it's not a dealbreaker] — [Mitigation strategy]
2. [Gap]: [Why it's not a dealbreaker] — [Mitigation strategy]

### Positioning Notes
- Lead with: [what to emphasize]
- Preemptively address: [what the HM will ask about]
- Don't mention: [what to leave for the interview to discover]
```

## Section 4 — Interview Prep

Generate 6-10 STAR+Reflection stories mapped to the JD's key requirements:

| # | JD Requirement | Story Title | Situation | Task | Action | Result | Reflection |
|---|---------------|-------------|-----------|------|--------|--------|------------|
| 1 | "Production ML systems" | "Rebuilt the recommendation engine at X Corp" | Legacy system, 30% accuracy | Redesign + deploy new model | Led 3-person team, used PyTorch + K8s | 45% accuracy improvement, 2x throughput | "Learned to prototype fast and iterate rather than design the perfect system upfront" |

**Reflection matters.** Junior candidates say what happened. Senior candidates say what they learned. The reflection column signals seniority to interviewers.

Also include:
- 3-5 questions the candidate should ask the interviewer (tailored to this company/role)
- Red flag questions the HM might ask, with suggested responses
- Which project from the resume to use as the deep-dive case study

## Section 5 — PDF Generation

1. Populate `templates/cv-template.html` with the tailored resume content
2. Run `node generate-pdf.mjs` to produce the PDF
3. Save to `output/{candidate-slug}-for-{company-slug}-{YYYY-MM-DD}.pdf`
4. Report: file path, page count (should be 1-2 pages), keyword coverage %

## Post-Prep (Mandatory)

1. Save the match analysis to the evaluation report in `reports/`
2. Save interview prep to `reports/{###}-{company-slug}-interview-prep.md`
3. Update `data/pipeline.md` — mark the candidate's status as "Prepped"
4. Tell the recruiter: "Run `/placement-ops submit` to generate the full submission package"
