"""Lambda: bronze -> silver -> gold, invoked synchronously by the batch-chain state machine.

Ported from the original Glue Python Shell job design (Glue's own
`glue:CreateJob`/`glue:CreateCrawler` are blocked by an account-level
restriction on this account — see docs/aws-architecture.md). At this data
volume (a few thousand rows per 5-minute poll) a 512 MB Lambda with
pandas/pyarrow does the same work as a 1/16-DPU Glue Python Shell job would
have, without needing Glue jobs at all. The Glue *Catalog* (tables, not jobs)
is unaffected by the restriction, so this function still registers each run's
output as a new partition via `glue:BatchCreatePartition` — the same catalog
bookkeeping a crawler would have done, just driven explicitly instead of
inferred by crawling.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from datetime import UTC, datetime

import boto3
import numpy as np
import pandas as pd

s3 = boto3.client("s3")
glue = boto3.client("glue")

BUCKET = os.environ["LAKE_BUCKET"]
INPUT_PREFIX = os.environ.get("INPUT_PREFIX", "bronze/")
GLUE_DATABASE = os.environ["GLUE_DATABASE"]
ML_ARTIFACT_KEY = os.environ.get("ML_ARTIFACT_KEY", "models/corridors_v1.json")
EARTH_RADIUS_KM = 6371.0

# --- ported from streaming/utils/enrich.py ---

REGION_BOXES: list[tuple[str, float, float, float, float]] = [
    ("Europe", 34.0, 72.0, -25.0, 45.0),
    ("North America", 5.0, 72.0, -170.0, -50.0),
    ("South America", -56.0, 15.0, -82.0, -34.0),
    ("Africa", -35.0, 37.0, -20.0, 52.0),
    ("South Asia", 5.0, 38.0, 60.0, 100.0),
    ("Asia", -10.0, 55.0, 45.0, 150.0),
    ("Oceania", -50.0, 0.0, 110.0, 180.0),
]
EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}
MAX_PLAUSIBLE_VELOCITY_MPS = 400.0
MAX_PLAUSIBLE_ALTITUDE_M = 15000.0
MAX_PLAUSIBLE_VERTICAL_RATE_MPS = 50.0
STALE_CONTACT_THRESHOLD_S = 60

# --- ported from streaming/utils/airlines.py ---

AIRLINE_PREFIXES: dict[str, str] = {
    "DLH": "Lufthansa", "RYR": "Ryanair", "BAW": "British Airways", "AFR": "Air France",
    "KLM": "KLM", "EZY": "easyJet", "WZZ": "Wizz Air", "VLG": "Vueling", "IBE": "Iberia",
    "SWR": "Swiss", "AUA": "Austrian Airlines", "SAS": "SAS", "FIN": "Finnair",
    "LOT": "LOT Polish Airlines", "TAP": "TAP Air Portugal", "THY": "Turkish Airlines",
    "AAL": "American Airlines", "UAL": "United Airlines", "DAL": "Delta Air Lines",
    "SWA": "Southwest Airlines", "JBU": "JetBlue", "ASA": "Alaska Airlines",
    "FDX": "FedEx Express", "UPS": "UPS Airlines", "ACA": "Air Canada",
    "QTR": "Qatar Airways", "UAE": "Emirates", "ETD": "Etihad Airways",
    "AIC": "Air India", "IGO": "IndiGo", "SEJ": "SpiceJet", "AXB": "Air India Express",
    "AKJ": "Akasa Air", "VTI": "Vistara", "GOW": "Go First", "LLR": "Alliance Air",
}


def region_bucket(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "Unknown"
    for name, lat_min, lat_max, lon_min, lon_max in REGION_BOXES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return "Other"


def flight_phase(on_ground: bool, vertical_rate: float | None) -> str:
    if on_ground:
        return "ground"
    if vertical_rate is None:
        return "cruise"
    if vertical_rate > 1.0:
        return "climb"
    if vertical_rate < -1.0:
        return "descent"
    return "cruise"


def speed_kmh(velocity_mps: float | None) -> float | None:
    return None if velocity_mps is None else round(velocity_mps * 3.6, 2)


def altitude_ft(baro_m: float | None, geo_m: float | None) -> float | None:
    meters = baro_m if baro_m is not None else geo_m
    return None if meters is None else round(meters * 3.28084, 1)


def data_quality_flags(row: pd.Series) -> list[str]:
    flags: list[str] = []
    if pd.isna(row.get("latitude")) or pd.isna(row.get("longitude")):
        flags.append("missing_position")
    tp, lc = row.get("time_position"), row.get("last_contact")
    if pd.isna(tp) or (not pd.isna(lc) and not pd.isna(tp) and lc - tp > STALE_CONTACT_THRESHOLD_S):
        flags.append("stale_contact")
    v = row.get("velocity")
    if not pd.isna(v) and (v > MAX_PLAUSIBLE_VELOCITY_MPS or v < 0):
        flags.append("implausible_speed")
    alt = row.get("baro_altitude")
    if not pd.isna(alt) and (alt > MAX_PLAUSIBLE_ALTITUDE_M or alt < -500):
        flags.append("implausible_altitude")
    vr = row.get("vertical_rate")
    if not pd.isna(vr) and abs(vr) > MAX_PLAUSIBLE_VERTICAL_RATE_MPS:
        flags.append("implausible_vertical_rate")
    if row.get("squawk") in EMERGENCY_SQUAWKS:
        flags.append("emergency_squawk")
    return flags


def callsign_to_airline(callsign: str | None) -> str:
    # pd.isna (not `not callsign`) — the nullable "string" dtype's pd.NA
    # raises TypeError on a plain truthiness check ("boolean value of NA is
    # ambiguous"), unlike None/"" on a plain object dtype.
    if pd.isna(callsign) or len(str(callsign).strip()) < 3:
        return "Unknown/Other"
    return AIRLINE_PREFIXES.get(str(callsign).strip().upper()[:3], "Unknown/Other")


def read_bronze(bucket: str, prefix: str) -> pd.DataFrame:
    """Read every gzip-NDJSON object under the bronze prefix into one frame."""
    paginator = s3.get_paginator("list_objects_v2")
    rows: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "bronze-errors" in key or not key.endswith(".gz"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            text = gzip.decompress(body).decode()
            for line in text.splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return pd.DataFrame(rows)


# Fixed dtypes applied right before every Parquet write, so each run's
# physical Parquet schema matches the Glue Catalog table definition exactly —
# without this, pandas' dtype inference could drift between runs (e.g.
# int64 -> float64 the moment a single null appears) and break Athena reads
# across partitions written by different runs.
SILVER_DTYPES = {
    "icao24": "string", "callsign": "string", "origin_country": "string",
    "longitude": "float64", "latitude": "float64", "baro_altitude": "float64",
    "on_ground": "boolean", "velocity": "float64", "true_track": "float64",
    "vertical_rate": "float64", "geo_altitude": "float64", "squawk": "string",
    "spi": "boolean", "position_source": "Int64", "ingest_ts": "string", "source": "string",
    "region": "string", "flight_phase": "string", "speed_kmh": "float64",
    "altitude_ft": "float64", "ingest_date": "string", "ingest_hour": "string",
}


def build_silver(bronze: pd.DataFrame) -> pd.DataFrame:
    if bronze.empty:
        return bronze

    df = bronze.copy()
    df = df.sort_values("ingest_ts").drop_duplicates(
        subset=["icao24", "time_position"], keep="last"
    )

    df["region"] = df.apply(lambda r: region_bucket(r.get("latitude"), r.get("longitude")), axis=1)
    df["flight_phase"] = df.apply(
        lambda r: flight_phase(bool(r.get("on_ground")), r.get("vertical_rate")), axis=1
    )
    df["speed_kmh"] = df["velocity"].apply(speed_kmh)
    df["altitude_ft"] = df.apply(
        lambda r: altitude_ft(r.get("baro_altitude"), r.get("geo_altitude")), axis=1
    )
    df["data_quality_flags"] = df.apply(data_quality_flags, axis=1)
    df["ingest_date"] = pd.to_datetime(df["ingest_ts"]).dt.strftime("%Y-%m-%d")
    df["ingest_hour"] = pd.to_datetime(df["ingest_ts"]).dt.strftime("%H")

    df["time_position"] = pd.to_numeric(df.get("time_position"), errors="coerce").astype("Int64")
    df["last_contact"] = pd.to_numeric(df.get("last_contact"), errors="coerce").astype("Int64")
    for col, dtype in SILVER_DTYPES.items():
        if col in df.columns:
            df[col] = df[col].astype(dtype)
    return df


def build_gold(silver: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if silver.empty:
        return {}

    silver = silver.copy()
    silver["hour_bucket"] = pd.to_datetime(silver["ingest_ts"]).dt.floor("h")

    traffic_by_hour = (
        silver.groupby("hour_bucket")
        .agg(
            flight_count=("icao24", "nunique"),
            avg_altitude_ft=("altitude_ft", "mean"),
            avg_speed_kmh=("speed_kmh", "mean"),
        )
        .reset_index()
    )
    traffic_by_hour["hour_bucket"] = traffic_by_hour["hour_bucket"].astype("string")

    traffic_by_country = (
        silver.groupby("origin_country").agg(flight_count=("icao24", "nunique")).reset_index()
        .sort_values("flight_count", ascending=False)
    )

    silver["airline"] = silver["callsign"].apply(callsign_to_airline)
    airline_activity = (
        silver.groupby("airline").agg(flight_count=("icao24", "nunique")).reset_index()
        .sort_values("flight_count", ascending=False)
    )

    bins = [-1000, 0, 5000, 15000, 25000, 35000, 45000, 100000]
    labels = ["ground", "0-5k", "5-15k", "15-25k", "25-35k", "35-45k", "45k+"]
    silver["altitude_band"] = pd.cut(silver["altitude_ft"].fillna(-1), bins=bins, labels=labels)
    altitude_band_distribution = (
        silver.groupby("altitude_band", observed=True).size().reset_index(name="flight_count")
    )
    altitude_band_distribution["altitude_band"] = (
        altitude_band_distribution["altitude_band"].astype("string")
    )

    return {
        "traffic_by_hour": traffic_by_hour,
        "traffic_by_country": traffic_by_country,
        "airline_activity": airline_activity,
        "altitude_band_distribution": altitude_band_distribution,
    }


# --- Cloud anomaly scoring, ported from ml/anomaly.py's score_points()/reasons() ---
#
# Reuses the *reference-table* approach ml/corridors.py + ml/anomaly.py
# already use locally: DBSCAN is transductive (no .predict() on new points),
# so "the trained model" is really the discovered corridor centroid table,
# not a pickled sklearn object — that table travels to the cloud as a small
# JSON artifact at `ml-artifacts/corridors.json` (trained from the cloud's
# own accumulated silver data; see docs/aws-architecture.md for how it was
# produced) and is loaded once per cold start, not re-fit here.


def _load_ml_artifact() -> dict | None:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=ML_ARTIFACT_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


_ML_ARTIFACT = _load_ml_artifact()


def _haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _angular_diff_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    diff = (a - b + 180) % 360 - 180
    return np.abs(diff)


def score_anomalies(silver: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    """Score cruise-phase rows against the corridor reference table. Identical
    formula to ml/anomaly.py's score_points()/reasons(), operating on
    cloud silver instead of the local Postgres-backed silver mirror."""
    corridors = pd.DataFrame(artifact["corridors"])
    params = artifact["scoring_params"]
    lateral_scale = params["lateral_distance_scale_km"]
    heading_scale = params["heading_deviation_scale_deg"]
    altitude_scale = params["altitude_z_scale"]

    cruise = silver[(~silver["on_ground"]) & (silver["flight_phase"] == "cruise")].copy()
    cruise = cruise.dropna(subset=["latitude", "longitude", "true_track"])
    if cruise.empty or corridors.empty:
        return cruise

    centroids = corridors[["corridor_id", "centroid_lat", "centroid_lon"]].to_numpy()
    nearest_ids = np.empty(len(cruise), dtype=int)
    nearest_dist = np.empty(len(cruise))
    for i, (lat, lon) in enumerate(zip(cruise["latitude"], cruise["longitude"], strict=True)):
        dists = _haversine_km(
            lat, lon, centroids[:, 1].astype(float), centroids[:, 2].astype(float)
        )
        j = int(np.argmin(dists))
        nearest_ids[i] = int(centroids[j, 0])
        nearest_dist[i] = dists[j]

    cruise["corridor_id"] = nearest_ids
    cruise["lateral_distance_km"] = nearest_dist

    lookup = corridors.set_index("corridor_id")
    cruise["corridor_heading"] = cruise["corridor_id"].map(lookup["modal_heading_deg"])
    cruise["corridor_alt_mean"] = cruise["corridor_id"].map(lookup["altitude_mean_ft"])
    cruise["corridor_alt_std"] = cruise["corridor_id"].map(lookup["altitude_std_ft"])

    cruise["heading_deviation_deg"] = _angular_diff_deg(
        cruise["true_track"], cruise["corridor_heading"]
    )
    cruise["altitude_z"] = (
        (cruise["altitude_ft"] - cruise["corridor_alt_mean"]) / cruise["corridor_alt_std"]
    )

    lateral_component = (cruise["lateral_distance_km"] / lateral_scale).clip(0, 1)
    heading_component = (cruise["heading_deviation_deg"] / heading_scale).clip(0, 1)
    altitude_component = (cruise["altitude_z"].abs() / altitude_scale).clip(0, 1)

    cruise["anomaly_score"] = (
        lateral_component.fillna(1.0) * (1 / 3)
        + heading_component.fillna(1.0) * (1 / 3)
        + altitude_component.fillna(1.0) * (1 / 3)
    )
    cruise["is_ml_anomaly"] = cruise["anomaly_score"] > params["anomaly_score_threshold"]

    def _reason(row: pd.Series) -> str:
        r = []
        if row["lateral_distance_km"] / lateral_scale > 0.5:
            r.append("far_from_corridor")
        if row["heading_deviation_deg"] / heading_scale > 0.5:
            r.append("heading_deviation")
        if abs(row["altitude_z"]) / altitude_scale > 0.5:
            r.append("altitude_outlier")
        return ",".join(r) if r else "none"

    cruise["anomaly_reason"] = cruise.apply(_reason, axis=1)

    return cruise[
        [
            "icao24", "callsign", "time_position", "latitude", "longitude", "altitude_ft",
            "true_track", "corridor_id", "lateral_distance_km", "heading_deviation_deg",
            "altitude_z", "anomaly_score", "anomaly_reason", "is_ml_anomaly", "ingest_ts",
        ]
    ].astype({
        "icao24": "string", "callsign": "string", "time_position": "Int64",
        "latitude": "float64", "longitude": "float64", "altitude_ft": "float64",
        "true_track": "float64", "corridor_id": "int64", "lateral_distance_km": "float64",
        "heading_deviation_deg": "float64", "altitude_z": "float64", "anomaly_score": "float64",
        "anomaly_reason": "string", "is_ml_anomaly": "boolean", "ingest_ts": "string",
    })


def write_partition(table: str, df: pd.DataFrame, run_id: str) -> None:
    """Write one run's output as a Hive-style partition, then register it in Glue Catalog."""
    prefix = "silver" if table == "silver" else f"gold/{table}"
    key = f"{prefix}/run_ts={run_id}/{table}.parquet"
    location = f"s3://{BUCKET}/{prefix}/run_ts={run_id}/"

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue())

    table_info = glue.get_table(DatabaseName=GLUE_DATABASE, Name=table)["Table"]
    storage_descriptor = dict(table_info["StorageDescriptor"])
    storage_descriptor["Location"] = location
    try:
        glue.create_partition(
            DatabaseName=GLUE_DATABASE,
            TableName=table,
            PartitionInput={"Values": [run_id], "StorageDescriptor": storage_descriptor},
        )
    except glue.exceptions.AlreadyExistsException:
        pass


