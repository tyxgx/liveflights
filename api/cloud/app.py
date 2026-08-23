"""Cloud API: reads two small S3 JSON files and computes every stat on the
fly — that's the entire backing store now.

NOTE (Aug 2026): this stack is a live-data-only MVP — ML (corridor
discovery, anomaly detection, traffic forecast, and the departure/
predicted-destination/ETA trajectory-tracking that briefly lived here) is
paused. Along with it: DynamoDB (3 tables), Athena, Glue Catalog, Step
Functions, the transform Lambda, and the Bedrock text-to-SQL Lambda — all
removed, not just unused, since none of them have a consumer right now.
See docs/aws-architecture.md for the reasoning; the short version is that
DynamoDB's full-table item-by-item rewrite every 1-minute poll was a real,
measured ~$155/mo problem once Europe's multi-point coverage pushed the
live-state table to ~4,600 items, and none of that machinery was needed
just to answer "what's flying right now" — one small overwritten S3 object
(live/latest.json) does that for a few cents a month.

What this app actually serves, backed by the resources this Terraform
stack creates:
  - GET /health                       — liveness only
  - GET /api/flights/live             — the live snapshot, straight from S3
  - GET /api/stats/overview           — computed from the live snapshot
  - GET /api/stats/by-country         — computed from the live snapshot
  - GET /api/stats/airline-activity   — computed from the live snapshot
  - GET /api/stats/altitude-distribution — computed from the live snapshot
  - GET /api/stats/traffic-by-hour    — from stats/hourly.json (a small
                                         rolling aggregate the ingest Lambda
                                         maintains, not a data warehouse)
  - GET /api/corridors, /api/anomalies — stub responses, `"paused": true`,
                                          so the dashboard can render an
                                          honest "ML paused" state instead
                                          of erroring or reading as "0
                                          anomalies found" (a real anomaly-
                                          rate claim would need the ML
                                          pipeline actually running)

`/ws/flights` has no cloud equivalent; poll `/api/flights/live` instead.
"""

from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI
from mangum import Mangum
from utils.airlines import callsign_to_airline

app = FastAPI(
    title="liveflights cloud API",
    description="Serverless live-data API (ML paused) — see docs/aws-architecture.md.",
    version="0.2.0-cloud-mvp",
)

s3 = boto3.client("s3")

LAKE_BUCKET = os.environ["LAKE_BUCKET"]
LIVE_SNAPSHOT_KEY = "live/latest.json"
HOURLY_STATS_KEY = "stats/hourly.json"

ALTITUDE_BANDS: list[tuple[float, float, str]] = [
    (-1000, 0, "ground"),
    (0, 5000, "0-5k"),
    (5000, 15000, "5-15k"),
    (15000, 25000, "15-25k"),
    (25000, 35000, "25-35k"),
    (35000, 45000, "35-45k"),
    (45000, 100000, "45k+"),
]


def _load_json(key: str, default: dict) -> dict:
    try:
        obj = s3.get_object(Bucket=LAKE_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return default
        raise
    return json.loads(obj["Body"].read())


def _live_flights() -> dict:
    return _load_json(LIVE_SNAPSHOT_KEY, {"updated_at": None, "count": 0, "flights": []})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/flights/live")
def flights_live(limit: int = 500) -> dict:
    data = _live_flights()
    flights = data.get("flights", [])[: min(limit, 2000)]
    return {"count": len(flights), "flights": flights, "updated_at": data.get("updated_at")}


@app.get("/api/stats/overview")
def stats_overview() -> dict:
    flights = _live_flights().get("flights", [])
    altitudes_m = [f["baro_altitude"] for f in flights if f.get("baro_altitude") is not None]
    countries = {f["origin_country"] for f in flights if f.get("origin_country")}
    avg_altitude_ft = (sum(altitudes_m) / len(altitudes_m) * 3.28084) if altitudes_m else None
    return {
        "active_flights": len(flights),
        "countries": len(countries),
        "avg_altitude_ft": avg_altitude_ft,
        # Not "0 anomalies found" — anomaly detection isn't running at all
        # right now (ML paused). null is deliberate; a bare 0 here would
        # read as a real (and false) "nothing unusual" claim.
        "anomaly_count": None,
        "ml_paused": True,
    }


@app.get("/api/stats/by-country")
def stats_by_country(limit: int = 20) -> dict:
    flights = _live_flights().get("flights", [])
    counts: dict[str, int] = {}
    for f in flights:
        country = f.get("origin_country") or "Unknown"
        counts[country] = counts.get(country, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return {"countries": [{"origin_country": k, "flight_count": v} for k, v in ranked]}


@app.get("/api/stats/airline-activity")
def stats_airline_activity(limit: int = 20) -> list[dict]:
    flights = _live_flights().get("flights", [])
    counts: dict[str, int] = {}
    for f in flights:
        airline = callsign_to_airline(f.get("callsign"))
        counts[airline] = counts.get(airline, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"airline": k, "flight_count": v} for k, v in ranked]


@app.get("/api/stats/altitude-distribution")
def stats_altitude_distribution() -> dict:
    flights = _live_flights().get("flights", [])
    counts = {label: 0 for _, _, label in ALTITUDE_BANDS}
    for f in flights:
        alt_m = f.get("baro_altitude")
        alt_ft = (alt_m * 3.28084) if alt_m is not None else -1
        for lo, hi, label in ALTITUDE_BANDS:
            if lo < alt_ft <= hi:
                counts[label] += 1
                break
    bands = [
        {"altitude_band": label, "flight_count": counts[label]} for _, _, label in ALTITUDE_BANDS
    ]
    return {"bands": bands}


@app.get("/api/stats/traffic-by-hour")
def stats_traffic_by_hour(hours: int = 24) -> dict:
    data = _load_json(HOURLY_STATS_KEY, {"hours": []})
    recent = data.get("hours", [])[-hours:]
    points = [
        {
            "hour_bucket": h["hour"],
            "flight_count": h.get("flight_count", 0),
            "avg_altitude_ft": h.get("avg_altitude_ft"),
            "avg_speed_kmh": None,
            "is_synthetic": False,
        }
        for h in recent
    ]
    return {"points": points}


@app.get("/api/corridors")
def corridors(limit: int = 20) -> dict:
    return {"total_corridors": 0, "returned": 0, "corridors": [], "ml_paused": True}


@app.get("/api/anomalies")
def anomalies(page: int = 1, page_size: int = 50, flagged_only: bool = True) -> dict:
    return {"total": 0, "page": page, "page_size": page_size, "events": [], "ml_paused": True}


handler = Mangum(app)
