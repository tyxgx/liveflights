"""Model 1: air corridor discovery via DBSCAN.

Rule-based thresholds (silver's data_quality_flags) already catch physically
impossible states. This model exists to do something rules cannot: learn
latent spatial structure — the corridors aircraft actually cluster into —
so Model 3 can score deviation from *learned* behavior instead of a fixed
threshold. See PROGRESS.md / README.md "Division of labour" section.

Heading (sin/cos of true_track) is included as a feature deliberately:
opposite-direction traffic sharing the same lat/lon airway must separate
into distinct corridors, which pure position clustering would merge.

**Fit per region, not on combined data.** A single StandardScaler fit
across Europe + India (thousands of km apart) distorts the scaled feature
space: the mean sits somewhere in the ocean between them, and the standard
deviation is dominated by the inter-continental spread rather than
within-corridor spread — the k-distance elbow then picks an eps that
doesn't mean the same thing for either region's actual local density.
Confirmed by direct measurement: fitting combined dropped silhouette from
0.607 (Europe alone, pre-India) to 0.113. Regions are also genuinely
different in airspace structure (route density, corridor spacing), so a
shared eps was never going to be right for both even with a scaling fix.
"""

from __future__ import annotations

import json
import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine

from ml.config import settings
from ml.data import load_silver

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ml.corridors")

FEATURES = ["latitude", "longitude", "track_sin", "track_cos"]
MIN_SAMPLES = 8

# NOTE: these are intentionally NOT ingestion/config.py:REGION_BBOXES.
# Those are tight bounding boxes for constraining a *live OpenSky query*
# (lat 45-56 for "europe"), and cut off a large fraction of the actual
# simulator airport network — e.g. Madrid (40.5), Barcelona (41.3), Rome
# (41.8), and Lisbon (38.8) all fall outside that box despite being real
# "Europe" airports. Using the tight ingestion box here fragmented real
# corridors at the boundary and produced a large, meaningless "other"
# bucket with its own low silhouette (measured: -0.09 for the
# artificially-truncated "europe" bucket). These boxes instead match
# streaming/utils/enrich.py's broader continental `region_bucket()`
# boxes (Europe / South Asia), which comfortably contain every airport in
# ingestion/airports.py's AIRPORTS_EUROPE / AIRPORTS_INDIA lists.
REGION_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "europe": (34.0, -25.0, 72.0, 45.0),
    "north_america": (5.0, -170.0, 72.0, -50.0),
    "india": (5.0, 60.0, 38.0, 100.0),  # matches enrich.py's "South Asia" box
}

# Below this many cruise-phase points, a region's own StandardScaler/DBSCAN
# fit is too unstable to trust (the k-distance elbow needs enough points to
# have a real elbow) — such a region's rows are kept (tagged noise, -1)
# rather than dropped, so downstream anomaly scoring still sees them.
MIN_ROWS_FOR_REGION_FIT = MIN_SAMPLES * 2

# Tried and rejected: scaling min_samples with each region's row count
# (matching the ratio from the original 0.607-silhouette run,
# 8 samples / 15,000 rows) on the theory that a fixed k's k-th-nearest-
# neighbor distance mechanically shrinks as ~(k/n)^(1/d) when n grows,
# even with no real change in corridor structure. Measured directly:
# this made silhouette WORSE, not better (Europe -0.081 -> -0.157, India
# 0.105 -> 0.081) — see PROGRESS.md for both measurements. Reverted to a
# fixed MIN_SAMPLES rather than keep tuning until a number looked right;
# the low/negative silhouette at this data volume appears to be a real
# property of a hub-and-spoke route network (many corridors converge near
# shared airports, which silhouette — built for globular clusters —
# penalizes even when DBSCAN's density-based partition is functionally
# correct), not something a different min_samples fixes.


