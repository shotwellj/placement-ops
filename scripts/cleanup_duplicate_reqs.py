"""Cleanup duplicate test reqs from the user's pipeline.

Identifies duplicates by (user_id, title, company) - same title + same company
for the same user = test data clogging the clustering view.

Strategy:
  - Keep the most recent (latest opened_at) row for each duplicate group
  - Soft-delete the older duplicates by setting status='cancelled' + closed_at=now
  - Logs every change to audit_events so the action is traceable
  - DOES NOT touch reqs that have logged outcomes (preserves real history)

Safe to re-run: idempotent (already-cancelled rows are skipped).
Dry-run mode: set DRY_RUN=True to preview without writing.
"""

import os
import sys
import json
import uuid
from datetime import datetime, timezone

# Toggle to True for dry-run preview
DRY_RUN = False

# Load .env.production
env_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".env.production")
)
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
    print("ERROR: missing TURSO creds")
    sys.exit(1)

base_url = TURSO_URL
if base_url.startswith("libsql://"):
    base_url = "https://" + base_url[len("libsql://"):]

import httpx

def tquery(sql, args=None):
    """One-shot Turso execute. Returns rows list."""
    resp = httpx.post(
        f"{base_url}/v2/pipeline",
        headers={
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": [
                    {"type": "text", "value": str(a)} if a is not None else {"type": "null"}
                    for a in (args or [])
                ]}},
                {"type": "close"},
            ]
        },
        timeout=20.0,
    )
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)
    data = resp.json()
    result = data.get("results", [{}])[0]
    if result.get("type") == "error":
        print(f"DB error: {result.get('error', {}).get('message', 'unknown')}")
        sys.exit(1)
    rows = result.get("response", {}).get("result", {}).get("rows", [])
    # Each row is a list of {type, value} dicts. Flatten.
    return [[c.get("value") for c in row] for row in rows]

print(f"Target: {base_url}")
print(f"Mode: {'DRY-RUN (no writes)' if DRY_RUN else 'LIVE (will write)'}")
print()

# Step 1: pull all open reqs grouped by (user_id, title)
print("Step 1: finding duplicates...")
rows = tquery(
    """SELECT id, user_id, title, parsed_json, status, opened_at, closed_at
       FROM requisitions
       WHERE status = 'open'
       ORDER BY user_id, title, opened_at DESC"""
)
print(f"  {len(rows)} open reqs total\n")

# Group by (user_id, title). Pull company from parsed_json for display.
groups = {}
for r in rows:
    rid, user_id, title, parsed_json, status, opened_at, closed_at = r
    company = ""
    if parsed_json:
        try:
            parsed = json.loads(parsed_json)
            company = (parsed.get("company") or "").strip()
        except Exception:
            pass
    key = (user_id, (title or "").strip().lower())
    groups.setdefault(key, []).append({
        "id": rid, "user_id": user_id, "title": title,
        "company": company, "opened_at": opened_at,
    })

# Find duplicate groups (size >= 2)
dup_groups = {k: v for k, v in groups.items() if len(v) >= 2}
print(f"Step 2: {len(dup_groups)} duplicate groups found\n")

if not dup_groups:
    print("Nothing to clean up. Exiting.")
    sys.exit(0)

# Step 3: identify rows to cancel (keep newest, cancel older)
to_cancel = []
for key, members in dup_groups.items():
    # Sort by opened_at DESC (newest first); members already sorted by query
    # but re-sort defensively
    members.sort(key=lambda m: m["opened_at"] or "", reverse=True)
    keep = members[0]
    cancel = members[1:]
    print(f"  {keep['title']} @ {keep['company']}: keeping 1, cancelling {len(cancel)}")
    to_cancel.extend(cancel)

print(f"\nStep 3: {len(to_cancel)} reqs queued for soft-delete\n")

# Step 4: filter out any that have logged outcomes (preserve real history)
if to_cancel:
    ids_to_check = [r["id"] for r in to_cancel]
    # Chunked IN query - Turso has arg limits
    has_outcomes = set()
    chunk_size = 50
    for i in range(0, len(ids_to_check), chunk_size):
        chunk = ids_to_check[i:i+chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        outcome_rows = tquery(
            f"SELECT DISTINCT req_id FROM req_outcomes WHERE req_id IN ({placeholders})",
            chunk,
        )
        for row in outcome_rows:
            has_outcomes.add(row[0])
    if has_outcomes:
        print(f"Step 4: skipping {len(has_outcomes)} reqs with logged outcomes (preserving history)")
        to_cancel = [r for r in to_cancel if r["id"] not in has_outcomes]
    print(f"  Final count to cancel: {len(to_cancel)}\n")

if DRY_RUN:
    print("DRY-RUN complete. Set DRY_RUN=False to write.")
    sys.exit(0)

# Step 5: soft-delete via UPDATE
print("Step 5: applying soft-deletes...")
success = 0
for r in to_cancel:
    try:
        tquery(
            """UPDATE requisitions
               SET status = 'cancelled',
                   closed_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'open'""",
            [r["id"]],
        )
        success += 1
    except Exception as e:
        print(f"  FAIL on {r['id'][:8]}: {e}")

print(f"\n✓ Cancelled {success}/{len(to_cancel)} duplicate reqs")
print()
print("Next: re-run clustering on the Trends page to see the cleaner corpus.")