# --- Daily corridor retrain, ported from ml/corridors.py's discover() ---
#
# Scoped to a single region ("india") rather than corridors.py's
# multi-region loop, since that's the only region the cloud pipeline's own
# ingestion (adsb.lol centered on India) ever populates. Triggered by a
# separate daily EventBridge Schedule invoking this Lambda directly with
# {"retrain_corridors": true} — NOT part of the per-minute batch chain,
# since DBSCAN over the full accumulated history on every micro-batch would
# be wasteful and corridors are a slowly-changing structure.

MIN_SAMPLES = 8
MIN_ROWS_FOR_FIT = MIN_SAMPLES * 2  # bare minimum for DBSCAN to be numerically stable

# Separate, much higher bar for actually REPLACING the canonical artifact.
# The locally-trained set (271 corridors from 29,785 accumulated silver
# rows, built over a much longer-running deployment) is real signal; the
# cloud pipeline's own accumulated data starts from zero the moment this
# stack is deployed. Retraining is still useful to run daily (it's cheap,
# and produces a versioned snapshot showing how the cloud-only fit is
# maturing), but overwriting the canonical `ML_ARTIFACT_KEY` — the one the
# API actually serves — before the cloud data is genuinely comparable would
# make the dashboard's corridor set to get WORSE, not better, on day one.
# 5,000 cruise rows is roughly a day's worth at this pipeline's observed
# ingestion rate (~1,300 cruise rows in the first ~1.5h of adsb.lol
# polling) — a deliberately conservative bar, not a precise one.
MIN_ROWS_TO_REPLACE_CANONICAL = 5000


