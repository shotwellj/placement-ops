"""Apply migration_006_req_outcomes.sql to production Turso DB via HTTP API.

This is a one-shot script. Reads .env.production for credentials, parses the
SQL file into individual statements, sends them through the Turso /v2/pipeline
HTTP endpoint as a single transactional batch.

Why a script not the CLI: turso CLI requires interactive auth which we can't
do from here. The HTTP API uses the same TURSO_AUTH_TOKEN that powers the
deployed app, so if the deployed app can write, this script can write.

Safety: every statement in the migration uses CREATE TABLE/INDEX IF NOT EXISTS
so re-running is a no-op. No data destruction possible.
"""

import os
import sys
import re
import httpx

# Load .env.production manually (no python-dotenv dep needed)
env_path = os.path.join(os.path.dirname(__file__), "..", ".env.production")
env_path = os.path.abspath(env_path)
if not os.path.exists(env_path):
    print(f"ERROR: {env_path} not found")
    sys.exit(1)

env = {}
for line in open(env_path):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    env[k.strip()] = v.strip().strip('"').strip("'")

TURSO_URL = env.get("TURSO_URL", "")
TURSO_TOKEN = env.get("TURSO_AUTH_TOKEN", "")

if not TURSO_URL or not TURSO_TOKEN:
    print("ERROR: TURSO_URL or TURSO_AUTH_TOKEN missing from .env.production")
    sys.exit(1)

# libsql:// -> https:// for HTTP API
if TURSO_URL.startswith("libsql://"):
    base_url = "https://" + TURSO_URL[len("libsql://") :]
else:
    base_url = TURSO_URL

print(f"Target: {base_url}")

# Parse SQL file into individual statements (split on `;` outside of comments)
sql_path = os.path.join(os.path.dirname(__file__), "migration_006_req_outcomes.sql")
sql_text = open(sql_path).read()

# Strip comment lines, then split on `;` and clean up
statements = []
buf = []
for line in sql_text.split("\n"):
    stripped = line.strip()
    if stripped.startswith("--") or not stripped:
        continue
    buf.append(line)
joined = "\n".join(buf)

# Split on `;` but handle the trailing case
for raw_stmt in joined.split(";"):
    stmt = raw_stmt.strip()
    if stmt:
        statements.append(stmt)

print(f"Parsed {len(statements)} statements from migration_006_req_outcomes.sql")

# Build the pipeline request: every statement as an `execute`, then a `close`
requests = [{"type": "execute", "stmt": {"sql": s, "args": []}} for s in statements]
requests.append({"type": "close"})

# Fire the request
print("Sending to Turso /v2/pipeline...")
resp = httpx.post(
    f"{base_url}/v2/pipeline",
    headers={
        "Authorization": f"Bearer {TURSO_TOKEN}",
        "Content-Type": "application/json",
    },
    json={"requests": requests},
    timeout=30.0,
)

print(f"Status: {resp.status_code}")
if resp.status_code != 200:
    print("ERROR body:", resp.text[:500])
    sys.exit(1)

results = resp.json().get("results", [])
errors = []
for i, r in enumerate(results):
    if r.get("type") == "error":
        errors.append((i, r.get("error", {}).get("message", "unknown")))
    elif r.get("type") == "ok":
        # ok results: execute or close
        pass

if errors:
    print(f"\n{len(errors)} statement(s) failed:")
    for i, msg in errors:
        # Statement i is the i-th element in `statements` (the close is last)
        if i < len(statements):
            stmt_preview = statements[i].split("\n")[0][:60]
            print(f"  [{i}] {stmt_preview}... -> {msg}")
        else:
            print(f"  [{i}] (close) -> {msg}")
    sys.exit(1)

print(f"\nAll {len(statements)} statements applied cleanly.")

# Verify: query the schema for the new table
print("\nVerifying req_outcomes table exists...")
verify_resp = httpx.post(
    f"{base_url}/v2/pipeline",
    headers={
        "Authorization": f"Bearer {TURSO_TOKEN}",
        "Content-Type": "application/json",
    },
    json={
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": "SELECT name FROM sqlite_master WHERE type='table' AND name='req_outcomes'",
                    "args": [],
                },
            },
            {"type": "close"},
        ]
    },
    timeout=10.0,
)
verify_data = verify_resp.json()
table_rows = (
    verify_data.get("results", [{}])[0]
    .get("response", {})
    .get("result", {})
    .get("rows", [])
)
if table_rows:
    print("✓ req_outcomes table confirmed in production schema")
else:
    print("⚠ req_outcomes NOT found — migration may have silently failed")
    sys.exit(1)

print("\nMigration 006 applied successfully.")
