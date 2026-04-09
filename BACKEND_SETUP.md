# SourcingNav Backend — Setup & Deploy

This is the read-only Python FastAPI backend that powers the SourcingNav dashboards at sourcingnav.com.

## What got built

```
placement-ops/
├── api/
│   ├── index.py              ← FastAPI app (12 endpoints)
│   └── seed/
│       ├── dashboard.json    ← Agency + company KPIs
│       ├── candidates.json   ← 7 candidates with fit scores, prep cards
│       ├── pipeline.json     ← Active searches
│       ├── market_intel.json ← Comp hygiene, talent flow, posting audit
│       ├── scan.json         ← Competitor portal scans
│       ├── batch.json        ← Ranked batch eval
│       ├── calibration.json  ← Interview drift detection
│       └── integrations.json ← Connected + available integrations
├── vercel.json               ← Vercel serverless Python config
├── requirements.txt          ← Python dependencies
└── ui/people-ops.html        ← Company dashboard now pings /api/health on load
```

## Endpoints (all GET, read-only)

| Endpoint | Returns |
|---|---|
| `/api` | API metadata + endpoint list |
| `/api/health` | `{"status": "ok"}` |
| `/api/dashboard/agency` | Agency KPIs + funnel + activity |
| `/api/dashboard/company` | Company KPIs + funnel + activity |
| `/api/candidates` | List all 7 candidates |
| `/api/candidates/c_001` | Marcus Rivera deep dive |
| `/api/pipeline` | Active recruiting searches |
| `/api/market-intel` | Comp hygiene + talent flow + posting audit |
| `/api/scan` | Recent competitor scans |
| `/api/batch` | Ranked batch eval for Anthropic Staff ML |
| `/api/calibration` | Interview panel drift analysis |
| `/api/integrations` | Connected + available integrations |

---

## Step 1 — Run it locally (optional, to verify before deploying)

Open a terminal in the `placement-ops` folder and run:

```bash
pip install -r requirements.txt
pip install uvicorn
uvicorn api.index:app --reload --port 8000
```

Then in a browser, visit:
- http://localhost:8000/api/health
- http://localhost:8000/api/candidates/c_001
- http://localhost:8000/api/market-intel

You should see JSON responses. If that works, the backend is solid.

To test the frontend talking to the local backend, open `ui/people-ops.html` in a browser served from `localhost` (e.g. VS Code Live Server). The sidebar footer will show a green dot + "API: live" if the wiring is correct.

---

## Step 2 — Deploy to Vercel

You already have a Vercel account and the MCP is connected, so the fastest path is:

### Option A — One command with the Vercel CLI

```bash
npm install -g vercel
cd /path/to/placement-ops
vercel --prod
```

When it asks:
- **Set up and deploy?** → Yes
- **Which scope?** → your personal account
- **Link to existing project?** → No (or Yes if you already made one)
- **Project name?** → `sourcingnav-api`
- **Directory?** → `./` (just press enter)
- **Override settings?** → No

It will build and give you a URL like `https://sourcingnav-api.vercel.app`. Test it:

```bash
curl https://sourcingnav-api.vercel.app/api/health
```

### Option B — From GitHub (recommended for long term)

1. Commit and push these files to your GitHub repo (you already have one).
2. Go to https://vercel.com/new
3. Import the `placement-ops` repo.
4. Framework preset: **Other**
5. Build command: leave empty
6. Output directory: leave empty
7. Click Deploy.

Vercel auto-detects `vercel.json` and uses it. Every future `git push` to `main` auto-deploys the API.

---

## Step 3 — Point sourcingnav.com/api at the backend

Right now your frontend lives at `sourcingnav.com` via GitHub Pages. Two options for getting `/api/*` to hit the Python backend:

### Option A — Keep GitHub Pages for frontend, use `api.sourcingnav.com` subdomain

1. In Vercel project → Settings → Domains → add `api.sourcingnav.com`
2. In your DNS provider (where sourcingnav.com is registered): add a `CNAME` record
   - Host: `api`
   - Points to: `cname.vercel-dns.com`
3. In `ui/people-ops.html`, change this line:
   ```js
   : '/api';
   ```
   to:
   ```js
   : 'https://api.sourcingnav.com/api';
   ```
4. Commit & push.

### Option B — Move the whole site to Vercel (simpler long-term)

Let Vercel serve both the static HTML **and** the Python API from one project. This is what `vercel.json` is already configured for — it just needs the frontend added. Tell Jason to say the word and I'll wire it.

---

## Step 4 — Verify the live pill

Once deployed and DNS has propagated (can take a few minutes):

1. Visit https://sourcingnav.com/ui/people-ops.html
2. Look at the bottom of the left sidebar.
3. You should see a green dot + "API: live" under "SourcingNav v2.1 · Company".

If it says "API: offline", open browser DevTools → Console to see what the fetch failed on (usually CORS or DNS).

---

## What's still hardcoded

The dashboards still render most of their data from hardcoded HTML. The pilot wiring just proves the pipe works (health check). Next pass will replace hardcoded blocks with real fetches — one view at a time so nothing breaks.

Priority order for the next session:
1. `/api/candidates` → feed the "Candidates in Pipeline" table
2. `/api/market-intel` → feed the comp hygiene and posting audit tables
3. `/api/calibration` → feed the calibration drift table
4. `/api/dashboard/company` → feed the top KPIs
