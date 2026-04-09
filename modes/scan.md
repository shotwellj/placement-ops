# Mode: Scan

> Crawl target company career pages for new openings matching your niche.

## Trigger

`/placement-ops scan`

## What This Does

Scans your configured company portals (from `config/portals.yml`) for open positions that match your recruiting niche. Flags new reqs, skips duplicates, and adds fresh opportunities to your pipeline.

## Pre-Flight

1. Load `config/profile.yml` — need your niche keywords
2. Load `config/portals.yml` — the company list
3. Load `data/scan-history.tsv` — previous scan results (for dedup)
4. Load `data/pipeline.md` — current pipeline (for dedup)

## Three-Level Scan Strategy

### Level 1 — Playwright (Primary)

For each enabled portal with `scan_method: playwright`:

1. Navigate to the `careers_url` using browser automation
2. Read all visible job listings
3. Extract: **title**, **URL**, **location**, **department** (if visible)
4. Process in sequential batches (never parallel — respects rate limits)

Works with dynamic pages: Ashby, Lever, Workday, custom career sites.

### Level 2 — Greenhouse API (Complementary)

For portals with `scan_method: greenhouse_api` and an `api` field:

1. Fetch JSON from `boards-api.greenhouse.io/v1/boards/{slug}/jobs`
2. Parse structured job data (title, location, department, URL)
3. Faster and more reliable than Playwright for Greenhouse companies

### Level 3 — Web Search (Broad Discovery)

Execute search queries to discover companies NOT in your portal list:

```
site:greenhouse.io "machine learning engineer"
site:ashbyhq.com "data scientist" "senior"
site:lever.co "ML engineer"
```

Results require verification (may be cached/expired).

## Filtering Pipeline

For every discovered posting:

1. **Title Match**: Check against `positive_keywords` from profile. Skip if no match.
2. **Negative Filter**: Skip if title contains any `negative_keywords`.
3. **Priority Boost**: Flag if title contains `priority_boost_keywords`.
4. **Dedup — Scan History**: Skip if URL already in `data/scan-history.tsv`.
5. **Dedup — Pipeline**: Skip if URL already in `data/pipeline.md`.
6. **Dedup — Reports**: Skip if company+role already has a report in `reports/`.
7. **Expiry Check** (Level 3 only): Verify the posting is still live.

## Output

### 1. New Pipeline Entries

Add each new req to `data/pipeline.md`:

```markdown
| # | Date | Company | Role | Source | Priority | Status | Candidates | Notes |
|---|------|---------|------|--------|----------|--------|------------|-------|
| 047 | 2026-04-08 | Anthropic | Senior ML Engineer | greenhouse_api | high | New | — | SF. Research team. |
```

### 2. Scan Log

Append to `data/scan-history.tsv`:

```
2026-04-08\tAnthropic\tSenior ML Engineer\thttps://...\tadded
2026-04-08\tOpenAI\tData Analyst I\thttps://...\tskipped_negative_keyword
2026-04-08\tStripe\tSenior DS\thttps://...\tskipped_duplicate
```

### 3. Summary Report

Print a summary:

```
SCAN COMPLETE — 2026-04-08
─────────────────────────────
Portals scanned:     38 / 45
  Level 1 (Playwright): 20
  Level 2 (Greenhouse):  15
  Level 3 (WebSearch):    3
Total postings found:  142
  Matched niche:         67
  Duplicates skipped:    41
  Expired/removed:        3
  NEW reqs added:        23
  Priority (high):        7

Top new reqs:
  1. [HIGH] Anthropic — Senior ML Engineer (SF)
  2. [HIGH] Scale AI — Staff Data Scientist (SF/Remote)
  3. [HIGH] Tempus AI — ML Engineering Manager (Chicago)
  ...
```

## Rules

- Never run Playwright scans in parallel — sequential only
- If a URL fails, log it as `skipped_error` and continue
- If a portal is disabled (`enabled: false`), skip it entirely
- Always save newly discovered `careers_url` values back to portals.yml
- Respect robots.txt — if a site blocks automated access, use websearch fallback
