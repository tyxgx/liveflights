"""Model 2: trajectory prediction — position 5 minutes ahead.

Predicts DELTAS (delta_lat, delta_lon), not absolute coordinates, so the
model learns "how does a flight's position change given its current state"
rather than memorizing absolute geography. Compared against a mandatory
dead-reckoning baseline (great-circle projection along current track and
speed) — the point of this model is to show where learning from turn-rate/
acceleration/climb trends actually beats simple physics, and to say plainly
when it doesn't (cruise flight is close to dead reckoning by definition).
"""

from __future__ import annotations

import logging
import math

import joblib
import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sqlalchemy import create_engine

from ml.config import settings
from ml.data import load_silver

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ml.trajectory")

HORIZON_S = 300
TOLERANCE_S = 30
EARTH_RADIUS_KM = 6371.0
TURN_RATE_THRESHOLD_DEG_S = 1.0  # >1 deg/s sustained turn is a real maneuver, not noise
N_LAG_OBS = 3

FEATURE_COLS = [
    "latitude",
    "longitude",
    "velocity",
    "track_sin",
    "track_cos",
    "vertical_rate",
    "baro_altitude",
    "turn_rate",
    "acceleration",
    "climb_trend",
]


def _haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def dead_reckoning_delta(lat, lon, velocity, true_track, seconds=HORIZON_S):
    """Great-circle destination point given current position, speed (m/s),
    and bearing (degrees), projected `seconds` ahead. Returns (dlat, dlon).
    """
    lat1 = np.radians(lat)
    bearing = np.radians(true_track)
    distance_km = (velocity * seconds) / 1000.0
    ang_dist = distance_km / EARTH_RADIUS_KM

    lat2 = np.arcsin(
        np.sin(lat1) * np.cos(ang_dist) + np.cos(lat1) * np.sin(ang_dist) * np.cos(bearing)
    )
    lon1 = np.radians(lon)
    lon2 = lon1 + np.arctan2(
        np.sin(bearing) * np.sin(ang_dist) * np.cos(lat1),
        np.cos(ang_dist) - np.sin(lat1) * np.sin(lat2),
    )
    return np.degrees(lat2) - lat, np.degrees(lon2) - lon


