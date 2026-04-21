-- Migration 002: Session management
-- Run AFTER scripts/schema.sql has been applied.
-- ALTER COLUMN statements will fail if columns already exist; that's fine, the rest still runs.

ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP;
ALTER TABLE users ADD COLUMN last_login_ip TEXT;

CREATE TABLE IF NOT EXISTS sessions (
  id                  TEXT PRIMARY KEY,
  user_id             TEXT NOT NULL,
  session_token_hash  TEXT UNIQUE NOT NULL,
  user_agent          TEXT,
  ip_address          TEXT,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_used_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at          TIMESTAMP NOT NULL,
  revoked_at          TIMESTAMP,
  revoke_reason       TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(session_token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(user_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL,
  ip_address   TEXT,
  success      INTEGER NOT NULL DEFAULT 0,
  attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email_time ON login_attempts(email, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip_address, attempted_at);
