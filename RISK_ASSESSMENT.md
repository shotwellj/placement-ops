# SourcingNav Risk Assessment

**Document version:** 1.0
**Last reviewed:** April 22, 2026
**Next review:** October 22, 2026 (or on material system change)
**Owner:** Jason Shotwell, Founder

This document fulfills EU AI Act Article 9 (Risk Management System)
requirements for SourcingNav. It identifies known risks of the
AI-powered recruiting platform, the mitigations we have in place,
residual risks accepted, and the governance process for reassessing.

## 1. System classification

Under EU AI Act Article 6 and Annex III, SourcingNav qualifies as a
**high-risk AI system**. Specifically:

- **Annex III, Category 4(a):** AI systems used for recruitment or
  selection of natural persons, including placing targeted job
  advertisements, analyzing and filtering job applications, and
  evaluating candidates.
- **Annex III, Category 4(b):** AI systems used to make decisions
  affecting terms of work-related relationships, promotion, or
  termination, and for task allocation based on individual behavior
  or personal traits.

SourcingNav's candidate evaluation, matching engine, and fit scoring
fall under 4(a). The future Company SKU performance and retention
features will fall under 4(b). We accept high-risk classification and
design all features to meet Articles 9-15 requirements.

## 2. Identified risks

Risks are categorized and scored on likelihood (L) and impact (I),
each 1-5. Priority = L x I.

### 2.1 Discriminatory outcomes (L=3, I=5, Priority=15)

**Risk:** AI-generated fit scores or candidate rankings produce
disparate impact on protected classes (race, gender, age, disability,
national origin). Source: training data bias in underlying LLMs,
skill adjacency weights that inadvertently correlate with protected
attributes, or rejection patterns that encode historical bias.

**Mitigations in place:**
- `protected_attributes` table stores self-identified demographic data
  on a voluntary, opt-in basis only. Access-restricted at the app
  layer. Never joined into scoring queries.
- Scoring logic operates exclusively on skill and competency signals.
  Protected attributes are never inputs to `submission_dimensions`.
- `audit_events` tamper-evident chain records every automated decision
  with inputs_hash, outputs_hash, and model_version_id. Enables
  retrospective disparate impact analysis.
- `decision_explanations` table provides plain-English reasoning per
  decision per EU AI Act Article 13 and NYC Local Law 144.
- Phase B will add disparate impact monitoring dashboards that alert
  recruiters and companies when scoring patterns correlate with
  protected attributes.

**Residual risk accepted:** Until Phase B monitoring dashboards ship,
disparate impact detection requires manual query of audit tables.
Mitigated by the fact that all decisions are recorded and queryable.

### 2.2 Training data drift and stale taxonomy (L=4, I=3, Priority=12)

**Risk:** Skill taxonomy becomes out of date. New roles (Talent
Engineer, Prompt Engineer, AI Engineer) and hybrid roles (Data
Engineer to Full-Stack) are not captured, leading to false negatives
and biased scoring toward older role archetypes.

**Mitigations in place:**
- `req_skills.raw_skill_text` and `candidate_skills.raw_skill_text`
  preserve the original AI-emitted skill names even when they do not
  resolve to a canonical taxonomy entry. No data is lost.
- Unresolved skill warnings logged during every seed run.
- Taxonomy split into domain-specific YAML files
  (`skills_data_ml.yml`, `skills_hardware_semiconductor.yml`, etc.).
  New files auto-discovered by the seed script. Expansion is a
  single-commit operation.
- Phase B2 will automate unresolved skill promotion (system surfaces
  new skill candidates after N occurrences across users).

**Residual risk accepted:** Until Phase B2 ships, taxonomy expansion
is manual. Scheduled review every 90 days.

### 2.3 Over-reliance on automated decisions (L=3, I=4, Priority=12)

**Risk:** Recruiters treat fit scores as ground truth instead of
decision support. A 72% score gets interpreted as "submit" or
"reject" without human review of the evidence.

**Mitigations in place:**
- Every evaluation includes `blocker_assessment`,
  `preferred_assessment`, `strengths`, `risks_to_probe`, and
  `interview_questions` in the output. The score is one data point
  among many.
- `decision_explanations.human_review_status` field defaults to
  `not_requested` but is set to `requested` or `completed` when a
  recruiter flags for review. Records human oversight.
- UI presents scores alongside the full evaluation, not in isolation.
- No automatic candidate rejection. A recruiter must explicitly mark
  a candidate as rejected; the system never decides unilaterally.

**Residual risk accepted:** We cannot force human review on every
decision. Mitigated by UX design that surfaces evidence and by the
audit trail that records when scores were accepted without review.

### 2.4 Data breach or unauthorized access (L=2, I=5, Priority=10)

**Risk:** Candidate PII (name, email, resume text, contact info)
exfiltrated through compromised credentials, injection attacks, or
insider access.

**Mitigations in place:**
- All data stored in Turso (encrypted at rest, TLS in transit).
- API keys encrypted at rest via Fernet (`BYOK_ENCRYPTION_KEY`).
- Session tokens hashed (SHA-256) before storage. Raw tokens never
  persisted.
- Magic-link authentication with 15-minute expiry. No password
  storage.
- Rate limiting on auth endpoints: 5 attempts/email/hour, 20/IP/hour.
- `login_attempts` table tracks all auth attempts for anomaly
  detection.
- `data_subjects` table enables per-subject data export and
  anonymization for GDPR Article 15-22 requests.

**Residual risk accepted:** Third-party dependency risk (Turso,
Vercel, Resend, Together.ai). Mitigated by vendor selection and BYOK
model that keeps AI provider keys under user control.

