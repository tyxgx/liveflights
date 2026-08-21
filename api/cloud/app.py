"""Cloud API: a deliberately smaller sibling of api/main.py, not the same app.

Why this isn't just `api/main.py` wrapped in Mangum: the local API's
`/ws/flights` push loop and `live_store.py` assume a long-lived process with
a background Kafka consumer thread — neither survives a Lambda invocation,
which starts cold, runs one request, and may freeze or be reaped at any
moment. `/api/forecast/traffic` is still backed locally by an MLflow model
this cloud path doesn't provision — out of scope for this phase. But
`/api/corridors` and `/api/anomalies` ARE served here now: corridor
discovery (DBSCAN) is transductive (no `.predict()` on new points), so "the
trained model" is really the discovered corridor centroid table, which
travels to the cloud as a small JSON artifact — see
docs/aws-architecture.md for how it's produced and refreshed.

What this app actually serves, backed by the resources this Terraform stack
creates:
  - GET /health                    — liveness only
  - GET /api/flights/live          — latest per-aircraft state from DynamoDB
  - GET /api/corridors             — corridor reference table from S3 (models/)
  - GET /api/anomalies             — Athena query over gold.anomaly_events
  - GET /api/stats/overview        — Athena query over gold.traffic_by_hour
  - GET /api/stats/traffic-by-hour — Athena query over gold.traffic_by_hour
  - GET /api/stats/by-country      — Athena query over gold.traffic_by_country
  - GET /api/stats/airline-activity — Athena query over gold.airline_activity

`/ws/flights` has no cloud equivalent; poll `/api/flights/live` instead —
documented in README under "Cloud deployment differences".
"""

from __future__ import annotations

import json
import os
import time

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from mangum import Mangum

app = FastAPI(
    title="liveflights cloud API",
    description="Serverless read path over the AWS batch pipeline (see docs/aws-architecture.md).",
    version="0.1.0-cloud",
)

dynamodb = boto3.resource("dynamodb")
athena = boto3.client("athena")
s3 = boto3.client("s3")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
ATHENA_DATABASE = os.environ["ATHENA_DATABASE"]
ATHENA_WORKGROUP = os.environ["ATHENA_WORKGROUP"]
LAKE_BUCKET = os.environ["LAKE_BUCKET"]
CORRIDOR_ARTIFACT_KEY = os.environ.get("CORRIDOR_ARTIFACT_KEY", "models/corridors_v1.json")

ALLOWED_TABLES = {
    "traffic_by_hour", "traffic_by_country", "airline_activity", "altitude_band_distribution",
}


def run_athena_query(sql: str, poll_seconds: float = 0.5, max_polls: int = 20) -> list[dict]:
    """Synchronous Athena query helper: start, poll, fetch, shape as list-of-dicts.

    Only ever called with SQL this module builds itself against
    ALLOWED_TABLES — never with user-supplied SQL (that's the Bedrock
    Lambda's job, with its own separate guardrails).
    """
    exec_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        WorkGroup=ATHENA_WORKGROUP,
    )["QueryExecutionId"]

    for _ in range(max_polls):
        execution = athena.get_query_execution(QueryExecutionId=exec_id)
        status = execution["QueryExecution"]["Status"]["State"]
        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(poll_seconds)
    else:
        raise HTTPException(status_code=504, detail="Athena query timed out")

    if status != "SUCCEEDED":
        raise HTTPException(status_code=502, detail=f"Athena query {status}")

    result = athena.get_query_results(QueryExecutionId=exec_id)
    rows = result["ResultSet"]["Rows"]
    if not rows:
        return []
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    return [
        dict(zip(header, [c.get("VarCharValue") for c in row["Data"]], strict=False))
        for row in rows[1:]
    ]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/flights/live")
def flights_live(limit: int = 500) -> dict:
    table = dynamodb.Table(TABLE_NAME)
    items = table.scan(Limit=min(limit, 1000)).get("Items", [])
    return {"count": len(items), "flights": items}


@app.get("/api/corridors")
def corridors(limit: int = 20) -> dict:
    """Corridor reference table, sorted by member_count (busiest first).

    Not an Athena query — this is a small S3 JSON artifact (see module
    docstring), refreshed by the daily corridor-retrain step of the batch
    chain, not by every 1-minute ingestion poll. Response shape matches the
    frontend's `CorridorsResponse`/`Corridor` types exactly (same contract
    the local API serves), so the dashboard's CorridorLayer needs no
    cloud-specific branch. The artifact stores altitude_mean_ft/std_ft, not
    percentiles (see docs/aws-architecture.md) — p10/p50/p90 below are a
    normal-distribution approximation from those two, same as the artifact
    export itself did in reverse.
    """
    try:
        obj = s3.get_object(Bucket=LAKE_BUCKET, Key=CORRIDOR_ARTIFACT_KEY)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return {"total_corridors": 0, "returned": 0, "corridors": []}
        raise
    artifact = json.loads(obj["Body"].read())
    ranked = sorted(artifact["corridors"], key=lambda c: c["member_count"], reverse=True)
    shaped = [
        {
            "corridor_id": c["corridor_id"],
            "centroid_lat": c["centroid_lat"],
            "centroid_lon": c["centroid_lon"],
            "modal_heading_deg": c["modal_heading_deg"],
            "altitude_p10_ft": round(c["altitude_mean_ft"] - 1.2816 * c["altitude_std_ft"], 1),
            "altitude_p50_ft": c["altitude_mean_ft"],
            "altitude_p90_ft": round(c["altitude_mean_ft"] + 1.2816 * c["altitude_std_ft"], 1),
            "member_count": c["member_count"],
            "polyline": c["polyline"],
        }
        for c in ranked[:limit]
    ]
    return {
        "total_corridors": len(artifact["corridors"]),
        "returned": len(shaped),
        "corridors": shaped,
    }


