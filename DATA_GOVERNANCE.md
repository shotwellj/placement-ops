# SourcingNav Data Governance

**Document version:** 1.0
**Last reviewed:** April 22, 2026
**Next review:** October 22, 2026 (or on material system change)
**Owner:** Jason Shotwell, Founder

This document fulfills EU AI Act Article 10 (Data and data governance)
and GDPR Article 30 (Records of processing activities). It describes
how SourcingNav collects, stores, processes, retains, and deletes
data, and the controls applied at each stage.

## 1. Data sources

SourcingNav processes data from four sources:

1. **Recruiter-provided.** Job descriptions pasted into `/app/`
   intake, candidate notes, pipeline metadata, rejection reasons,
   placement outcomes.
2. **Candidate-provided.** Resume text, LinkedIn profile text, email
   addresses, optional self-identified demographic data.
3. **AI-generated.** Parsed JD structure, skill extractions,
   evaluation scores, outreach drafts, explanation text.
4. **System-generated.** Timestamps, audit events, HMAC chains,
   model version snapshots.

## 2. Legal basis for processing

SourcingNav processes personal data under **GDPR Article 6(1)(f)
legitimate interest**. The legitimate interest is the operation of a
recruiting platform that helps employers fill roles and helps workers
find employment, which is a socially and economically beneficial
activity.

We have conducted a balancing test: the interest in processing is
outweighed neither by the impact on data subjects nor by their
reasonable expectations. Candidates whose profiles are processed by
recruiters have a reasonable expectation that their public
professional information may be reviewed for job matching purposes.

Where consent is required for a specific processing purpose (e.g.,
voluntary self-identification of protected attributes for bias
monitoring), we use GDPR Article 6(1)(a) consent with explicit opt-in.

## 3. Data quality controls

AI output quality is controlled at three stages:

1. **Prompt-level.** `JD_PARSER_PROMPT` and `CANDIDATE_EVAL_PROMPT`
   include explicit instructions on required fields, format, and
   severity tagging. `canonical_skills` output is constrained to
   clean 2-5 word skill names.
2. **Parse-level.** `parse_json_strict` rejects malformed AI output.
   Invalid responses fall through to error states, not silent
   corruption.
3. **Taxonomy-level.** Skill names are resolved against a canonical
   taxonomy before use in scoring. Unresolved names preserved in
   `raw_skill_text` for future review (see Phase B2).

## 4. Retention policy

Retention is tiered by data subject status:

| Data subject type | Active state | Retention | After retention |
|-------------------|--------------|-----------|-----------------|
| Active candidate | In open pipeline | Indefinite | N/A while active |
| Closed candidate | Req closed, not placed | 2 years | Anonymize per Recital 26 |
| Placed candidate | Placement completed | 7 years | Anonymize |
| Recruiter user | Account active | Indefinite | 90 days post-deletion request |
| Audit events | N/A | 7 years | Cryptographic integrity only |
| Model versions | N/A | Indefinite | Required for historical reproducibility |

Rationale:
- **2 years for closed candidates** reflects typical recruitment
  cycle length (most candidates re-enter a search within 18-24
  months).
- **7 years for placed candidates** aligns with SOC 2 audit
  retention and typical wage-and-hour statute of limitations.
- **7 years for audit events** meets EU AI Act Article 12 record-
  keeping requirements for high-risk systems.
- **Indefinite for model versions** is required to reproduce past
  decisions for dispute investigation and regulatory review.

## 5. Anonymization procedure

Per GDPR Recital 26, anonymization removes all identifiers that could
reasonably link data back to a natural person. When a retention
period expires:

1. `data_subjects.anonymized_at` timestamp set
2. PII fields cleared: `candidates.name`, `candidates.email`,
   `candidates.resume_text`, `candidates.linkedin_url`
3. Skill data preserved: `candidate_skills` rows kept for calibration
   and taxonomy training (no identity linkage remains)
4. Audit events preserve hash references only; raw inputs/outputs
   never persisted in cleartext

Anonymization is irreversible and pseudonymization is explicitly not
sufficient for our retention policy.

## 6. Data subject rights (GDPR Articles 15-22)

Data subjects can exercise the following rights:

- **Access (Art 15):** export all data held on them in JSON format
- **Rectification (Art 16):** correct inaccurate personal data
- **Erasure (Art 17):** trigger immediate anonymization (subject to
  the 7-year audit retention exception for placed candidates)
