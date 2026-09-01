"""Cloud API: reads a handful of small S3 JSON/model files and computes
every stat on the fly — that's the entire backing store.

NOTE (Aug 2026): this stack is a live-data-only MVP. ML (corridor
discovery, anomaly detection, traffic forecast) was paused after a real
~$155/mo DynamoDB cost bug, then **resumed 2026-08-29 in a deliberately
cheap, stateless form** to avoid repeating that mistake: no DynamoDB, no
per-aircraft state table, no Step Functions, no Athena. Corridors/anomaly-
threshold/forecast-model are small static artifacts trained offline
(ml/scratch/train_all.py) and stored under models/ in the same S3 bucket
(a few hundred KB total) — this Lambda just reads them and does the actual
scoring/inference per-request against the same live snapshot it already
reads. See docs/aws-architecture.md for the removed-infra history.
Trajectory-delta (next-position) prediction was also trained but is NOT
wired up here yet — no UI consumes it, deliberately deferred.

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
  - GET /api/corridors                — real corridors from models/corridors.json
  - GET /api/anomalies                — real per-request scoring against the
                                         live snapshot + corridor centroids
  - GET /api/forecast/traffic         — real next-hours forecast from
                                         models/forecast_gbr.joblib

`/ws/flights` has no cloud equivalent; poll `/api/flights/live` instead.
"""

from __future__ import annotations

import io
import json
import os

import boto3
import joblib
import numpy as np
from botocore.exceptions import ClientError
from fastapi import FastAPI
from mangum import Mangum
from utils.airlines import callsign_to_airline

app = FastAPI(
    title="liveflights cloud API",
    description="Serverless live-data API — see docs/aws-architecture.md.",
    version="0.3.0-cloud-ml",
)

s3 = boto3.client("s3")

LAKE_BUCKET = os.environ["LAKE_BUCKET"]
LIVE_SNAPSHOT_KEY = "live/latest.json"
HOURLY_STATS_KEY = "stats/hourly.json"

# Module-level caches: a warm Lambda container reuses these across
# invocations instead of re-reading tiny S3 objects every request. Cold
# starts pay one extra read each; negligible.
_corridors_cache: list[dict] | None = None
_anomaly_threshold_cache: float | None = None
_forecast_model_cache = None
_forecast_mae_cache: float | None = None

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
    # Cap raised from 2000 to 6000: the previous cap silently truncated a
    # full-Europe snapshot (~3,600+ aircraft and climbing) to whatever
    # arrived first in live/latest.json's list — which merge order (fastest-
    # responding adsb.lol point wins ties) skews toward one geographic hub,
    # not a random cross-section. A caller asking for "everything" got a
    # biased subset of one region instead, not an even sample of Europe.
    flights = data.get("flights", [])[: min(limit, 6000)]
    return {"count": len(flights), "flights": flights, "updated_at": data.get("updated_at")}