def assign_region(lat: pd.Series, lon: pd.Series) -> pd.Series:
    conditions = []
    choices = []
    for region, (lat_min, lon_min, lat_max, lon_max) in REGION_BBOXES.items():
        conditions.append((lat >= lat_min) & (lat <= lat_max) & (lon >= lon_min) & (lon <= lon_max))
        choices.append(region)
    return pd.Series(np.select(conditions, choices, default="other"), index=lat.index)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    cruise = df[(~df["on_ground"]) & (df["flight_phase"] == "cruise")].copy()
    cruise = cruise.dropna(subset=["latitude", "longitude", "true_track"])
    cruise["track_sin"] = np.sin(np.radians(cruise["true_track"]))
    cruise["track_cos"] = np.cos(np.radians(cruise["true_track"]))
    cruise["region"] = assign_region(cruise["latitude"], cruise["longitude"])
    return cruise.reset_index(drop=True)


def choose_eps_via_knee(scaled: np.ndarray, k: int) -> tuple[float, np.ndarray]:
    """k-distance elbow: sort each point's k-th nearest-neighbor distance
    ascending: the knee of this curve is a standard DBSCAN eps heuristic.
    Knee found via max perpendicular distance from the line joining the
    curve's endpoints (no extra dependency needed for this).
    """
    nn = NearestNeighbors(n_neighbors=k).fit(scaled)
    distances, _ = nn.kneighbors(scaled)
    k_distances = np.sort(distances[:, -1])

    x = np.arange(len(k_distances))
    y = k_distances
    p1, p2 = np.array([x[0], y[0]]), np.array([x[-1], y[-1]])
    line_vec = p2 - p1
    line_len = np.linalg.norm(line_vec)
    line_unit = line_vec / line_len
    points = np.column_stack([x, y]) - p1
    proj_len = points @ line_unit
    proj_points = np.outer(proj_len, line_unit)
    perp_dist = np.linalg.norm(points - proj_points, axis=1)
    knee_idx = int(np.argmax(perp_dist))
    return float(k_distances[knee_idx]), k_distances


def modal_heading(track_sin: pd.Series, track_cos: pd.Series) -> float:
    return float(np.degrees(np.arctan2(track_sin.mean(), track_cos.mean())) % 360)


def build_polyline(
    group: pd.DataFrame, heading_deg: float, n_points: int = 10
) -> list[list[float]]:
    lat0 = group["latitude"].mean()
    heading_rad = np.radians(heading_deg)
    northing = group["latitude"] - lat0
    easting = (group["longitude"] - group["longitude"].mean()) * np.cos(np.radians(lat0))
    projection = northing * np.cos(heading_rad) + easting * np.sin(heading_rad)
    ordered = group.assign(_proj=projection).sort_values("_proj")
    if len(ordered) <= n_points:
        sampled = ordered
    else:
        idx = np.linspace(0, len(ordered) - 1, n_points).astype(int)
        sampled = ordered.iloc[idx]
    return [[round(r.latitude, 4), round(r.longitude, 4)] for r in sampled.itertuples()]


def _fit_region(sub: pd.DataFrame) -> dict:
    """DBSCAN fit for one region's cruise rows. Returns local (unoffset)
    labels plus the diagnostics needed to report/plot this region alone.
    """
    if len(sub) < MIN_ROWS_FOR_REGION_FIT:
        return {
            "labels": np.full(len(sub), -1),
            "eps": None,
            "k_distances": None,
            "min_samples": None,
            "n_clusters": 0,
            "noise_pct": 100.0,
            "silhouette": None,
            "input_rows": len(sub),
            "skipped": True,
        }

    min_samples = MIN_SAMPLES

    scaler = StandardScaler()
    scaled = scaler.fit_transform(sub[FEATURES])
    eps, k_distances = choose_eps_via_knee(scaled, k=min_samples)
    db = DBSCAN(eps=eps, min_samples=min_samples).fit(scaled)
    labels = db.labels_

    n_clusters = len(set(labels) - {-1})
    noise_pct = float((labels == -1).mean() * 100)
    sil = None
    if n_clusters >= 2:
        mask = labels != -1
        if mask.sum() > n_clusters:
            sil = float(silhouette_score(scaled[mask], labels[mask]))

    return {
        "labels": labels,
        "eps": eps,
        "k_distances": k_distances,
        "min_samples": min_samples,
        "n_clusters": n_clusters,
        "noise_pct": noise_pct,
        "silhouette": sil,
        "input_rows": len(sub),
        "skipped": False,
    }