def _choose_eps_via_knee(scaled, k: int) -> float:
    from sklearn.neighbors import NearestNeighbors

    nn = NearestNeighbors(n_neighbors=k).fit(scaled)
    distances, _ = nn.kneighbors(scaled)
    k_distances = np.sort(distances[:, -1])
    x = np.arange(len(k_distances))
    p1, p2 = np.array([x[0], k_distances[0]]), np.array([x[-1], k_distances[-1]])
    line_unit = (p2 - p1) / np.linalg.norm(p2 - p1)
    points = np.column_stack([x, k_distances]) - p1
    proj = np.outer(points @ line_unit, line_unit)
    perp_dist = np.linalg.norm(points - proj, axis=1)
    return float(k_distances[int(np.argmax(perp_dist))])


def _modal_heading(track_sin: pd.Series, track_cos: pd.Series) -> float:
    return float(np.degrees(np.arctan2(track_sin.mean(), track_cos.mean())) % 360)


def _build_polyline(sub: pd.DataFrame, n_points: int = 10) -> list[list[float]]:
    ordered = sub.sort_values("latitude")
    idx = np.linspace(0, len(ordered) - 1, min(n_points, len(ordered))).astype(int)
    sample = ordered.iloc[idx]
    return [[round(r.latitude, 4), round(r.longitude, 4)] for r in sample.itertuples()]



