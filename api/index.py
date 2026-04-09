"""
SourcingNav API — Read-only FastAPI backend.

Serves seed data from /api/seed/*.json to the frontend dashboards.
Deploy target: Vercel serverless Python functions.
Local dev: `uvicorn api.index:app --reload --port 8000` from the repo root.
"""

import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="SourcingNav API",
    description="Read-only API powering the SourcingNav agency + company dashboards.",
    version="0.1.0",
)

# Allow the frontend (sourcingnav.com + localhost) to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sourcingnav.com",
        "https://www.sourcingnav.com",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:5500",
        "*",  # open for demo; tighten later
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Seed data lives next to this file in /api/seed/
SEED_DIR = Path(__file__).parent / "seed"


def load_seed(name: str) -> dict:
    """Load a JSON seed file by name (without the .json extension)."""
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Seed file '{name}' not found")
    with open(path, "r") as f:
        return json.load(f)


# ---------- Meta ----------

@app.get("/api")
def root():
    return {
        "name": "SourcingNav API",
        "version": "0.1.0",
        "status": "ok",
        "endpoints": [
            "/api/health",
            "/api/dashboard/agency",
            "/api/dashboard/company",
            "/api/candidates",
            "/api/candidates/{candidate_id}",
            "/api/pipeline",
            "/api/market-intel",
            "/api/scan",
            "/api/batch",
            "/api/calibration",
            "/api/integrations",
        ],
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "sourcingnav-api"}


# ---------- Dashboard KPIs ----------

@app.get("/api/dashboard/agency")
def dashboard_agency():
    data = load_seed("dashboard")
    return data["agency"]


@app.get("/api/dashboard/company")
def dashboard_company():
    data = load_seed("dashboard")
    return data["company"]


# ---------- Candidates ----------

@app.get("/api/candidates")
def list_candidates():
    return load_seed("candidates")


@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id: str):
    data = load_seed("candidates")
    for c in data["candidates"]:
        if c["id"] == candidate_id:
            return c
    raise HTTPException(status_code=404, detail=f"Candidate '{candidate_id}' not found")


# ---------- Pipeline (agency searches) ----------

@app.get("/api/pipeline")
def pipeline():
    return load_seed("pipeline")


# ---------- Intelligence views ----------

@app.get("/api/market-intel")
def market_intel():
    return load_seed("market_intel")


@app.get("/api/scan")
def scan():
    return load_seed("scan")


@app.get("/api/batch")
def batch():
    return load_seed("batch")


@app.get("/api/calibration")
def calibration():
    return load_seed("calibration")


@app.get("/api/integrations")
def integrations():
    return load_seed("integrations")