def discover(df: pd.DataFrame) -> dict:
    """Runs feature build + per-region eps selection + DBSCAN. Pure (no
    MLflow/DB I/O) so ml/anomaly.py can reuse the exact same corridor
    assignments without depending on ml/corridors.py having already run in
    this process.
    """
    cruise_all = build_features(df)

    per_region: dict[str, dict] = {}
    cruise_frames = []
    global_offset = 0
    for region in sorted(cruise_all["region"].unique()):
        sub = cruise_all[cruise_all["region"] == region].copy()
        fit = _fit_region(sub)
        offset_labels = np.where(fit["labels"] == -1, -1, fit["labels"] + global_offset)
        sub["corridor_id"] = offset_labels
        cruise_frames.append(sub)
        per_region[region] = fit
        global_offset += fit["n_clusters"]

    cruise = pd.concat(cruise_frames, ignore_index=True)

    corridors_rows = []
    for corridor_id, group in cruise[cruise["corridor_id"] != -1].groupby("corridor_id"):
        heading = modal_heading(group["track_sin"], group["track_cos"])
        alt_col = "altitude_ft" if "altitude_ft" in group.columns else "baro_altitude"
        corridors_rows.append(
            {
                "corridor_id": int(corridor_id),
                "region": group["region"].iloc[0],
                "centroid_lat": round(float(group["latitude"].mean()), 5),
                "centroid_lon": round(float(group["longitude"].mean()), 5),
                "modal_heading_deg": round(heading, 1),
                "altitude_mean_ft": float(group[alt_col].mean()),
                "altitude_std_ft": float(group[alt_col].std()) or 1.0,
                "altitude_p10_ft": round(float(group[alt_col].quantile(0.10)), 1),
                "altitude_p50_ft": round(float(group[alt_col].quantile(0.50)), 1),
                "altitude_p90_ft": round(float(group[alt_col].quantile(0.90)), 1),
                "member_count": int(len(group)),
                "polyline": json.dumps(build_polyline(group, heading)),
            }
        )
    corridors_df = (
        pd.DataFrame(corridors_rows)
        .sort_values("member_count", ascending=False)
        .reset_index(drop=True)
    )

    n_clusters_total = sum(r["n_clusters"] for r in per_region.values())
    noise_pct_total = float((cruise["corridor_id"] == -1).mean() * 100)

    return {
        "cruise": cruise,
        "corridors": corridors_df,
        "per_region": per_region,
        "n_clusters": n_clusters_total,
        "noise_pct": noise_pct_total,
    }


