"""Model 3: contextual anomaly detection, built on Model 1's corridors.

Division of labour (see README.md / PROGRESS.md for the full rationale):
RULES (silver's data_quality_flags — implausible speed/altitude/vertical
rate, missing position, emergency squawk) catch physically impossible
states — a plain threshold is the right tool for those, and re-deriving
them with ML would be circular. This model catches something rules
structurally cannot: a flight that is individually plausible but behaving
unlike anything else near it — off its corridor's centroid, against its
corridor's modal heading, or at an altitude unusual for that corridor.

The overlap table below is the actual evidence that this model adds
signal beyond the rules, not just decoration.
"""

from __future__ import annotations

import logging

import mlflow
import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from ml.config import settings
from ml.corridors import discover
from ml.data import load_silver

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ml.anomaly")

# Calibrated against the observed score distribution (see run(), which logs
# the full percentile table every run) — anomalies must be rare, not a fifth
# of all traffic. The uncalibrated default of 0.5 flagged ~27% of points,
# which is not a defensible "anomaly" rate for any downstream consumer.
#
# Re-picked after adding the India region: mixing India's own well-formed
# corridors into the same DBSCAN run shifted the whole score distribution
# (fewer points are now "far from every corridor", since India traffic has
# its own nearby corridors instead of being scored against only Europe/US
# ones) — the prior 0.65 threshold, calibrated Europe/US-only, dropped to a
# 1.6% flagged rate once India joined the corridor set. Re-calibrated to
# p97≈0.624 -> ~3%. This is a real characteristic of a growing, multi-region
# corridor set, not a one-off bug: re-check this threshold against the
# logged percentile table whenever the traffic mix changes materially
# (e.g. a new region added, or the region distribution shifts a lot).
#
# Re-calibrated again after switching the India region from simulator data
# to real OpenSky polling: real traffic has more corridor-unassigned/noise
# points than the simulator's tight synthetic routes, which shifted the
# score distribution and pushed the 0.62 threshold's flagged rate to 6.34%
# (percentiles: p95=0.634, p96=0.658, p97=0.784 — a sharp jump between p96
# and p97). Re-picked at p97≈0.78 -> ~3%, back in the 2-5% band.
ANOMALY_SCORE_THRESHOLD = 0.78
LATERAL_DISTANCE_SCALE_KM = 50.0  # ~90th percentile of typical corridor spread, for normalization
HEADING_DEVIATION_SCALE_DEG = 45.0
ALTITUDE_Z_SCALE = 3.0
EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _angular_diff_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    diff = (a - b + 180) % 360 - 180
    return np.abs(diff)


def score_points(cruise: pd.DataFrame, corridors: pd.DataFrame) -> pd.DataFrame:
    cruise = cruise.copy()
    centroids = corridors[["corridor_id", "centroid_lat", "centroid_lon"]].values

    nearest_ids = np.empty(len(cruise), dtype=int)
    nearest_dist = np.empty(len(cruise))
    for i, (lat, lon) in enumerate(
        zip(cruise["latitude"].values, cruise["longitude"].values, strict=True)
    ):
        dists = _haversine_km(
            lat, lon, centroids[:, 1].astype(float), centroids[:, 2].astype(float)
        )
        j = int(np.argmin(dists))
        nearest_ids[i] = int(centroids[j, 0])
        nearest_dist[i] = dists[j]

    cruise["nearest_corridor_id"] = nearest_ids
    cruise["lateral_distance_km"] = nearest_dist
    cruise["is_noise"] = cruise["corridor_id"] == -1

    corridor_lookup = corridors.set_index("corridor_id")
    cruise["corridor_heading"] = cruise["nearest_corridor_id"].map(
        corridor_lookup["modal_heading_deg"]
    )
    cruise["corridor_alt_mean"] = cruise["nearest_corridor_id"].map(
        corridor_lookup["altitude_mean_ft"]
    )
    cruise["corridor_alt_std"] = cruise["nearest_corridor_id"].map(
        corridor_lookup["altitude_std_ft"]
    )

    cruise["heading_deviation_deg"] = _angular_diff_deg(
        cruise["true_track"], cruise["corridor_heading"]
    )
    alt_col = "altitude_ft" if "altitude_ft" in cruise.columns else "baro_altitude"
    cruise["altitude_z"] = (cruise[alt_col] - cruise["corridor_alt_mean"]) / cruise[
        "corridor_alt_std"
    ]

    lateral_component = (cruise["lateral_distance_km"] / LATERAL_DISTANCE_SCALE_KM).clip(0, 1)
    heading_component = (cruise["heading_deviation_deg"] / HEADING_DEVIATION_SCALE_DEG).clip(0, 1)
    altitude_component = (cruise["altitude_z"].abs() / ALTITUDE_Z_SCALE).clip(0, 1)
    noise_component = cruise["is_noise"].astype(float)

    cruise["anomaly_score"] = (
        0.25 * lateral_component
        + 0.25 * heading_component
        + 0.25 * altitude_component
        + 0.25 * noise_component
    )

    def reasons(row) -> str:
        r = []
        if row["lateral_distance_km"] / LATERAL_DISTANCE_SCALE_KM > 0.5:
            r.append("far_from_corridor")
        if row["heading_deviation_deg"] / HEADING_DEVIATION_SCALE_DEG > 0.5:
            r.append("heading_deviation")
        if abs(row["altitude_z"]) / ALTITUDE_Z_SCALE > 0.5:
            r.append("altitude_outlier")
        if row["is_noise"]:
            r.append("unassigned_corridor")
        return ",".join(r) if r else "none"

    cruise["ml_reasons"] = cruise.apply(reasons, axis=1)
    cruise["is_ml_anomaly"] = cruise["anomaly_score"] > ANOMALY_SCORE_THRESHOLD
    return cruise


