-- Migration 003: Compliance-aware taxonomy + brain foundation
-- ==========================================================
-- Run AFTER migration_002_sessions.sql.
-- Idempotent via IF NOT EXISTS. Safe to re-run.
--
-- Defaults (Jason's decisions, April 2026):
--   Retention: active indefinite, closed 2yr then anonymize, placed 7yr
--   Consent model: legitimate interest (GDPR Art 6(1)(f))
--   Bias monitoring: voluntary self-ID only
--   Deletion: anonymize (GDPR Recital 26 irreversible anonymization)
--
-- Framework coverage:
--   EU AI Act Articles 9, 10, 11, 12, 13, 14, 15
--   GDPR Articles 5, 6, 13, 15-22, 30
--   CCPA, EEOC, NYC Local Law 144, Colorado AI Act, SOC 2, BIPA
-- ==========================================================

-- ----- BRAIN LAYER -----

CREATE TABLE IF NOT EXISTS skills (
  id             TEXT PRIMARY KEY,
  canonical_name TEXT UNIQUE NOT NULL,
  category       TEXT NOT NULL,
  aliases_json   TEXT,
  description    TEXT,
  weight         TEXT NOT NULL DEFAULT 'medium',
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_canonical_name ON skills(canonical_name);

CREATE TABLE IF NOT EXISTS skill_adjacencies (
  id           TEXT PRIMARY KEY,
  skill_id     TEXT NOT NULL,
  adjacent_id  TEXT NOT NULL,
  weight       REAL NOT NULL DEFAULT 0.6,
  source       TEXT NOT NULL DEFAULT 'taxonomy',
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (skill_id)    REFERENCES skills(id) ON DELETE CASCADE,
  FOREIGN KEY (adjacent_id) REFERENCES skills(id) ON DELETE CASCADE,
  UNIQUE(skill_id, adjacent_id)
);
CREATE INDEX IF NOT EXISTS idx_adj_skill ON skill_adjacencies(skill_id);

CREATE TABLE IF NOT EXISTS competencies (
  id              TEXT PRIMARY KEY,
  canonical_name  TEXT UNIQUE NOT NULL,
  category        TEXT NOT NULL,
  archetypes_json TEXT,
  signals_json    TEXT,
  levels_json     TEXT,
  weight          TEXT NOT NULL DEFAULT 'medium',
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_comp_category ON competencies(category);

CREATE TABLE IF NOT EXISTS req_skills (
  id             TEXT PRIMARY KEY,
  req_id         TEXT NOT NULL,
  skill_id       TEXT,
  raw_skill_text TEXT NOT NULL,
  importance     TEXT NOT NULL DEFAULT 'preferred',
  years_min      INTEGER,
  rationale      TEXT,
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (req_id)   REFERENCES requisitions(id) ON DELETE CASCADE,
  FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_req_skills_req ON req_skills(req_id);
CREATE INDEX IF NOT EXISTS idx_req_skills_skill ON req_skills(skill_id);

CREATE TABLE IF NOT EXISTS candidate_skills (
  id             TEXT PRIMARY KEY,
  candidate_id   TEXT NOT NULL,
  skill_id       TEXT,
  raw_skill_text TEXT NOT NULL,
  evidence       TEXT,
  recency        TEXT NOT NULL DEFAULT 'current',
  depth          TEXT NOT NULL DEFAULT 'mentioned',
  confidence     REAL NOT NULL DEFAULT 0.5,
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
  FOREIGN KEY (skill_id)     REFERENCES skills(id)     ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_skills_candidate ON candidate_skills(candidate_id);
CREATE INDEX IF NOT EXISTS idx_cand_skills_skill ON candidate_skills(skill_id);

CREATE TABLE IF NOT EXISTS submission_dimensions (
  id                   TEXT PRIMARY KEY,
  submission_id        TEXT NOT NULL,
  technical_match      REAL,
  seniority_fit        REAL,
  location_alignment   REAL,
  comp_alignment       REAL,
  culture_signals      REAL,
  gap_severity         REAL,
  presentation_risk    REAL,
  fill_probability     REAL,
  composite_score      REAL,
  blocker_count        INTEGER DEFAULT 0,
  match_breakdown_json TEXT,
  created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE,
  UNIQUE(submission_id)
);

CREATE TABLE IF NOT EXISTS comp_observations (
  id                TEXT PRIMARY KEY,
  source_type       TEXT NOT NULL,
  source_entity_id  TEXT,
  amount_min        INTEGER,
  amount_max        INTEGER,
  currency          TEXT NOT NULL DEFAULT 'USD',
  comp_type         TEXT NOT NULL DEFAULT 'base',
  equity_notes      TEXT,
  location          TEXT,
  level             TEXT,
  archetype         TEXT,
  observed_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id           TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_comp_source ON comp_observations(source_type, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_comp_location_level ON comp_observations(location, level);
CREATE INDEX IF NOT EXISTS idx_comp_observed ON comp_observations(observed_at);

-- ----- COMPLIANCE LAYER -----

CREATE TABLE IF NOT EXISTS data_subjects (
  id                TEXT PRIMARY KEY,
  subject_type      TEXT NOT NULL,
  linked_entity_id  TEXT,
  legal_basis       TEXT NOT NULL DEFAULT 'legitimate_interest',
  jurisdiction      TEXT,
  consent_json      TEXT,
  retention_policy  TEXT NOT NULL DEFAULT 'active_indefinite_closed_2yr_placed_7yr',
  anonymized_at     TIMESTAMP,
  deleted_at        TIMESTAMP,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_subjects_linked ON data_subjects(subject_type, linked_entity_id);
CREATE INDEX IF NOT EXISTS idx_subjects_jurisdiction ON data_subjects(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_subjects_retention ON data_subjects(anonymized_at, deleted_at);

CREATE TABLE IF NOT EXISTS data_subject_requests (
  id                 TEXT PRIMARY KEY,
  subject_id         TEXT NOT NULL,
  request_type       TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'pending',
  requested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at       TIMESTAMP,
  deadline_at        TIMESTAMP,
  response_notes     TEXT,
  requester_email    TEXT,
  handled_by_user_id TEXT,
  FOREIGN KEY (subject_id)         REFERENCES data_subjects(id) ON DELETE CASCADE,
  FOREIGN KEY (handled_by_user_id) REFERENCES users(id)         ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_dsr_subject ON data_subject_requests(subject_id);
CREATE INDEX IF NOT EXISTS idx_dsr_status ON data_subject_requests(status, deadline_at);

CREATE TABLE IF NOT EXISTS audit_events (
  id                TEXT PRIMARY KEY,
  seq               INTEGER NOT NULL,
  event_type        TEXT NOT NULL,
  actor_user_id     TEXT,
  actor_ip          TEXT,
  subject_id        TEXT,
  entity_type       TEXT,
  entity_id         TEXT,
  action            TEXT NOT NULL,
  inputs_hash       TEXT,
  outputs_hash      TEXT,
  model_version_id  TEXT,
  confidence_score  REAL,
  hmac_chain        TEXT NOT NULL,
  prev_hmac         TEXT,
  occurred_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (actor_user_id) REFERENCES users(id)         ON DELETE SET NULL,
  FOREIGN KEY (subject_id)    REFERENCES data_subjects(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_events(seq);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit_events(subject_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_events(occurred_at);

CREATE TABLE IF NOT EXISTS model_versions (
  id                TEXT PRIMARY KEY,
  version_tag       TEXT UNIQUE NOT NULL,
  prompt_name       TEXT NOT NULL,
  prompt_hash       TEXT NOT NULL,
  model_provider    TEXT NOT NULL,
  model_name        TEXT NOT NULL,
  taxonomy_snapshot TEXT,
  git_commit_sha    TEXT,
  active            INTEGER NOT NULL DEFAULT 1,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  retired_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_model_versions_active ON model_versions(active, prompt_name);

CREATE TABLE IF NOT EXISTS decision_explanations (
  id                  TEXT PRIMARY KEY,
  audit_event_id      TEXT NOT NULL,
  subject_id          TEXT,
  decision_type       TEXT NOT NULL,
  decision_outcome    TEXT,
  top_factors_json    TEXT,
  plain_english       TEXT,
  human_review_status TEXT DEFAULT 'not_requested',
  human_reviewer_id   TEXT,
  human_review_notes  TEXT,
  human_review_at     TIMESTAMP,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (audit_event_id)    REFERENCES audit_events(id)  ON DELETE CASCADE,
  FOREIGN KEY (subject_id)        REFERENCES data_subjects(id) ON DELETE SET NULL,
  FOREIGN KEY (human_reviewer_id) REFERENCES users(id)         ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_explain_audit ON decision_explanations(audit_event_id);
CREATE INDEX IF NOT EXISTS idx_explain_subject ON decision_explanations(subject_id);
CREATE INDEX IF NOT EXISTS idx_explain_review ON decision_explanations(human_review_status);

CREATE TABLE IF NOT EXISTS protected_attributes (
  id                TEXT PRIMARY KEY,
  subject_id        TEXT NOT NULL,
  gender            TEXT,
  race_ethnicity    TEXT,
  age_range         TEXT,
  disability_status TEXT,
  veteran_status    TEXT,
  lgbtq_status      TEXT,
  source            TEXT NOT NULL DEFAULT 'self_reported',
  consent_given     INTEGER NOT NULL DEFAULT 0,
  jurisdiction      TEXT,
  provided_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (subject_id) REFERENCES data_subjects(id) ON DELETE CASCADE,
  UNIQUE(subject_id)
);
CREATE INDEX IF NOT EXISTS idx_protected_subject ON protected_attributes(subject_id);

-- Migration tracking
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (3, 'compliance_and_taxonomy');