def run() -> pd.DataFrame:
    """Trains the corridor model (per region), logs to MLflow, writes gold
    tables. Returns the assignments DataFrame (used by ml/anomaly.py).
    """
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_corridor_experiment)

    df = load_silver("ml-corridors")
    result = discover(df)
    cruise, corridors_df, per_region = result["cruise"], result["corridors"], result["per_region"]
    logger.info("Cruise, airborne rows for corridor discovery: %d", len(cruise))
    logger.info("TOTAL: corridors=%d noise_pct=%.1f%%", result["n_clusters"], result["noise_pct"])

    with mlflow.start_run(run_name="dbscan-corridors"):
        mlflow.log_param("features", FEATURES)
        mlflow.log_param("min_samples", MIN_SAMPLES)
        mlflow.log_param(
            "eps_selection", "k-distance knee (max perpendicular distance), per region"
        )
        mlflow.log_param("input_rows", len(cruise))
        mlflow.log_metric("corridor_count_total", result["n_clusters"])
        mlflow.log_metric("noise_pct_total", result["noise_pct"])

        for region, fit in per_region.items():
            logger.info(
                "region=%s input_rows=%d min_samples=%s eps=%s corridors=%d noise_pct=%.1f%% "
                "silhouette=%s%s",
                region,
                fit["input_rows"],
                fit["min_samples"] if fit["min_samples"] is not None else "n/a",
                f"{fit['eps']:.4f}" if fit["eps"] is not None else "n/a",
                fit["n_clusters"],
                fit["noise_pct"],
                f"{fit['silhouette']:.4f}" if fit["silhouette"] is not None else "n/a",
                " (SKIPPED: below min-rows-for-fit threshold)" if fit["skipped"] else "",
            )
            mlflow.log_metric(f"input_rows_{region}", fit["input_rows"])
            mlflow.log_metric(f"corridor_count_{region}", fit["n_clusters"])
            mlflow.log_metric(f"noise_pct_{region}", fit["noise_pct"])
            if fit["min_samples"] is not None:
                mlflow.log_metric(f"min_samples_{region}", fit["min_samples"])
            if fit["eps"] is not None:
                mlflow.log_metric(f"eps_{region}", fit["eps"])
            if fit["silhouette"] is not None:
                mlflow.log_metric(f"silhouette_{region}", fit["silhouette"])

            if fit["k_distances"] is not None:
                fig, ax = plt.subplots()
                ax.plot(fit["k_distances"])
                ax.axhline(
                    fit["eps"], color="red", linestyle="--", label=f"chosen eps={fit['eps']:.3f}"
                )
                ax.set_xlabel("points sorted by k-distance")
                ax.set_ylabel(f"{fit['min_samples']}-NN distance (scaled feature space)")
                ax.set_title(f"DBSCAN k-distance elbow — {region}")
                ax.legend()
                plot_path = f"{settings.plots_dir}/corridor_kdistance_elbow_{region}.png"
                fig.savefig(plot_path, dpi=100, bbox_inches="tight")
                plt.close(fig)
                mlflow.log_artifact(plot_path)

    engine = create_engine(settings.database_url)
    write_cols = [
        c for c in corridors_df.columns if c not in ("altitude_mean_ft", "altitude_std_ft")
    ]
    assignments_df = cruise[
        ["icao24", "time_position", "latitude", "longitude", "corridor_id", "ingest_ts"]
    ]
    # TRUNCATE (not DROP/replace) so a table identity survives a retrain even
    # after a dbt staging view is later built on top of it — same class of
    # bug fixed in ml/anomaly.py and gold_batch.py (see PROGRESS.md).
    with engine.begin() as conn:
        for table in ("flight_corridors", "corridor_assignments"):
            exists = conn.exec_driver_sql(
                f"SELECT to_regclass('gold.{table}') IS NOT NULL"
            ).scalar()
            if exists:
                conn.exec_driver_sql(f"TRUNCATE TABLE gold.{table}")
    corridors_df[write_cols].to_sql(
        "flight_corridors", engine, schema="gold", if_exists="append", index=False, method="multi"
    )
    assignments_df.to_sql(
        "corridor_assignments",
        engine,
        schema="gold",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )
    logger.info(
        "Wrote gold.flight_corridors (%d corridors) and gold.corridor_assignments (%d rows)",
        len(corridors_df),
        len(assignments_df),
    )

    logger.info("Top 5 corridors overall by member count:")
    for row in corridors_df.head(5).itertuples():
        logger.info(
            "  corridor %d [%s]: centroid=(%.2f,%.2f) heading=%.0f alt_p50=%.0fft members=%d",
            row.corridor_id,
            row.region,
            row.centroid_lat,
            row.centroid_lon,
            row.modal_heading_deg,
            row.altitude_p50_ft,
            row.member_count,
        )

    india_corridors = corridors_df[corridors_df["region"] == "india"].sort_values(
        "member_count", ascending=False
    )
    logger.info(
        "Top %d India corridors by member count (endpoints from the corridor's own "
        "polyline, for sanity-checking against real routes like DEL-BOM/DEL-BLR/BOM-BLR):",
        min(5, len(india_corridors)),
    )
    for row in india_corridors.head(5).itertuples():
        poly = json.loads(row.polyline)
        start, end = poly[0], poly[-1]
        logger.info(
            "  India corridor %d: %s -> %s heading=%.0f members=%d",
            row.corridor_id,
            start,
            end,
            row.modal_heading_deg,
            row.member_count,
        )

    return cruise[["icao24", "time_position", "latitude", "longitude", "corridor_id"]]


if __name__ == "__main__":
    run()