- **Restriction (Art 18):** suspend processing while a complaint is
  reviewed
- **Portability (Art 20):** receive exported data in machine-readable
  format
- **Object (Art 21):** opt out of specific processing purposes

Requests are logged in `data_subject_requests` with a 30-day response
deadline. Automated deletion runs nightly to process approved
requests.

**Current state:** The table structure supports these rights
end-to-end. User-facing request forms on `/app/` are Phase B work.
Until then, requests sent to info@nostalgicskinco.com are processed
manually via direct database operation, logged as audit events.

## 7. Cross-border data transfer

Current production infrastructure:
- **Database (Turso):** AWS us-west-2 (primary)
- **Compute (Vercel):** Global edge network, primary region iad1
- **AI inference (Together.ai, BYOK):** US-based compute
- **Email (Resend):** US-based

No production data is currently stored in EU regions. For EU-based
candidates, processing falls under GDPR extraterritorial scope
(Article 3(2)). Until EU-region hosting is established, users are
notified at intake that data will be processed in the US under
standard contractual clauses where applicable.

## 8. Data Protection Impact Assessment (DPIA)

A DPIA per GDPR Article 35 is required because SourcingNav performs
systematic evaluation of natural persons based on automated
processing (candidate scoring). A full DPIA will be completed before
the Company SKU launches, since that expands processing from external
sourcing into internal employee data.

Current DPIA status: **Phase A covers risk identification
(RISK_ASSESSMENT.md). Phase B will complete formal DPIA including
proportionality analysis, consultation with affected data subjects,
and mitigation effectiveness review.**

## 9. Breach notification process

In the event of a personal data breach:

1. **Detection.** Anomalies surfaced via `login_attempts` monitoring,
   audit chain integrity checks, or external report.
2. **Assessment.** Within 24 hours: determine scope, data categories
   affected, approximate number of data subjects.
3. **Containment.** Revoke compromised credentials, rotate keys,
   patch vulnerability.
4. **Notification.**
   - Regulatory: within 72 hours per GDPR Article 33 (data
     protection authorities in affected jurisdictions)
   - Data subjects: without undue delay where high risk per Article
     34
5. **Documentation.** Breach recorded in audit chain with full
   timeline and remediation steps.

**Current state:** Process documented. Detection monitoring exists
for auth anomalies (`login_attempts`). Automated breach detection
tooling is Phase B+ work.

## 10. Records of processing activities (GDPR Article 30)

**Controller:** SourcingNav (Jason Shotwell, sole proprietor),
info@nostalgicskinco.com

**Processing activities:**

| Activity | Purpose | Data categories | Subjects | Legal basis | Retention |
|----------|---------|-----------------|----------|-------------|-----------|
| JD parsing | Extract structured requirements from recruiter input | JD text, skill lists | Recruiter | Art 6(1)(f) | Indefinite while account active |
| Candidate evaluation | Score candidate fit for specific reqs | Resume, LinkedIn profile, contact info | Candidate | Art 6(1)(f) | Tiered (see section 4) |
| Outreach generation | Draft personalized messages | Candidate context, JD context | Candidate | Art 6(1)(f) | Until sent + 90 days |
| Audit logging | Tamper-evident record of all automated decisions | Hashes only (no raw content) | All subjects | Art 6(1)(c) legal obligation (EU AI Act Art 12) | 7 years |
| Bias monitoring | Disparate impact analysis | Voluntary demographic data | Candidate (opt-in) | Art 6(1)(a) consent | Until consent withdrawn |

**Processors engaged:**
- Turso (database)
- Vercel (hosting)
- Resend (email)
- Together.ai (AI inference, customer-keyed BYOK)
- Anthropic (AI inference, customer-keyed BYOK)

Standard contractual clauses apply where processing occurs outside
the data subject's jurisdiction.

## 11. Change log

- 2026-04-22 v1.0 — Initial data governance document created as part
  of Phase A4 closure

## 12. References

- GDPR (Regulation 2016/679)
- EU AI Act (Regulation 2024/1689), Article 10
- SourcingNav schema:
  `scripts/migration_003_compliance_and_taxonomy.sql`
- SourcingNav compliance helpers: `api/_compliance.py`
- Risk assessment: `RISK_ASSESSMENT.md`
