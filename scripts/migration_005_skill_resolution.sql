-- Migration 005: Phase B2 skill resolution decisions
-- ===================================================
-- Run AFTER migration_004_calibration.sql.
-- Idempotent. Safe to re-run.
--
-- Goal: track every decision a user makes about an unresolved
-- raw_skill_text — alias to existing skill, promote to new skill,
-- or reject as junk. Without this table the unresolved-candidates
-- queue would show the same items forever.
--
-- Key columns:
--   raw_text_normalized: lowercased + trimmed for dedup. The same
--     literal string may appear with different casing across reqs.
--   decision: 'alias' | 'promote' | 'reject'
--   resolved_skill_id: NULL for 'reject'. Set to existing skill id
--     for 'alias'. Set to NEW skill id for 'promote'.
--   audit_event_id: links to the audit_events row for compliance trace.
-- ===================================================

CREATE TABLE IF NOT EXISTS skill_resolution_decisions (
  id                    TEXT PRIMARY KEY,
  raw_text_normalized   TEXT NOT NULL,
  decision              TEXT NOT NULL CHECK (decision IN ('alias','promote','reject')),
  resolved_skill_id     TEXT,
  decided_by_user_id    TEXT,
  decided_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes                 TEXT,
  audit_event_id        TEXT,
  FOREIGN KEY (resolved_skill_id)  REFERENCES skills(id) ON DELETE SET NULL,
  FOREIGN KEY (decided_by_user_id) REFERENCES users(id)  ON DELETE SET NULL,
  FOREIGN KEY (audit_event_id)     REFERENCES audit_events(id) ON DELETE SET NULL,
  UNIQUE(raw_text_normalized)
);
CREATE INDEX IF NOT EXISTS idx_resolution_decision ON skill_resolution_decisions(decision);
CREATE INDEX IF NOT EXISTS idx_resolution_skill ON skill_resolution_decisions(resolved_skill_id);

-- Cache for AI-generated suggestions on unresolved skills, so we
-- don't pay for repeated LLM calls when the user reloads the
-- promotion UI. Cleared when the underlying skill is acted on.
CREATE TABLE IF NOT EXISTS skill_promotion_suggestions (
  id                    TEXT PRIMARY KEY,
  raw_text_normalized   TEXT NOT NULL,
  suggestion_type       TEXT NOT NULL CHECK (suggestion_type IN ('alias','promote','reject')),
  suggested_canonical   TEXT,
  suggested_category    TEXT,
  suggested_adjacencies TEXT,           -- JSON array of skill_ids
  suggested_alias_target TEXT,          -- skill_id when type='alias'
  confidence            REAL,
  llm_rationale         TEXT,
  generated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  model_version_id      TEXT,
  FOREIGN KEY (suggested_alias_target) REFERENCES skills(id) ON DELETE SET NULL,
  FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE SET NULL,
  UNIQUE(raw_text_normalized)
);
CREATE INDEX IF NOT EXISTS idx_promo_suggest_text ON skill_promotion_suggestions(raw_text_normalized);

-- Migration tracking
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (5, 'skill_resolution_phase_b2');