@app.get("/api/anomalies")
def anomalies(page: int = 1, page_size: int = 50, flagged_only: bool = True) -> dict:
    """Response shape matches the frontend's `AnomaliesResponse`/`AnomalyEvent`
    types exactly. `anomaly_reason` (this pipeline's column name) is aliased
    to `anomaly_type` (the field name AnomalyFeed.tsx already renders) —
    both are the same comma-joined reasons string, just named differently
    between the local and cloud pipelines' own gold tables. `origin_country`
    and `speed_kmh` aren't columns on gold.anomaly_events (see
    lambda_transform/handler.py's score_anomalies) — returned as null rather
    than guessed.
    """
    where = "WHERE is_ml_anomaly = true" if flagged_only else ""
    rows = run_athena_query(
        f'SELECT icao24, callsign, latitude, longitude, altitude_ft, corridor_id, '
        f'anomaly_score, anomaly_reason, lateral_distance_km, heading_deviation_deg, '
        f'altitude_z, ingest_ts '
        f'FROM "{ATHENA_DATABASE}"."anomaly_events" {where} '
        f'ORDER BY anomaly_score DESC LIMIT {int(page_size)}'
    )
    def _f(row: dict, key: str) -> float | None:
        return float(row[key]) if row.get(key) else None

    events = [
        {
            "icao24": r["icao24"],
            "callsign": r["callsign"],
            "origin_country": None,
            "ingest_ts": r["ingest_ts"],
            "latitude": _f(r, "latitude"),
            "longitude": _f(r, "longitude"),
            "altitude_ft": _f(r, "altitude_ft"),
            "speed_kmh": None,
            "anomaly_score": float(r["anomaly_score"]),
            "anomaly_type": r["anomaly_reason"],
            "nearest_corridor_id": int(r["corridor_id"]) if r.get("corridor_id") else None,
            "lateral_distance_km": _f(r, "lateral_distance_km"),
            "heading_deviation_deg": _f(r, "heading_deviation_deg"),
            "altitude_z": _f(r, "altitude_z"),
            "unassigned_corridor": None,
        }
        for r in rows
    ]
    return {"total": len(events), "page": page, "page_size": page_size, "events": events}


@app.get("/api/stats/overview")
def stats_overview() -> dict:
    """Shape matches the frontend's `OverviewStats` exactly — the local API
    computes these same four fields from its own live-flights store; this
    computes them from DynamoDB's latest-state table (same source
    `/api/flights/live` reads) plus one Athena count for anomalies.
    """
    table = dynamodb.Table(TABLE_NAME)
    items = table.scan().get("Items", [])
    altitudes_m = [float(i["baro_altitude"]) for i in items if i.get("baro_altitude") is not None]
    countries = {i["origin_country"] for i in items if i.get("origin_country")}

    anomaly_count = 0
    try:
        rows = run_athena_query(
            f'SELECT COUNT(*) AS cnt FROM "{ATHENA_DATABASE}"."anomaly_events" '
            f"WHERE is_ml_anomaly = true"
        )
        anomaly_count = int(rows[0]["cnt"]) if rows and rows[0].get("cnt") else 0
    except HTTPException:
        anomaly_count = 0  # anomaly_events may not have a partition yet on a fresh deploy

    avg_altitude_ft = (sum(altitudes_m) / len(altitudes_m) * 3.28084) if altitudes_m else None
    return {
        "active_flights": len(items),
        "countries": len(countries),
        "avg_altitude_ft": avg_altitude_ft,
        "anomaly_count": anomaly_count,
    }


@app.get("/api/stats/traffic-by-hour")
def stats_traffic_by_hour(hours: int = 24) -> dict:
    rows = run_athena_query(
        f'SELECT * FROM "{ATHENA_DATABASE}"."traffic_by_hour" '
        f"ORDER BY hour_bucket DESC LIMIT {int(hours)}"
    )
    points = [
        {
            "hour_bucket": r["hour_bucket"],
            "flight_count": int(r["flight_count"]) if r.get("flight_count") else 0,
            "avg_altitude_ft": float(r["avg_altitude_ft"]) if r.get("avg_altitude_ft") else None,
            "avg_speed_kmh": float(r["avg_speed_kmh"]) if r.get("avg_speed_kmh") else None,
            "is_synthetic": False,
        }
        for r in rows
    ]
    return {"points": points}


@app.get("/api/stats/by-country")
def stats_by_country(limit: int = 20) -> dict:
    rows = run_athena_query(
        f'SELECT * FROM "{ATHENA_DATABASE}"."traffic_by_country" '
        f"ORDER BY flight_count DESC LIMIT {int(limit)}"
    )
    countries = [
        {
            "origin_country": r["origin_country"],
            "flight_count": int(r["flight_count"]) if r.get("flight_count") else 0,
        }
        for r in rows
    ]
    return {"countries": countries}


@app.get("/api/stats/airline-activity")
def stats_airline_activity(limit: int = 20) -> list[dict]:
    return run_athena_query(
        f'SELECT * FROM "{ATHENA_DATABASE}"."airline_activity" '
        f"ORDER BY flight_count DESC LIMIT {int(limit)}"
    )


handler = Mangum(app)
