-- Migration 006: Stage 2 of proactive engine — req outcome feedback loop
-- =====================================================================
-- Run AFTER migration_005_skill_resolution.sql.
-- Idempotent. Safe to re-run.
--
-- Goal: capture what actually happened with each requisition (filled,
-- lost, cancelled) so the Live Intelligence Stream's velocity_baseline
-- and skill_concentration signals can learn from real outcomes instead
-- of pure pipeline math. This is the substrate for the predictive layer
-- of the Talent Intelligence Engine.
--
-- Why a separate table and not columns on requisitions:
--   1. One req can have multiple outcome events over time (placed, then
--      fell off at month 3, then re-opened) — we want the history not
--      just the latest snapshot
--   2. Compliance: outcome data feeds prediction models, so every write
--      gets an audit_event_id link for EU AI Act Article 12 traceability
--   3. Privacy: anonymizing a closed req per GDPR Recital 26 should
--      preserve outcome signal even after PII is stripped
--
-- Key columns:
--   outcome: the terminal state. 'filled' | 'lost' | 'cancelled' |
--            'fell_off' (placed but rejected/quit within guarantee
--            period) | 'reopened' (post-fell_off restart)
--   time_to_close_days: days from req opened_at to this outcome event.
--                       NULL for 'reopened'. Computed at write-time so
--                       it survives anonymization of the source req.
--   placed_candidate_company_prev: the company the placed candidate was
--                       AT before this hire. Powers competitor_overlap
--                       intelligence ("Stripe is your most-poached
--                       company across 4 placements").
--   placed_candidate_skills: JSON array of canonical skill ids that the
--                       placed candidate actually had. Powers skill
--                       outcome learning ("'Embedded C/C++' closes
--                       38% faster than the pipeline average").
--   lost_to_company: if outcome='lost', who did they go to? Surfaces
--                       repeat losers and competitive pressure.
--   notes: free text from the recruiter (optional)
--   logged_at: when the outcome was recorded (vs. closed_at on the req)
--   audit_event_id: links to audit_events for compliance trace
-- =====================================================================

CREATE TABLE IF NOT EXISTS req_outcomes (
  id                              TEXT PRIMARY KEY,
  req_id                          TEXT NOT NULL,
  outcome                         TEXT NOT NULL CHECK (outcome IN ('filled','lost','cancelled','fell_off','reopened')),
  time_to_close_days              INTEGER,
  placed_candidate_company_prev   TEXT,
  placed_candidate_skills         TEXT,  -- JSON array
  lost_to_company                 TEXT,
  notes                           TEXT,
  logged_by_user_id               TEXT NOT NULL,
  logged_at                       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  audit_event_id                  TEXT,
  FOREIGN KEY (req_id)            REFERENCES requisitions(id) ON DELETE CASCADE,
  FOREIGN KEY (logged_by_user_id) REFERENCES users(id)         ON DELETE SET NULL,
  FOREIGN KEY (audit_event_id)    REFERENCES audit_events(id)  ON DELETE SET NULL
);

-- Index on req_id is the primary lookup pattern (latest outcome for a req)
CREATE INDEX IF NOT EXISTS idx_req_outcomes_req ON req_outcomes(req_id, logged_at DESC);

-- Index on outcome + logged_at supports the cross-req aggregation queries
-- that power the Live Intelligence Stream's velocity_baseline event.
CREATE INDEX IF NOT EXISTS idx_req_outcomes_state ON req_outcomes(outcome, logged_at DESC);

-- Index on placed_candidate_company_prev powers the competitor_overlap
-- enhancement: "Stripe is your most-poached company across 4 placements".
CREATE INDEX IF NOT EXISTS idx_req_outcomes_poached ON req_outcomes(placed_candidate_company_prev)
  WHERE placed_candidate_company_prev IS NOT NULL;

-- Index on logged_by_user_id supports the per-user dashboard queries
-- (a user only sees their own outcomes).
CREATE INDEX IF NOT EXISTS idx_req_outcomes_user ON req_outcomes(logged_by_user_id, logged_at DESC);