# Reading every silver partition ever written assumed the original design
# volume (~1,300 cruise rows in 1.5h of India-region polling) — at Europe's
# actual volume (measured: ~2.75M cruise rows accumulated in ~4h post
# region-switch, ~300K total rows PER 15-minute batch-chain partition) that
# assumption OOM-killed a 2048MB Lambda outright on the very first retrain
# attempt. Even 3 partitions (~900K rows) still OOM'd — one partition alone
# clears MIN_ROWS_TO_REPLACE_CANONICAL by ~60x, so there's no accuracy
# reason to read more; recency-bounding this hard is strictly an
# improvement anyway (corridors should reflect current traffic, not an
# ever-growing all-time blend).
MAX_RETRAIN_PARTITIONS = 1


def retrain_corridors(region: str = "india") -> dict:
    """Reads the most recent silver partitions, re-fits DBSCAN, overwrites
    the corridor artifact in S3. Returns a report dict — including an
    honest call on whether there was enough data for a meaningful fit."""
    from sklearn.cluster import DBSCAN
    from sklearn.preprocessing import StandardScaler

    paginator = s3.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=BUCKET, Prefix="silver/")
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".parquet")
    ]
    # run_ts is embedded in the key path (silver/run_ts=<...>/silver.parquet)
    # so a plain lexicographic sort is already a chronological sort.
    keys.sort(reverse=True)
    keys = keys[:MAX_RETRAIN_PARTITIONS]

    frames = []
    for key in keys:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        frames.append(pd.read_parquet(io.BytesIO(body)))
    if not frames:
        return {"retrained": False, "reason": "no silver data found"}

    silver = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["icao24", "time_position"]
    )
    is_cruise = (~silver["on_ground"].astype(bool)) & (silver["flight_phase"] == "cruise")
    cruise = silver[is_cruise].copy()
    cruise = cruise.dropna(subset=["latitude", "longitude", "true_track"])

    report = {"retrained": False, "region": region, "input_rows": len(cruise)}

    if len(cruise) < MIN_ROWS_FOR_FIT:
        report["reason"] = (
            f"only {len(cruise)} cruise rows accumulated, need >= {MIN_ROWS_FOR_FIT} for a "
            "stable DBSCAN fit — keeping the existing (locally-trained) corridor artifact"
        )
        return report

    cruise["track_sin"] = np.sin(np.radians(cruise["true_track"]))
    cruise["track_cos"] = np.cos(np.radians(cruise["true_track"]))
    features = cruise[["latitude", "longitude", "track_sin", "track_cos"]].to_numpy()

    scaler = StandardScaler()
    scaled = scaler.fit_transform(features)
    eps = _choose_eps_via_knee(scaled, k=MIN_SAMPLES)
    labels = DBSCAN(eps=eps, min_samples=MIN_SAMPLES).fit(scaled).labels_
    cruise["cluster"] = labels

    corridors = []
    for cluster_id in sorted(set(labels) - {-1}):
        sub = cruise[cruise["cluster"] == cluster_id]
        corridors.append({
            "corridor_id": int(cluster_id),
            "region": region,
            "centroid_lat": round(float(sub["latitude"].mean()), 5),
            "centroid_lon": round(float(sub["longitude"].mean()), 5),
            "modal_heading_deg": round(_modal_heading(sub["track_sin"], sub["track_cos"]), 1),
            "altitude_mean_ft": round(float(sub["altitude_ft"].mean()), 1),
            "altitude_std_ft": max(round(float(sub["altitude_ft"].std() or 1.0), 1), 1.0),
            "member_count": int(len(sub)),
            "polyline": _build_polyline(sub),
        })

    noise_pct = float((labels == -1).mean() * 100)
    if not corridors:
        report["reason"] = "DBSCAN found zero clusters (all noise) — keeping existing artifact"
        return report

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    artifact = {
        "corridors": corridors,
        "scoring_params": {
            "anomaly_score_threshold": 0.78,
            "lateral_distance_scale_km": 50.0,
            "heading_deviation_scale_deg": 45.0,
            "altitude_z_scale": 3.0,
        },
        "trained_from": f"cloud silver, all partitions as of retrain ({len(cruise)} cruise rows)",
        "exported_at": datetime.now(UTC).isoformat(),
    }
    body = json.dumps(artifact, indent=2).encode()
    versioned_key = f"models/corridors/{run_id}.json"
    s3.put_object(Bucket=BUCKET, Key=versioned_key, Body=body)  # always versioned, tracks growth

    replaced_canonical = len(cruise) >= MIN_ROWS_TO_REPLACE_CANONICAL
    if replaced_canonical:
        s3.put_object(Bucket=BUCKET, Key=ML_ARTIFACT_KEY, Body=body)

    report.update({
        "retrained": True,
        "n_clusters": len(corridors),
        "noise_pct": round(noise_pct, 1),
        "eps": round(eps, 4),
        "versioned_key": versioned_key,
        "replaced_canonical": replaced_canonical,
        "canonical_replace_bar": MIN_ROWS_TO_REPLACE_CANONICAL,
    })
    if not replaced_canonical:
        report["note"] = (
            f"{len(cruise)} cruise rows < {MIN_ROWS_TO_REPLACE_CANONICAL} bar — snapshot saved to "
            f"{versioned_key} but the canonical artifact (serving the live API) was NOT overwritten"
        )
    return report


