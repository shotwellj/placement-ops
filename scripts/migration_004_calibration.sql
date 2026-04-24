-- Migration 004: Phase B1 calibration signal capture
-- ==================================================
-- Run AFTER migration_003_compliance_and_taxonomy.sql.
-- Idempotent. Safe to re-run.
--
-- Goal: extend calibration_events with enough data for the Bayesian
-- adjacency-weight update to reconstruct exactly what happened. Also
-- add a calibration_runs table so we can audit and rollback.
--
-- Design notes:
--  - event_weight stores the signed signal magnitude
--    (+3.0 placed, +2.0 offer, +1.0 onsite, -1.0 early reject, -2.0 late reject)
--  - from_stage/to_stage snapshot the transition for reproducibility
--  - req_id denormalized from submission_id to let the calibrator
--    skip a join on every adjacency computation
--  - calibration_runs lets us group updates and rollback if needed
-- ==================================================

-- Extend calibration_events with transition + signal data
ALTER TABLE calibration_events ADD COLUMN req_id TEXT;
ALTER TABLE calibration_events ADD COLUMN from_stage TEXT;
ALTER TABLE calibration_events ADD COLUMN to_stage TEXT;
ALTER TABLE calibration_events ADD COLUMN event_weight REAL NOT NULL DEFAULT 0.0;
ALTER TABLE calibration_events ADD COLUMN audit_event_id TEXT;

CREATE INDEX IF NOT EXISTS idx_calib_events_processed ON calibration_events(processed, created_at);
CREATE INDEX IF NOT EXISTS idx_calib_events_submission ON calibration_events(submission_id);
CREATE INDEX IF NOT EXISTS idx_calib_events_req ON calibration_events(req_id);

-- calibration_runs: groups adjustments for audit/rollback
CREATE TABLE IF NOT EXISTS calibration_runs (
  id                    TEXT PRIMARY KEY,
  triggered_by_user_id  TEXT,
  events_processed      INTEGER NOT NULL DEFAULT 0,
  pairs_updated         INTEGER NOT NULL DEFAULT 0,
  learning_rate_used    REAL,
  notes                 TEXT,
  started_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at          TIMESTAMP,
  FOREIGN KEY (triggered_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_calib_runs_started ON calibration_runs(started_at);

-- adjacency_history: every weight change, with before/after + which run caused it
-- This is the rollback path and the compliance evidence that calibration is
-- deterministic and auditable.
CREATE TABLE IF NOT EXISTS adjacency_history (
  id                   TEXT PRIMARY KEY,
  run_id               TEXT NOT NULL,
  skill_id             TEXT NOT NULL,
  adjacent_id          TEXT NOT NULL,
  old_weight           REAL NOT NULL,
  new_weight           REAL NOT NULL,
  sample_count_before  INTEGER NOT NULL,
  source_before        TEXT NOT NULL,
  source_after         TEXT NOT NULL,
  created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id)      REFERENCES calibration_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (skill_id)    REFERENCES skills(id) ON DELETE CASCADE,
  FOREIGN KEY (adjacent_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_adj_hist_run ON adjacency_history(run_id);
CREATE INDEX IF NOT EXISTS idx_adj_hist_pair ON adjacency_history(skill_id, adjacent_id);

-- Migration tracking
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES (4, 'calibration_phase_b1');