def overlap_table(scored: pd.DataFrame) -> pd.DataFrame:
    rules_flagged = scored["data_quality_flags"].apply(
        lambda flags: flags is not None and len(flags) > 0
    )
    ml_flagged = scored["is_ml_anomaly"]

    rules_only = int((rules_flagged & ~ml_flagged).sum())
    ml_only = int((~rules_flagged & ml_flagged).sum())
    both = int((rules_flagged & ml_flagged).sum())
    neither = int((~rules_flagged & ~ml_flagged).sum())

    return pd.DataFrame(
        [
            {"bucket": "rules_only", "count": rules_only},
            {"bucket": "ml_only", "count": ml_only},
            {"bucket": "both", "count": both},
            {"bucket": "neither", "count": neither},
            {"bucket": "total", "count": len(scored)},
        ]
    )


def report_score_distribution(scores: pd.Series) -> None:
    percentiles = [50, 75, 90, 95, 96, 97, 98, 99, 99.5]
    values = {p: float(np.percentile(scores, p)) for p in percentiles}
    logger.info(
        "anomaly_score percentiles: %s",
        ", ".join(f"p{p}={v:.4f}" for p, v in values.items()),
    )
    flagged_at_threshold = float((scores > ANOMALY_SCORE_THRESHOLD).mean() * 100)
    logger.info(
        "At threshold=%.2f: flagged rate=%.2f%% (target band: 2-5%%)",
        ANOMALY_SCORE_THRESHOLD,
        flagged_at_threshold,
    )


def show_top_ml_only_detections(
    scored: pd.DataFrame, rules_flagged: pd.Series, n: int = 5
) -> pd.DataFrame:
    """The real proof the contextual model adds signal: pull the highest-
    scoring ML-only detections and show the actual field values, so it's
    possible to see they're genuinely unusual (off-corridor, wrong heading,
    altitude outlier) rather than noise the threshold happened to catch.
    """
    ml_only = scored[scored["is_ml_anomaly"] & ~rules_flagged].sort_values(
        "anomaly_score", ascending=False
    )
    top = ml_only.head(n)
    cols = [
        "icao24",
        "latitude",
        "longitude",
        "true_track",
        "corridor_heading",
        "heading_deviation_deg",
        "baro_altitude" if "altitude_ft" not in top.columns else "altitude_ft",
        "corridor_alt_mean",
        "altitude_z",
        "lateral_distance_km",
        "is_noise",
        "anomaly_score",
        "ml_reasons",
    ]
    logger.info(
        "Top %d ML-only detections (rule-flags did NOT catch these):\n%s",
        n,
        top[cols].to_string(index=False),
    )
    return top[cols]


