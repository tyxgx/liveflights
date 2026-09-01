"""
Throwaway local API for visually comparing OLD (buggy, currently-live)
corridors vs FIXED (local test) corridors in the real web/ dashboard,
without touching AWS. Not part of the deployed app -- lives in ml/scratch/
and is never imported by api/cloud/app.py.

Serves the minimum the /live dashboard needs in cloud/polling mode.
Corridor set is picked via ?set=old|fixed (default: fixed).

Run: uv run --group ml uvicorn ml.scratch.local_preview_api:app --port 8000 --app-dir .
"""

import json
import os
from datetime import UTC, datetime

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

DEFAULT_SET = os.environ.get("CORRIDOR_SET", "fixed")

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ml/scratch/artifacts/corridors.json is now the real train_all.py output
# (the fix was folded into the real script, not just the throwaway test
# copy) -- both "old" and "fixed" point at the same current artifact.
FIXED = json.load(open("ml/scratch/artifacts/corridors.json"))
OLD = FIXED
for c in FIXED:
    c.setdefault("altitude_p10_ft", 0)
    c.setdefault("altitude_p50_ft", 0)
    c.setdefault("altitude_p90_ft", 0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/flights/live")
def flights_live(limit: int = 500):
    return {"count": 0, "flights": [], "updated_at": datetime.now(UTC).isoformat()}


@app.get("/api/stats/overview")
def overview():
    return {
        "active_flights": 0,
        "countries": 0,
        "avg_altitude_ft": None,
        "anomaly_count": None,
        "ml_paused": True,
    }


@app.get("/api/stats/traffic-by-hour")
def traffic_by_hour():
    return {"points": []}


@app.get("/api/stats/by-country")
def by_country(limit: int = 10):
    return {"countries": []}


@app.get("/api/stats/altitude-distribution")
def altitude_distribution():
    return {"bands": []}


@app.get("/api/corridors")
def corridors(limit: int = 200, set: str = Query(DEFAULT_SET)):
    src = FIXED if set == "fixed" else OLD
    ranked = sorted(src, key=lambda c: c["member_count"], reverse=True)[:limit]
    return {
        "total_corridors": len(src),
        "returned": len(ranked),
        "corridors": ranked,
        "ml_paused": False,
    }


@app.get("/api/anomalies")
def anomalies(page: int = 1, page_size: int = 100):
    return {"total": 0, "page": page, "page_size": page_size, "events": [], "ml_paused": True}


@app.get("/api/forecast/traffic")
def forecast():
    return {"points": [], "ml_paused": True}
