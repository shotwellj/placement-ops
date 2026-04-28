# Shadow AI Detection — Product Requirements Document

**Author:** Jason Shotwell
**Date:** April 28, 2026
**Status:** Draft v0.1
**Filed:** April 28, 2026 morning session

---

## Problem Statement

Recruiters and hiring managers are pasting resumes, candidate notes, and
evaluation criteria into ChatGPT, Claude, Gemini, and other LLMs to generate
screening decisions — with zero audit trail, no bias controls, and no
compliance documentation. This "shadow AI" usage happens outside sanctioned
tools and leaves organizations exposed to regulatory liability under NYC
LL144, Illinois HB 3773, California FEHA, Colorado CAIA, and EU AI Act
Annex III (high-risk hiring classification).

No existing compliance tool detects this. Every competitor focuses on
scanning code or auditing deployed models. Nobody is scanning for humans
using AI informally in hiring workflows.

## Success Metrics

- 5 enterprise design partners running pilots within 90 days of MVP launch
- 50 organizations on waitlist before first line of code ships (validated demand)
- $10K MRR within 6 months of GA launch
- Detection accuracy: 85%+ precision on AI-generated evaluation language,
  less than 10% false positive rate

## User Stories

As an **HR Compliance Officer**, I want to know which recruiters on my team
are using unsanctioned AI tools to evaluate candidates so that I can ensure
we meet NYC LL144 and Illinois HB 3773 requirements before an audit.

As a **Chief People Officer**, I want a dashboard showing AI usage patterns
across my recruiting team so that I can make informed decisions about which
AI tools to formally adopt and govern.

As a **Recruiter**, I want clear guidance on which AI tools are approved
and which are not so that I can use AI to be more productive without
putting myself or the company at legal risk.

As a **Legal/GRC Analyst**, I want evidence that our hiring process has
controls against unsanctioned AI usage so that I can include this in our
compliance documentation.

## Market Sizing

**TAM:** $2.8B — Global HR compliance software market intersected with
AI governance ($4.3B by 2030).

**SAM:** $340M — US and EU enterprises with 500+ employees using AI in
hiring workflows, subject to at least one hiring AI regulation.
Approximately 12,000 companies.

**SOM (12 months):** $600K-$1.2M — 50-100 mid-market companies at $1K/month
average, acquired through compliance urgency around August 2026 EU AI Act
deadline and active NYC LL144 enforcement.

**Why now:** NYC LL144 enforcement fines started 2024. Illinois HB 3773
live January 2026. Colorado CAIA live June 2026. EU AI Act high-risk
deadline August 2026. Companies are getting fined NOW and the window for
panic-buying solutions is open.

## Competitive Landscape

| Competitor | What They Do | What They Don't Do |
|---|---|---|
| Credo AI | Model governance platform | No detection of unsanctioned AI in hiring |
| Holistic AI | Bias auditing for deployed models | Only audits sanctioned tools |
| HireVue | AI interviewing with bias tools | Only governs their own tool |
| Pymetrics/Harver | Assessment platforms with fairness | Closed ecosystem, no cross-tool visibility |
| OneTrust | Privacy/GRC expanding to AI | Enterprise-heavy, no hiring-specific layer |
| AIR Blackbox | Code compliance scanner | Currently scans code, not hiring workflows |

**The gap:** Every competitor assumes AI usage happens through sanctioned,
deployed tools. Nobody handles the recruiter who pastes 50 resumes into
ChatGPT and writes "not a culture fit" based on the output. This is the
undefended attack surface.

**Positioning:** "For HR Compliance teams who need their organization's AI
usage in hiring to be audit-ready, this product generates the documentation
auditors require — finding AI-generated evaluation language in your ATS and
producing the audit trail your recruiters' decisions need to be defensible
under LL144, HB 3773, FEHA, CAIA, and the EU AI Act."

## Scope

### IN SCOPE (MVP)

1. **ATS Integration Scanner** — Connect to Greenhouse, Lever, Workday,
   iCIMS via API. Scan recruiter notes, screening feedback, and evaluation
   comments for AI-generated language patterns.

2. **AI-Generated Language Detector** — NLP classifier trained to
   distinguish human-written recruiter notes from AI-generated text.
   Key signals:
   - Formulaic evaluation structure
   - Uniform paragraph lengths across many candidates
   - Vocabulary sophistication inconsistent with the recruiter's historical writing
   - Hedging patterns typical of LLMs
   - Identical phrasing across multiple candidate evaluations

3. **Audit Gap Analyzer** — Flag decisions where the time between resume
   receipt and screening decision is too short for human review (e.g., 200
   resumes scored in 3 minutes). Cross-reference with ATS activity logs.