def run() -> None:
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_anomaly_experiment)

    df = load_silver("ml-anomaly")
    result = discover(df)
    cruise, corridors = result["cruise"], result["corridors"]

    if len(corridors) == 0:
        logger.warning(
            "No corridors discovered yet (need more cruise data) — skipping Model 3 this run."
        )
        return

    scored = score_points(cruise, corridors)
    report_score_distribution(scored["anomaly_score"])
    overlap = overlap_table(scored)
    logger.info("Rules vs ML anomaly overlap:\n%s", overlap.to_string(index=False))

    rules_flagged_series = scored["data_quality_flags"].apply(
        lambda flags: flags is not None and len(flags) > 0
    )
    show_top_ml_only_detections(scored, rules_flagged_series)

    # Sanity check only, NOT the headline metric — the simulator's injected
    # anomalies were generated by the same kind of rule thresholds we're
    # comparing against, so agreement here is somewhat circular by
    # construction. It's useful as a smoke test, not as proof the model works.
    injected_mask = scored["data_quality_flags"].apply(
        lambda flags: flags is not None and len(flags) > 0
    )
    if injected_mask.sum() > 0:
        catch_rate = float(scored.loc[injected_mask, "is_ml_anomaly"].mean())
        logger.info(
            "SANITY CHECK (not a headline metric — injected anomalies are rule-shaped by "
            "construction): ML model flags %.1f%% of simulator-injected/rule-flagged states.",
            catch_rate * 100,
        )

    with mlflow.start_run(run_name="contextual-anomaly-scoring"):
        mlflow.log_param("anomaly_score_threshold", ANOMALY_SCORE_THRESHOLD)
        mlflow.log_param("lateral_distance_scale_km", LATERAL_DISTANCE_SCALE_KM)
        mlflow.log_param("heading_deviation_scale_deg", HEADING_DEVIATION_SCALE_DEG)
        mlflow.log_param("altitude_z_scale", ALTITUDE_Z_SCALE)
        mlflow.log_param("input_rows", len(scored))
        mlflow.log_metric("flagged_rate_pct", float(scored["is_ml_anomaly"].mean() * 100))
        for p in [50, 75, 90, 95, 97, 99]:
            mlflow.log_metric(f"score_p{p}", float(np.percentile(scored["anomaly_score"], p)))
        for _, row in overlap.iterrows():
            mlflow.log_metric(f"overlap_{row['bucket']}", row["count"])
        if injected_mask.sum() > 0:
            mlflow.log_metric("sanity_check_catch_rate", catch_rate)

    events = scored[scored["is_ml_anomaly"] | injected_mask].copy()
    events["rule_reasons"] = events["data_quality_flags"].apply(
        lambda flags: ",".join(flags) if flags is not None and len(flags) > 0 else "none"
    )
    events["anomaly_type"] = events.apply(
        lambda r: (
            ",".join(x for x in [r["rule_reasons"], r["ml_reasons"]] if x != "none") or "none"
        ),
        axis=1,
    )
    alt_col = "altitude_ft" if "altitude_ft" in events.columns else "baro_altitude"
    out = events[
        [
            "icao24",
            "callsign",
            "origin_country",
            "ingest_ts",
            "latitude",
            "longitude",
            alt_col,
            "velocity",
            "anomaly_score",
            "anomaly_type",
            "nearest_corridor_id",
            "lateral_distance_km",
            "heading_deviation_deg",
            "altitude_z",
            "is_noise",
        ]
    ].rename(
        columns={alt_col: "altitude_ft", "velocity": "speed_kmh", "is_noise": "unassigned_corridor"}
    )

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        # dbt's stg_anomaly_events view (P4) depends on this table. A
        # DROP TABLE ... CASCADE (the previous approach) took the view down
        # with it every retrain, forcing `dbt run` before `dbt test` on
        # every cycle — silently breaking once P8's daily_ml_retrain and
        # daily_dbt DAGs run on independent schedules. TRUNCATE + re-insert
        # keeps the table (and the view built on it) untouched; the table
        # itself is only ever created once, the first time this runs.
        exists = conn.exec_driver_sql(
            "SELECT to_regclass('gold.anomaly_events') IS NOT NULL"
        ).scalar()
        if exists:
            conn.exec_driver_sql("TRUNCATE TABLE gold.anomaly_events")
    out.to_sql(
        "anomaly_events",
        engine,
        schema="gold",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )
    logger.info(
        "Wrote %d rows to gold.anomaly_events (rule/ML-flagged, with corridor context)",
        len(out),
    )


if __name__ == "__main__":
    run()