def handler(event: dict, context: object) -> dict:
    if event.get("retrain_corridors"):
        return retrain_corridors(region=event.get("region", "india"))
    return _run_batch_transform(event, context)


def _run_batch_transform(event: dict, context: object) -> dict:
    """Step Functions entrypoint: bronze -> silver -> gold, registers a partition per table."""
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    bronze = read_bronze(BUCKET, INPUT_PREFIX)
    silver = build_silver(bronze)
    result = {"run_id": run_id, "bronze_rows": len(bronze), "silver_rows": len(silver)}

    if silver.empty:
        return result

    write_partition("silver", silver, run_id)

    gold_tables = build_gold(silver)
    for name, table_df in gold_tables.items():
        write_partition(name, table_df, run_id)
        result[f"gold_{name}_rows"] = len(table_df)

    if _ML_ARTIFACT is not None:
        anomaly_events = score_anomalies(silver, _ML_ARTIFACT)
        if not anomaly_events.empty:
            write_partition("anomaly_events", anomaly_events, run_id)
            result["gold_anomaly_events_rows"] = len(anomaly_events)
            result["anomaly_flagged"] = int(anomaly_events["is_ml_anomaly"].sum())
    else:
        result["anomaly_scoring"] = "skipped: no ml artifact at " + ML_ARTIFACT_KEY

    return result