4. **Compliance Risk Dashboard** — Per-recruiter and per-team view showing:
   - Likelihood of AI-generated evaluations (confidence score)
   - Decisions with suspicious timing gaps
   - Trend over time
   - Regulatory exposure summary (which laws apply based on candidate locations)

5. **Compliance Evidence Export** — Generate PDF reports documenting AI
   usage detection results for regulatory audits. Maps findings to specific
   regulations (LL144, HB 3773, FEHA, CAIA, EU AI Act).

### OUT OF SCOPE (v1)

- Browser extension monitoring (privacy concerns, requires MDM deployment)
- Email scanning for AI-assisted communications
- Real-time blocking of LLM usage (this is a documentation tool, not a blocker)
- Non-hiring AI usage detection (focus on hiring vertical only)
- Integration with HRIS systems (just ATS for MVP)
- On-premise deployment (cloud SaaS only for MVP)

## Technical Architecture

### High-Level System Design

```
                    +--------------------+
                    |   Compliance       |
                    |   Dashboard (Web)  |
                    +--------+-----------+
                             |
                    +--------v-----------+
                    |   API Gateway      |
                    |   (FastAPI)        |
                    +--------+-----------+
                             |
              +--------------+--------------+
              |              |              |
    +---------v----+ +------v-------+ +----v-----------+
    | ATS Connector| | AI Language  | | Audit Gap      |
    | Service      | | Detector     | | Analyzer       |
    +---------+----+ +------+-------+ +----+-----------+
              |              |              |
              |     +--------v---------+    |
              +---->| Analysis Engine  |<---+
                    | (Orchestrator)   |
                    +--------+---------+
                             |
                    +--------v---------+
                    | PostgreSQL       |
                    | (Findings Store) |
                    +------------------+
```

### Component Details

**1. ATS Connector Service** (SHARED with SourcingNav — see decision doc)
- OAuth2 integrations with Greenhouse, Lever, Workday Recruiting, iCIMS
- Polls for new screening events on configurable intervals (default: every 4 hours)
- Normalizes data into a common schema:
  `{recruiter_id, candidate_id, action_type, text_content, timestamp, source_ats}`
- Respects ATS API rate limits
- Stores raw data with encryption at rest

**2. AI Language Detector**
- Fine-tuned classifier (base: ModernBERT or DeBERTa-v3-large)
- Training data: 10K+ paired examples of human-written vs AI-generated recruiter notes
- Features: perplexity, burstiness, vocabulary fingerprint, structural analysis,
  hedging density, cross-candidate similarity
- Output: `{is_ai_generated: bool, confidence: float, evidence: [str]}`
- Target: 85%+ precision, 80%+ recall
- On-device option (ONNX export) for privacy-sensitive customers

**3. Audit Gap Analyzer**
- Compares timestamps: resume_received_at vs. screening_decision_at
- Flags physically impossible review speeds
- Cross-references with ATS login sessions
- Detects batch patterns
- Statistical model for "reasonable review time" based on role complexity

**4. Analysis Engine (Orchestrator)**
- Receives normalized events from ATS Connector
- Runs detector and analyzer in parallel
- Combines signals into a composite Compliance Risk Score per decision
- Applies regulatory mapping based on candidate's location, company HQ, job location
- Stores findings in PostgreSQL with full audit trail

**5. Dashboard**
- React frontend (or Next.js for SSR)
- Team-level: aggregate compliance risk by department, recruiter, time period
- Drill-down: individual flagged decisions with evidence
- Regulatory exposure view: map flagged decisions to specific laws
- Export: PDF compliance reports, CSV data exports
- Role-based access: Compliance Officer (full), HR Manager (team), Recruiter (self)

### Privacy and Security

- **No candidate PII in detection model.** Classifier only analyzes writing
  style, not candidate data. Names, emails, demographics stripped before analysis.
- **Encryption at rest** for all stored ATS data (AES-256).
- **SOC 2 Type II** target for GA launch (required for enterprise sales).
- **Data residency options** for EU customers (GDPR compliance).
- **On-device model option** for customers who cannot send ATS data to cloud.

### Tech Stack

| Component | Technology | Rationale |
|---|---|---|
| API | FastAPI (Python) | Jason's stack, async support, auto-docs |
| Database | PostgreSQL | Relational queries for compliance reporting |
| AI Detector | Fine-tuned ModernBERT | Best accuracy/speed tradeoff for text classification |
| Queue | Redis + Celery | Async processing of ATS events |
| Dashboard | Next.js + Tailwind | SSR for SEO, matches airblackbox.ai stack |
| Hosting | Vercel (frontend) + Railway/Fly.io (backend) | Cost-effective for MVP, scales later |
| ATS APIs | OAuth2 per vendor | Standard auth for Greenhouse, Lever, Workday |