@app.get("/api/stats/overview")
def stats_overview() -> dict:
    flights = _live_flights().get("flights", [])
    altitudes_m = [f["baro_altitude"] for f in flights if f.get("baro_altitude") is not None]
    countries = {f["origin_country"] for f in flights if f.get("origin_country")}
    avg_altitude_ft = (sum(altitudes_m) / len(altitudes_m) * 3.28084) if altitudes_m else None

    # anomaly_count stays null (not a real 0) whenever ML artifacts aren't
    # present -- a bare 0 would read as a false "nothing unusual" claim.
    all_corridors = _get_corridors()
    threshold = _get_anomaly_threshold()
    ml_paused = not all_corridors or threshold is None
    anomaly_count = None if ml_paused else anomalies(page=1, page_size=10_000)["total"]

    return {
        "active_flights": len(flights),
        "countries": len(countries),
        "avg_altitude_ft": avg_altitude_ft,
        "anomaly_count": anomaly_count,
        "ml_paused": ml_paused,
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


def _get_corridors() -> list[dict]:
    global _corridors_cache
    if _corridors_cache is None:
        _corridors_cache = _load_json("models/corridors.json", [])
    return _corridors_cache


def _get_anomaly_threshold() -> float | None:
    global _anomaly_threshold_cache
    if _anomaly_threshold_cache is None:
        try:
            obj = s3.get_object(Bucket=LAKE_BUCKET, Key="models/anomaly_threshold.txt")
            _anomaly_threshold_cache = float(obj["Body"].read())
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise
    return _anomaly_threshold_cache


@app.get("/api/corridors")
def corridors(limit: int = 20) -> dict:
    all_corridors = _get_corridors()
    if not all_corridors:
        # Artifacts not trained/uploaded yet -- honest empty state, not an error.
        return {"total_corridors": 0, "returned": 0, "corridors": [], "ml_paused": True}
    ranked = sorted(all_corridors, key=lambda c: c["member_count"], reverse=True)[:limit]
    return {
        "total_corridors": len(all_corridors),
        "returned": len(ranked),
        "corridors": ranked,
        "ml_paused": False,
    }


@app.get("/api/anomalies")
def anomalies(page: int = 1, page_size: int = 50, flagged_only: bool = True) -> dict:
    all_corridors = _get_corridors()
    threshold = _get_anomaly_threshold()
    if not all_corridors or threshold is None:
        return {"total": 0, "page": page, "page_size": page_size, "events": [], "ml_paused": True}

    flights = _live_flights().get("flights", [])
    centroids = np.array([[c["centroid_lat"], c["centroid_lon"]] for c in all_corridors])

    events = []
    for f in flights:
        lat, lon = f.get("latitude"), f.get("longitude")
        if lat is None or lon is None or f.get("on_ground"):
            continue
        dists = np.sqrt(((centroids[:, 0] - lat) ** 2) + ((centroids[:, 1] - lon) ** 2))
        nearest_idx = int(np.argmin(dists))
        nearest_dist_deg = float(dists[nearest_idx])
        if flagged_only and nearest_dist_deg <= threshold:
            continue

        nearest = all_corridors[nearest_idx]
        heading = f.get("true_track")
        heading_dev = None
        if heading is not None:
            diff = abs(heading - nearest["modal_heading_deg"]) % 360
            heading_dev = min(diff, 360 - diff)

        # Altitude z-score approximated from stored percentiles (p10/p90
        # spans ~2.56 std devs for a roughly normal distribution) -- we
        # don't keep raw per-corridor altitude samples at serve time, only
        # these 3 percentiles, so this is a deliberate approximation, not a
        # precise z-score.
        alt_ft = (f["baro_altitude"] * 3.28084) if f.get("baro_altitude") is not None else None
        altitude_z = None
        if alt_ft is not None:
            spread = (nearest["altitude_p90_ft"] - nearest["altitude_p10_ft"]) / 2.56
            if spread > 0:
                altitude_z = (alt_ft - nearest["altitude_p50_ft"]) / spread

        events.append(
            {
                "icao24": f.get("icao24"),
                "callsign": f.get("callsign"),
                "origin_country": f.get("origin_country"),
                "ingest_ts": f.get("ingest_ts"),
                "latitude": lat,
                "longitude": lon,
                "altitude_ft": alt_ft,
                "speed_kmh": (f["velocity"] * 3.6) if f.get("velocity") is not None else None,
                # How many multiples of the flag threshold this aircraft is
                # from its nearest corridor -- always > 1.0 for flagged
                # events (unbounded above, not a 0-1 probability).
                "anomaly_score": round(nearest_dist_deg / threshold, 2),
                "anomaly_type": "corridor_deviation",
                "nearest_corridor_id": nearest["corridor_id"],
                "lateral_distance_km": round(nearest_dist_deg * 111.0, 1),  # rough deg->km
                "heading_deviation_deg": heading_dev,
                "altitude_z": altitude_z,
                "unassigned_corridor": True,
            }
        )

    events.sort(key=lambda e: e["anomaly_score"], reverse=True)
    total = len(events)
    start = (page - 1) * page_size
    page_events = events[start : start + page_size]
    return {"total": total, "page": page, "page_size": page_size, "events": page_events, "ml_paused": False}


def _get_forecast_model():
    global _forecast_model_cache, _forecast_mae_cache
    if _forecast_model_cache is None:
        try:
            obj = s3.get_object(Bucket=LAKE_BUCKET, Key="models/forecast_gbr.joblib")
            _forecast_model_cache = joblib.load(io.BytesIO(obj["Body"].read()))
            mae_obj = s3.get_object(Bucket=LAKE_BUCKET, Key="models/forecast_mae.txt")
            _forecast_mae_cache = float(mae_obj["Body"].read())
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                return None, None
            raise
    return _forecast_model_cache, _forecast_mae_cache


@app.get("/api/forecast/traffic")
def forecast_traffic(hours: int = 6) -> dict:
    model, mae = _get_forecast_model()
    if model is None:
        return {"trained_on_synthetic_history": False, "points": []}

    hist = _load_json(HOURLY_STATS_KEY, {"hours": []}).get("hours", [])
    if len(hist) < 24:
        # Not enough real history yet for a lag24 feature -- honest empty
        # response rather than a guess with a fabricated lag.
        return {"trained_on_synthetic_history": False, "points": []}

    counts = [h["flight_count"] for h in hist]
    last_hour = hist[-1]["hour"]  # ISO string, e.g. "2026-08-29T09:00:00Z"
    from datetime import datetime, timedelta, timezone

    cursor = datetime.fromisoformat(last_hour.replace("Z", "+00:00"))

    points = []
    working_counts = list(counts)  # grows with each recursive prediction
    for _ in range(hours):
        cursor = cursor + timedelta(hours=1)
        lag1 = working_counts[-1]
        lag24 = working_counts[-24] if len(working_counts) >= 24 else sum(working_counts) / len(
            working_counts
        )
        X = np.array([[cursor.hour, cursor.weekday(), lag1, lag24]])
        pred = float(model.predict(X)[0])
        working_counts.append(pred)
        points.append(
            {
                "hour_bucket": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "predicted_flight_count": round(pred),
                # Symmetric +-MAE band, not true quantile regression -- an
                # honest approximation labeled as such, not a real
                # confidence interval.
                "lower_bound": round(max(pred - mae, 0)),
                "upper_bound": round(pred + mae),
            }
        )

    return {"trained_on_synthetic_history": False, "points": points}


handler = Mangum(app)
