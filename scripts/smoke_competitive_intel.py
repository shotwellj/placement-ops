#!/usr/bin/env python3
"""Smoke test for the Competitive Intelligence endpoint.

Hits POST /api/intake/competitive-intel against a deployed instance,
validates the JSON shape, and prints output for human review.

Usage:
    SN_TOKEN=<jwt> SN_REQ_ID=<uuid> python3 scripts/smoke_competitive_intel.py [base_url]

    base_url defaults to https://sourcingnav.com
    SN_TOKEN: a Pro-tier user's JWT (from localStorage.sn_token in browser)
    SN_REQ_ID: UUID of an existing requisition with company_clusters populated
               (Leidos demo: e346d266-5681-432f-937b-9d6c7d242d04)

Exit codes:
    0 = all checks passed
    1 = HTTP error from server
    2 = JSON parse error
    3 = schema validation failure
    4 = missing env vars
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
import urllib.error


# ---------- Schema validation ----------

REQUIRED_TOP_LEVEL = [
    "req_id",
    "competitors_analyzed",
    "insights",
    "ai_model",
]

# Per-insight required keys (matches COMPETITIVE_INTEL_PROMPT output schema).
# Some fields are AI-generated and may be optional in practice — we check
# the structural ones the UI relies on.
REQUIRED_INSIGHT_KEYS = [
    "company",
    "hiring_velocity",
    "salary_range",
    "boolean_strategies",
]

REQUIRED_SALARY_KEYS = [
    "salary_confidence",  # mandatory per honesty rule #1
]

REQUIRED_STRATEGY_KEYS = [
    "macro",
    "micro_1",
    "micro_2",
    "xray",
    "github",
]

VALID_VELOCITIES = {"aggressive", "moderate", "slow"}
VALID_CONFIDENCES = {"high", "medium", "low"}
VALID_DIFFICULTIES = {"high", "moderate", "low"}


def validate_response(payload: dict) -> list[str]:
    """Return list of human-readable issues. Empty list means all checks passed."""
    issues = []

    # Top-level structure
    for k in REQUIRED_TOP_LEVEL:
        if k not in payload:
            issues.append(f"Missing top-level key: {k}")

    insights = payload.get("insights") or []
    if not isinstance(insights, list):
        issues.append("insights is not a list")
        return issues  # nothing else makes sense

    if len(insights) == 0:
        issues.append("insights is empty (expected at least 1 company)")

    for i, ins in enumerate(insights):
        prefix = f"insights[{i}]"

        if not isinstance(ins, dict):
            issues.append(f"{prefix} is not a dict")
            continue

        # Per-insight required keys
        for k in REQUIRED_INSIGHT_KEYS:
            if k not in ins:
                issues.append(f"{prefix} missing key: {k}")

        # Hiring velocity must be in enum
        v = ins.get("hiring_velocity")
        if v and v.lower() not in VALID_VELOCITIES:
            issues.append(f"{prefix}.hiring_velocity = {v!r} (expected one of {VALID_VELOCITIES})")

        # Poaching difficulty (if present) must be in enum
        d = ins.get("poaching_difficulty")
        if d and d.lower() not in VALID_DIFFICULTIES:
            issues.append(f"{prefix}.poaching_difficulty = {d!r} (expected one of {VALID_DIFFICULTIES})")

        # Salary range structure + mandatory confidence flag
        sr = ins.get("salary_range") or {}
        for k in REQUIRED_SALARY_KEYS:
            if k not in sr:
                issues.append(f"{prefix}.salary_range missing key: {k} (HONESTY RULE: mandatory)")
        sc = sr.get("salary_confidence")
        if sc and sc.lower() not in VALID_CONFIDENCES:
            issues.append(f"{prefix}.salary_range.salary_confidence = {sc!r} (expected one of {VALID_CONFIDENCES})")

        # Boolean strategies must have all 5
        bs = ins.get("boolean_strategies") or {}
        for k in REQUIRED_STRATEGY_KEYS:
            if k not in bs or not bs[k]:
                issues.append(f"{prefix}.boolean_strategies missing or empty: {k}")

        # Boolean strategies must contain the company name
        company = ins.get("company") or ""
        if company:
            for k in ("macro", "micro_1", "xray"):
                if k in bs and bs[k] and company not in bs[k]:
                    issues.append(
                        f"{prefix}.boolean_strategies.{k} does not mention "
                        f"company name {company!r} (likely a templating bug)"
                    )

        # Boolean strategies should NOT contain literal AND between terms
        # (Google syntax uses implicit AND via space; literal AND is treated as keyword)
        for k, v in bs.items():
            if v and " AND " in v:
                issues.append(
                    f"{prefix}.boolean_strategies.{k} contains literal ' AND ' "
                    "(violates BOOLEAN_BUILDER_PROMPT Google syntax rule)"
                )

    return issues


# ---------- HTTP call ----------

def call_endpoint(base_url: str, token: str, req_id: str) -> tuple[int, dict]:
    """Returns (status_code, parsed_response_or_error_dict)."""
    url = f"{base_url.rstrip('/')}/api/intake/competitive-intel"
    body = json.dumps({"req_id": req_id}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read())
        except Exception:
            err_body = {"detail": str(e)}
        return e.code, err_body
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


# ---------- Pretty-print ----------

def print_summary(payload: dict) -> None:
    print("=" * 78)
    print(f"req_id:               {payload.get('req_id')}")
    print(f"role_title:           {payload.get('role_title')}")
    print(f"competitors_analyzed: {payload.get('competitors_analyzed')}")
    print(f"ai_model:             {payload.get('ai_model')}")
    print(f"honesty_caveat:       {payload.get('honesty_caveat', '')[:120]}")
    print()

    summary = payload.get("market_summary") or {}
    if summary:
        print("MARKET SUMMARY:")
        print(f"  competitive_intensity: {summary.get('competitive_intensity')}")
        if summary.get("competitive_intensity_rationale"):
            print(f"    rationale: {summary['competitive_intensity_rationale']}")
        print(f"  fastest_to_fill:       {summary.get('fastest_to_fill_competitor')}")
        print(f"  most_aggressive_hirer: {summary.get('most_aggressive_hirer')}")
        if summary.get("comp_benchmark_vs_jd"):
            print(f"  comp_vs_jd:            {summary['comp_benchmark_vs_jd']}")
        if summary.get("top_recruiting_angles"):
            print("  top_recruiting_angles:")
            for a in summary["top_recruiting_angles"]:
                print(f"    - {a}")
        print()

    print("PER-COMPANY INSIGHTS:")
    for i, ins in enumerate(payload.get("insights") or []):
        sr = ins.get("salary_range") or {}
        print(f"\n  [{i+1}] {ins.get('company')} (tier {ins.get('tier', '?')})")
        print(f"      hiring_velocity:     {ins.get('hiring_velocity')} (confidence: {ins.get('velocity_confidence', '—')})")
        print(f"      poaching_difficulty: {ins.get('poaching_difficulty')}")
        print(f"      eng_count:           {ins.get('estimated_engineering_count')}")
        print(f"      time_to_fill:        {ins.get('avg_time_to_fill')}")
        print(f"      remote_policy:       {ins.get('remote_policy')}")
        print(f"      salary_range:        ${sr.get('min', '?'):,} - ${sr.get('max', '?'):,}  ({sr.get('salary_confidence', '?')} confidence)")
        if sr.get("salary_basis"):
            print(f"        basis: {sr['salary_basis']}")
        if ins.get("key_recruiting_angle"):
            print(f"      recruiting_angle:    {ins['key_recruiting_angle']}")
        if ins.get("poaching_rationale"):
            print(f"      poaching_rationale:  {ins['poaching_rationale']}")
        skills = ins.get("common_skills_for_this_role") or []
        if skills:
            print(f"      common_skills:       {', '.join(skills[:5])}")
        bs = ins.get("boolean_strategies") or {}
        if bs:
            print(f"      boolean_strategies:")
            for k, v in bs.items():
                if v:
                    truncated = v if len(v) <= 100 else v[:97] + "..."
                    print(f"        {k}: {truncated}")


# ---------- Main ----------

def main() -> int:
    token = os.environ.get("SN_TOKEN")
    req_id = os.environ.get("SN_REQ_ID")
    base_url = sys.argv[1] if len(sys.argv) > 1 else "https://sourcingnav.com"

    if not token or not req_id:
        print("ERROR: missing env vars.")
        print()
        print("Usage:")
        print("  SN_TOKEN=<jwt> SN_REQ_ID=<uuid> python3 scripts/smoke_competitive_intel.py [base_url]")
        print()
        print("Get SN_TOKEN by logging into sourcingnav.com as a Pro user, then in DevTools:")
        print("  localStorage.getItem('sn_token')")
        print()
        print("Get SN_REQ_ID from any successful intake (the URL after intake or the requisitions table).")
        print("  Leidos demo req: e346d266-5681-432f-937b-9d6c7d242d04")
        return 4

    print(f"POST {base_url}/api/intake/competitive-intel")
    print(f"  req_id: {req_id}")
    print(f"  token:  {token[:20]}...")
    print()

    t0 = time.time()
    status, payload = call_endpoint(base_url, token, req_id)
    elapsed = time.time() - t0

    print(f"HTTP {status}  ({elapsed:.2f}s)")

    if status != 200:
        print()
        print("ERROR RESPONSE:")
        print(json.dumps(payload, indent=2))
        return 1

    # Pretty-print the response
    print_summary(payload)

    # Validate schema
    print()
    print("=" * 78)
    print("SCHEMA VALIDATION")
    print("=" * 78)
    issues = validate_response(payload)
    if not issues:
        print("OK — all schema checks passed.")
        print(f"      {len(payload.get('insights') or [])} insights, "
              f"all with required keys, valid enums, and 5 boolean strategies each.")
        return 0
    else:
        print(f"FAIL — {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