### 2.5 Adversarial input / prompt injection (L=2, I=3, Priority=6)

**Risk:** Malicious JDs or candidate profiles contain prompt
injection attacks that manipulate scoring or extract system prompts.

**Mitigations in place:**
- AI calls use temperature=0.3 and strict JSON output format.
- JSON parsing is defensive (`parse_json_strict`) and rejects
  malformed output.
- No system prompts contain user-controllable templating placeholders
  beyond `{jd}`, `{parsed_jd}`, `{candidate_text}`. No command
  injection surface.
- User input is hashed before storage in `audit_events.inputs_hash`,
  so injection payloads do not persist in audit logs.

**Residual risk accepted:** LLM-native injection resistance is
imperfect. Install of `air-langchain-trust` (AIR Blackbox trust
layer) planned as Phase A hardening follow-up to add runtime
injection scanning.

### 2.6 Regulatory drift (L=4, I=3, Priority=12)

**Risk:** EU AI Act Annex III classifications shift, GDPR
interpretations evolve, new state-level US AI employment laws emerge
(beyond current NYC LL144, Colorado AI Act, California Fair Chance
Act). System fails to comply with regulations that did not exist at
build time.

**Mitigations in place:**
- Compliance framework scope is documented and reviewed quarterly
  (see ROADMAP.md and this file).
- `model_versions` table snapshots every prompt+taxonomy state, so
  past decisions remain reproducible even when the rules change.
- Audit trail is tamper-evident via HMAC chain. Can demonstrate prior
  compliance state at any historical point.
- AIR Blackbox compliance scan runs as part of the development
  workflow (see `air-blackbox comply --scan .`).

**Residual risk accepted:** Novel regulations require manual
response. Mitigated by the architecture's separation of scoring
logic (stable) from prompt content (updateable) and taxonomy
(updateable).

### 2.7 Non-solicit violation / recommending the hiring company as source (L=3, I=5, Priority=15)

**Risk:** The AI-generated JD parse or Boolean output recommends
sourcing candidates from the hiring company itself. This is a
non-solicit violation in most agency and retained recruiting
agreements, a legal risk in some jurisdictions, and professional
malpractice regardless of contract terms. A recruiter acting on the
suggestion would expose themselves to contract termination, legal
action, and reputation damage.

**How it was discovered:** First end-to-end intake test with a real
Qualcomm JD on 2026-04-23. The AI returned "Target engineers with
Qualcomm, Broadcom, or MediaTek device driver experience" as the top
recommended first move, and listed Qualcomm itself in Tier 1 Direct
Competitors. The exact failure mode this risk category protects
against.

**Mitigations in place:**
- `JD_PARSER_PROMPT` contains a dedicated CRITICAL RULE block named
  "NEVER RECOMMEND POACHING THE HIRING COMPANY" that enumerates every
  output field (recommended_first_moves, poaching_targets,
  top_hiring_companies, talent_hotspots, sourcing_strategy) and
  instructs the model to treat the hiring company as a filter, not
  a source.
- `BOOLEAN_BUILDER_PROMPT` has an explicit exclusion rule preventing
  the hiring company from appearing in tier_1_direct_competitors or
  tier_2_adjacent company clusters.
- Both prompts are versioned via `model_versions`. The commit that
  introduced the fix is 17e7731 on 2026-04-23. Every decision made
  before that commit is traceable to the prior prompt hash.

**Residual risk accepted:** The AI may still occasionally violate the
rule on edge cases (abbreviated company names, subsidiaries, parent
companies). Planned follow-up:
- Phase B1 will add a code-level post-filter that strips the hiring
  company name from all output arrays before display, as defense in
  depth against prompt non-compliance.
- Phase B2 will add a canonical mapping of company aliases
  (e.g. "QCOM" → "Qualcomm", "ATVI" → "Activision Blizzard") so the
  filter catches abbreviations.

## 3. Risks deferred to future phases

| Phase | Risk addressed | Acceptance criteria |
|-------|----------------|---------------------|
| A4 | Documentation completeness | This file + DATA_GOVERNANCE.md + README update |
| B1 | Calibration-induced bias | Adjacency adjustments monitored for disparate impact |
| B2 | Stale taxonomy | Unresolved skill promotion automated |
| B3 | New-role blindness | Role archetype discovery surfaces emerging profiles |
| B4 | Skill combination blindness | Mesh co-occurrence informs matching |
| C | Kill switch / emergency stop | Explicit stop mechanism implemented |
| C+ | Runtime injection scanning | Trust layer integrated |

## 4. Governance

**Review cadence:**
- Quarterly review of this document by the system owner
- Immediate review on any material change (new model, new prompt,
  new regulation)
- Annual external compliance review (planned once Phase F customer
  base justifies it)

**Change log:**
- 2026-04-22 v1.0 — Initial risk assessment created as part of Phase
  A4 closure

## 5. References

- EU AI Act (Regulation 2024/1689), Articles 9-15
- GDPR (Regulation 2016/679), Articles 5, 6, 13, 15-22, 30
- California Consumer Privacy Act (CCPA)
- NYC Local Law 144 (Automated Employment Decision Tools)
- Colorado AI Act (SB 24-205)
- California Fair Chance Act
- AIR Blackbox Compliance Scan: see `AIR_BLACKBOX_SCAN.md` (when
  created)
- SourcingNav schema: `scripts/migration_003_compliance_and_taxonomy.sql`
- SourcingNav compliance helpers: `api/_compliance.py`
