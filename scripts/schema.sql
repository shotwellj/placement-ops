-- SourcingNav Talent Engine — Database Schema v1
-- Target: Turso (libSQL)

CREATE TABLE IF NOT EXISTS users (
  id              TEXT PRIMARY KEY,
  email           TEXT UNIQUE NOT NULL,
  mode            TEXT NOT NULL DEFAULT 'agency',
  plan            TEXT NOT NULL DEFAULT 'free',
  byok_provider   TEXT,
  byok_key_enc    TEXT,
  usage_intake    INTEGER NOT NULL DEFAULT 0,
  usage_eval      INTEGER NOT NULL DEFAULT 0,
  usage_outreach  INTEGER NOT NULL DEFAULT 0,
  usage_reset_at  TIMESTAMP,
  profile_json    TEXT,                       -- Free-form recruiter profile: display name, default meeting link, working hours, signature
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS organizations (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  org_type    TEXT NOT NULL DEFAULT 'client',
  logo_url    TEXT,
  domain      TEXT,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_orgs_user ON organizations(user_id);

CREATE TABLE IF NOT EXISTS requisitions (
  id                    TEXT PRIMARY KEY,
  org_id                TEXT NOT NULL,
  user_id               TEXT NOT NULL,
  title                 TEXT NOT NULL,
  jd_raw                TEXT NOT NULL,
  parsed_json           TEXT,
  boolean_strings_json  TEXT,
  competitive_intel_json TEXT,
  jamboard_json         TEXT,
  dei_strategy_json     TEXT,
  client_info_json      TEXT,                -- Free-form client/company info: contacts, prep notes, process map, perks
  status                TEXT NOT NULL DEFAULT 'open',
  priority              TEXT DEFAULT 'medium',
  fee_estimate          INTEGER,
  fee_actual            INTEGER,
  opened_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  closed_at             TIMESTAMP,
  updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reqs_user_status ON requisitions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_reqs_org ON requisitions(org_id);

CREATE TABLE IF NOT EXISTS candidates (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL,
  name              TEXT NOT NULL,
  email             TEXT,
  linkedin_url      TEXT,
  github_url        TEXT,
  current_title     TEXT,
  current_company   TEXT,
  resume_text       TEXT,
  skills_json       TEXT,
  source            TEXT,
  notes             TEXT,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_candidates_user ON candidates(user_id);

CREATE TABLE IF NOT EXISTS employees (
  id                 TEXT PRIMARY KEY,
  org_id             TEXT NOT NULL,
  name               TEXT NOT NULL,
  email              TEXT,
  role               TEXT,
  team               TEXT,
  level              TEXT,
  tenure_start       DATE,
  comp_base          INTEGER,
  comp_band_percentile INTEGER,
  last_1on1          TIMESTAMP,
  flight_risk_score  INTEGER DEFAULT 0,
  health_status      TEXT DEFAULT 'healthy',
  departed_at        TIMESTAMP,
  departure_reason   TEXT,
  created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_employees_org ON employees(org_id);
CREATE INDEX IF NOT EXISTS idx_employees_risk ON employees(flight_risk_score);

CREATE TABLE IF NOT EXISTS submissions (
  id                 TEXT PRIMARY KEY,
  req_id             TEXT NOT NULL,
  candidate_id       TEXT NOT NULL,
  taxonomy_score     REAL,
  ai_fit_score       INTEGER,
  composite_score    REAL,
  recommendation     TEXT,
  fit_analysis_json  TEXT,
  stage              TEXT NOT NULL DEFAULT 'sourced',
  close_probability  REAL,
  fee_estimate       INTEGER,
  days_active        INTEGER DEFAULT 0,
  created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  placed_at          TIMESTAMP,
  rejected_at        TIMESTAMP,
  updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (req_id) REFERENCES requisitions(id) ON DELETE CASCADE,
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_submissions_req ON submissions(req_id);
CREATE INDEX IF NOT EXISTS idx_submissions_stage ON submissions(stage);

CREATE TABLE IF NOT EXISTS outreach_messages (
  id                TEXT PRIMARY KEY,
  submission_id     TEXT NOT NULL,
  step_number       INTEGER NOT NULL DEFAULT 1,
  variant           TEXT DEFAULT 'A',
  style             TEXT,
  channel           TEXT,
  subject           TEXT,
  original_text     TEXT NOT NULL,
  edited_text       TEXT,
  sent_at           TIMESTAMP,
  opened_at         TIMESTAMP,
  replied_at        TIMESTAMP,
  detected_intent   TEXT,
  outcome           TEXT,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_outreach_submission ON outreach_messages(submission_id);

CREATE TABLE IF NOT EXISTS meetings (
  id                 TEXT PRIMARY KEY,
  submission_id      TEXT NOT NULL,
  scheduled_for      TIMESTAMP NOT NULL,
  duration_minutes   INTEGER DEFAULT 30,
  prep_brief_json    TEXT,                -- AI-generated prep notes
  google_event_id    TEXT,                -- null until Path B (Calendar integration)
  booking_source     TEXT,                -- 'in_app' | 'google_calendar' | 'manual'
  interview_type     TEXT,                -- 'phone_screen' | 'technical' | 'behavioral' | 'onsite'
  interviewer        TEXT,                -- free-text name or email of person conducting
  meeting_link       TEXT,                -- Zoom/Meet/Teams URL or dial-in
  notes              TEXT,
  outcome            TEXT,                -- 'completed' | 'cancelled_by_candidate' | 'cancelled_by_client' | 'no_show' | 'rescheduled'
  completed_at       TIMESTAMP,
  created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meetings_upcoming
  ON meetings(scheduled_for, completed_at);

CREATE TABLE IF NOT EXISTS signals (
  id            TEXT PRIMARY KEY,
  user_id       TEXT,
  org_id        TEXT,
  signal_type   TEXT NOT NULL,
  company_name  TEXT,
  payload_json  TEXT NOT NULL,
  confidence    REAL NOT NULL DEFAULT 0.5,
  detected_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actioned_at   TIMESTAMP,
  expires_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_user_type ON signals(user_id, signal_type);

CREATE TABLE IF NOT EXISTS recommendations (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  rec_type      TEXT NOT NULL,
  priority      INTEGER NOT NULL DEFAULT 50,
  title         TEXT NOT NULL,
  description   TEXT NOT NULL,
  action_label  TEXT,
  action_url    TEXT,
  entity_type   TEXT,
  entity_id     TEXT,
  signal_ids    TEXT,
  dollar_impact INTEGER,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  dismissed_at  TIMESTAMP,
  actioned_at   TIMESTAMP,
  expires_at    TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_recs_user_active ON recommendations(user_id, dismissed_at, actioned_at);

CREATE TABLE IF NOT EXISTS calibration_events (
  id               TEXT PRIMARY KEY,
  user_id          TEXT NOT NULL,
  submission_id    TEXT,
  event_type       TEXT NOT NULL,
  reason           TEXT,
  skill_gaps_json  TEXT,
  client_name      TEXT,
  processed        INTEGER NOT NULL DEFAULT 0,
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS style_edits (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  message_id    TEXT NOT NULL,
  original      TEXT NOT NULL,
  edited        TEXT NOT NULL,
  diff_summary  TEXT,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  entity_type   TEXT,
  entity_id     TEXT,
  action        TEXT NOT NULL,
  metadata_json TEXT,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);

CREATE TABLE IF NOT EXISTS scan_results (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  company_name    TEXT NOT NULL,
  role_title      TEXT NOT NULL,
  role_url        TEXT,
  location        TEXT,
  comp_range      TEXT,
  level           TEXT,
  skills_detected TEXT,
  scan_method     TEXT,
  match_score     REAL,
  detected_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at    TIMESTAMP,
  dismissed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  description TEXT
);
INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (1, 'initial schema');