def build_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-icao24, time-ordered lag features: turn rate, acceleration,
    climb-rate trend over the last N_LAG_OBS observations.
    """
    df = df.sort_values(["icao24", "time_position"]).copy()
    grouped = df.groupby("icao24", group_keys=False)

    df["track_sin"] = np.sin(np.radians(df["true_track"]))
    df["track_cos"] = np.cos(np.radians(df["true_track"]))

    df["prev_time"] = grouped["time_position"].shift(1)
    df["dt"] = (df["time_position"] - df["prev_time"]).clip(lower=1)

    prev_track_sin = grouped["track_sin"].shift(1)
    prev_track_cos = grouped["track_cos"].shift(1)
    # Signed angular difference via atan2 of the rotation between the two
    # unit vectors — avoids the 359deg -> 1deg wraparound bug of a naive
    # subtraction.
    cross = df["track_sin"] * prev_track_cos - df["track_cos"] * prev_track_sin
    dot = df["track_sin"] * prev_track_sin + df["track_cos"] * prev_track_cos
    heading_delta_deg = np.degrees(np.arctan2(cross, dot))
    df["turn_rate"] = heading_delta_deg / df["dt"]

    df["acceleration"] = (df["velocity"] - grouped["velocity"].shift(1)) / df["dt"]
    df["climb_trend"] = (df["vertical_rate"] - grouped["vertical_rate"].shift(1)) / df["dt"]

    return df


def build_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """For every airborne observation at time t, find (if any) the same
    aircraft's observation nearest to t+HORIZON_S within TOLERANCE_S, via a
    per-icao24 asof join. Returns one row per valid pair with both the
    feature columns (at t) and the actual position at t+5min.
    """
    airborne = df[~df["on_ground"]].dropna(
        subset=["latitude", "longitude", "velocity", "true_track", "vertical_rate", "baro_altitude"]
    )
    featured = build_lag_features(airborne)
    featured = featured.dropna(subset=FEATURE_COLS)

    left = featured.copy()
    left["target_time"] = left["time_position"] + HORIZON_S
    left = left.sort_values("target_time")

    right = featured[["icao24", "time_position", "latitude", "longitude", "flight_phase"]].rename(
        columns={
            "time_position": "future_time_position",
            "latitude": "future_latitude",
            "longitude": "future_longitude",
            "flight_phase": "future_flight_phase",
        }
    )
    right = right.sort_values("future_time_position")

    pairs = pd.merge_asof(
        left,
        right,
        left_on="target_time",
        right_on="future_time_position",
        by="icao24",
        direction="nearest",
        tolerance=TOLERANCE_S,
    )
    pairs = pairs.dropna(subset=["future_time_position"])
    # Exclude the trivial self-match (aircraft didn't move in time).
    pairs = pairs[pairs["future_time_position"] != pairs["time_position"]]

    pairs["delta_lat"] = pairs["future_latitude"] - pairs["latitude"]
    pairs["delta_lon"] = pairs["future_longitude"] - pairs["longitude"]

    pairs["eval_phase"] = np.where(
        pairs["turn_rate"].abs() > TURN_RATE_THRESHOLD_DEG_S, "turning", pairs["flight_phase"]
    )
    return pairs.reset_index(drop=True)


def report_dt_distribution(pairs: pd.DataFrame) -> pd.Series:
    """Distribution of ACTUAL elapsed seconds in each (t, t+5min) pair —
    checked before trusting a fixed-300s dead-reckoning baseline, since a
    baseline given the wrong horizon is trivially unfair to itself.
    """
    dt = pairs["future_time_position"] - pairs["time_position"]
    stats = dt.describe(percentiles=[0.1, 0.5, 0.9])
    logger.info(
        "Actual pair time gaps (s): min=%.1f p10=%.1f median=%.1f p90=%.1f max=%.1f mean=%.1f",
        stats["min"],
        stats["10%"],
        stats["50%"],
        stats["90%"],
        stats["max"],
        stats["mean"],
    )
    return dt


def _error_breakdown(
    pairs: pd.DataFrame, model_err, dr_err, group_col: str, labels: list[str]
) -> pd.DataFrame:
    rows = []
    for label in labels:
        mask = (pairs[group_col] == label).values
        if mask.sum() == 0:
            continue
        rows.append(
            {
                group_col: label,
                "n": int(mask.sum()),
                "model_median_km": round(float(np.median(model_err[mask])), 3),
                "model_p90_km": round(float(np.percentile(model_err[mask], 90)), 3),
                "dr_median_km": round(float(np.median(dr_err[mask])), 3),
                "dr_p90_km": round(float(np.percentile(dr_err[mask], 90)), 3),
            }
        )
    overall = {
        group_col: "OVERALL",
        "n": len(pairs),
        "model_median_km": round(float(np.median(model_err)), 3),
        "model_p90_km": round(float(np.percentile(model_err, 90)), 3),
        "dr_median_km": round(float(np.median(dr_err)), 3),
        "dr_p90_km": round(float(np.percentile(dr_err, 90)), 3),
    }
    return pd.DataFrame([*rows, overall])


def phase_error_table(pairs: pd.DataFrame, model_lat, model_lon, dr_lat, dr_lon) -> pd.DataFrame:
    actual_lat = pairs["latitude"] + pairs["delta_lat"]
    actual_lon = pairs["longitude"] + pairs["delta_lon"]
    model_err = _haversine_km(actual_lat, actual_lon, model_lat, model_lon)
    dr_err = _haversine_km(actual_lat, actual_lon, dr_lat, dr_lon)
    return _error_breakdown(
        pairs, model_err, dr_err, "eval_phase", ["cruise", "climb", "descent", "turning"]
    )


def source_error_table(pairs: pd.DataFrame, model_lat, model_lon, dr_lat, dr_lon) -> pd.DataFrame:
    """Model 2's evaluation set is built entirely from time-ordered pairs of
    the SAME aircraft — real OpenSky data only exists as a single replayed
    snapshot (one point in time per aircraft), so it can never form a valid
    (t, t+5min) pair. This is reported explicitly, not glossed over: if
    every row below is "simulate", the model has only ever been evaluated
    against the simulator's own generative process, and may simply be
    learning to denoise the simulator's per-tick random walk rather than
    anything that would generalize to real flight dynamics.
    """
    actual_lat = pairs["latitude"] + pairs["delta_lat"]
    actual_lon = pairs["longitude"] + pairs["delta_lon"]
    model_err = _haversine_km(actual_lat, actual_lon, model_lat, model_lon)
    dr_err = _haversine_km(actual_lat, actual_lon, dr_lat, dr_lon)
    return _error_breakdown(pairs, model_err, dr_err, "source", ["simulate", "opensky"])


def run() -> dict | None:
    """Returns a result dict, or None if there wasn't enough data to train."""
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_trajectory_experiment)

    df = load_silver("ml-trajectory")
    pairs = build_pairs(df)
    n_pairs = len(pairs)
    logger.info("Valid (t, t+5min) trajectory pairs found: %d", n_pairs)
    report_dt_distribution(pairs)
    logger.info("Pairs by source: %s", pairs["source"].value_counts().to_dict())

    if n_pairs < settings.min_trajectory_pairs:
        logger.warning(
            "Only %d valid trajectory pairs (< %d minimum) — the simulate stream hasn't "
            "been running long enough for enough aircraft to have a continuous 5-minute "
            "observation window yet. NOT training on this thin a dataset. Keep the "
            "producer/bronze/silver jobs running and re-run ml/trajectory.py later.",
            n_pairs,
            settings.min_trajectory_pairs,
        )
        return None

    # Time-based split on t (never random — consecutive observations of the
    # same aircraft are highly correlated and would leak across a random split).
    pairs = pairs.sort_values("time_position")
    split_idx = int(len(pairs) * 0.8)
    train, test = pairs.iloc[:split_idx], pairs.iloc[split_idx:]
    logger.info(
        "Train: %d rows (earliest), Test: %d rows (latest, time-based split)", len(train), len(test)
    )

    X_train, X_test = train[FEATURE_COLS], test[FEATURE_COLS]

    model_lat = GradientBoostingRegressor(random_state=42)
    model_lat.fit(X_train, train["delta_lat"])
    pred_delta_lat = model_lat.predict(X_test)

    model_lon = GradientBoostingRegressor(random_state=42)
    model_lon.fit(X_train, train["delta_lon"])
    pred_delta_lon = model_lon.predict(X_test)

    model_pred_lat = test["latitude"].values + pred_delta_lat
    model_pred_lon = test["longitude"].values + pred_delta_lon

    # Use each pair's ACTUAL elapsed time, not a nominal 300s — the gaps
    # are tightly clustered near 300s in practice (checked above via
    # report_dt_distribution), but this is the physically correct thing to
    # do regardless, and removes any doubt that the baseline is penalized
    # by an assumed horizon that doesn't match reality.
    actual_seconds = (test["future_time_position"] - test["time_position"]).values
    dr_dlat, dr_dlon = dead_reckoning_delta(
        test["latitude"].values,
        test["longitude"].values,
        test["velocity"].values,
        test["true_track"].values,
        seconds=actual_seconds,
    )
    dr_pred_lat = test["latitude"].values + dr_dlat
    dr_pred_lon = test["longitude"].values + dr_dlon

    error_table = phase_error_table(test, model_pred_lat, model_pred_lon, dr_pred_lat, dr_pred_lon)
    source_table = source_error_table(
        test, model_pred_lat, model_pred_lon, dr_pred_lat, dr_pred_lon
    )
    logger.info(
        "Trajectory error by source (model vs dead-reckoning baseline):\n%s",
        source_table.to_string(index=False),
    )
    logger.info(
        "Trajectory error by phase (model vs dead-reckoning baseline, ACTUAL elapsed time):\n%s",
        error_table.to_string(index=False),
    )

    overall = error_table[error_table["eval_phase"] == "OVERALL"].iloc[0]
    model_wins_overall = overall["model_median_km"] < overall["dr_median_km"]
    logger.info(
        "Overall: model %s dead reckoning (median %.3fkm vs %.3fkm)",
        "beats" if model_wins_overall else "does NOT beat",
        overall["model_median_km"],
        overall["dr_median_km"],
    )

    with mlflow.start_run(run_name="gbr-trajectory-delta") as run_ctx:
        mlflow.log_param("features", FEATURE_COLS)
        mlflow.log_param("horizon_seconds", HORIZON_S)
        mlflow.log_param("tolerance_seconds", TOLERANCE_S)
        mlflow.log_param("train_rows", len(train))
        mlflow.log_param("test_rows", len(test))
        mlflow.log_param("split", "time-based, 80/20, sorted by time_position")
        mlflow.log_param("dr_baseline_uses_actual_elapsed_time", True)
        pair_sources = pairs["source"].value_counts().to_dict()
        mlflow.log_param("pair_sources", pair_sources)
        for _, row in error_table.iterrows():
            prefix = row["eval_phase"].lower()
            mlflow.log_metric(f"{prefix}_model_median_km", row["model_median_km"])
            mlflow.log_metric(f"{prefix}_model_p90_km", row["model_p90_km"])
            mlflow.log_metric(f"{prefix}_dr_median_km", row["dr_median_km"])
            mlflow.log_metric(f"{prefix}_dr_p90_km", row["dr_p90_km"])
        for _, row in source_table.iterrows():
            prefix = f"src_{row['source'].lower()}"
            mlflow.log_metric(f"{prefix}_model_median_km", row["model_median_km"])
            mlflow.log_metric(f"{prefix}_dr_median_km", row["dr_median_km"])

        importances = pd.Series(
            (model_lat.feature_importances_ + model_lon.feature_importances_) / 2,
            index=FEATURE_COLS,
        ).sort_values()
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        importances.plot.barh(ax=ax)
        ax.set_title("Trajectory model — mean feature importance (lat+lon models)")
        plot_path = f"{settings.plots_dir}/trajectory_feature_importance.png"
        fig.savefig(plot_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(plot_path)

        mlflow.sklearn.log_model(model_lat, "model_delta_lat")
        mlflow.sklearn.log_model(model_lon, "model_delta_lon")
        joblib.dump(model_lat, f"{settings.artifacts_dir}/trajectory_model_lat.pkl")
        joblib.dump(model_lon, f"{settings.artifacts_dir}/trajectory_model_lon.pkl")

        # Register + conditionally promote (compare overall median km vs
        # current Production, if any). Lat and lon are two separate
        # single-output models, registered under distinct names so
        # ml/registry.py can load each independently.
        client = mlflow.tracking.MlflowClient()
        try:
            lat_name = f"{settings.mlflow_trajectory_model_name}-lat"
            lon_name = f"{settings.mlflow_trajectory_model_name}-lon"
            mv_lat = mlflow.register_model(f"runs:/{run_ctx.info.run_id}/model_delta_lat", lat_name)
            mv_lon = mlflow.register_model(f"runs:/{run_ctx.info.run_id}/model_delta_lon", lon_name)
            _maybe_promote(client, lat_name, mv_lat.version, overall["model_median_km"])
            _maybe_promote(client, lon_name, mv_lon.version, overall["model_median_km"])
        except Exception:
            logger.exception("Model registry step failed (non-fatal for this demo run)")

    ghost_trails = test[["icao24", "time_position", "latitude", "longitude", "eval_phase"]].copy()
    ghost_trails["predicted_lat"] = model_pred_lat
    ghost_trails["predicted_lon"] = model_pred_lon
    ghost_trails["actual_lat"] = test["latitude"] + test["delta_lat"]
    ghost_trails["actual_lon"] = test["longitude"] + test["delta_lon"]
    engine = create_engine(settings.database_url)
    # TRUNCATE (not DROP/replace) so the table identity survives a retrain
    # even after a dbt staging view is later built on top of it — same class
    # of bug fixed in ml/anomaly.py, ml/corridors.py, and gold_batch.py.
    with engine.begin() as conn:
        exists = conn.exec_driver_sql(
            "SELECT to_regclass('gold.trajectory_predictions') IS NOT NULL"
        ).scalar()
        if exists:
            conn.exec_driver_sql("TRUNCATE TABLE gold.trajectory_predictions")
    ghost_trails.to_sql(
        "trajectory_predictions",
        engine,
        schema="gold",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )
    logger.info(
        "Wrote %d rows to gold.trajectory_predictions (for the frontend's ghost trail)",
        len(ghost_trails),
    )

    return {
        "n_pairs": n_pairs,
        "error_table": error_table,
        "source_table": source_table,
        "model_wins_overall": model_wins_overall,
    }


def _maybe_promote(client, model_name: str, version: str, new_median_km: float) -> None:
    try:
        current_prod = client.get_latest_versions(model_name, stages=["Production"])
    except Exception:
        current_prod = []
    if not current_prod:
        client.transition_model_version_stage(model_name, version, "Production")
        logger.info(
            "Promoted %s v%s to Production (no prior Production model)", model_name, version
        )
        return
    prod_run = client.get_run(current_prod[0].run_id)
    prod_median = prod_run.data.metrics.get("overall_model_median_km")
    if prod_median is None or new_median_km < prod_median:
        client.transition_model_version_stage(
            model_name, version, "Production", archive_existing_versions=True
        )
        logger.info(
            "Promoted %s v%s to Production (%.3fkm < prior %.3fkm)",
            model_name,
            version,
            new_median_km,
            prod_median or math.inf,
        )
    else:
        logger.info(
            "Kept existing Production model (%.3fkm <= new %.3fkm)", prod_median, new_median_km
        )


if __name__ == "__main__":
    run()
