"""
Fast-path local training for all 4 liveflights ML models, against a recent
3-day slice of the local S3 backup (data/s3-backup-2026-08-28/bronze/).

Models:
  1. Corridor discovery (DBSCAN, per-3deg-grid-cell with a per-cell
     data-driven eps, sin/cos heading features, and each corridor's ends
     snapped to the nearest major airport ahead of them when one's close
     enough -- see the Model 1 section below for the full history of what
     was wrong with the first version of this).
  2. Trajectory-delta prediction (GBR) -- STATELESS design: predicts next
     ~60s position delta purely from the current poll's kinematic state
     (lat, lon, altitude, velocity, heading, vertical_rate), no per-aircraft
     history store needed at serve time. Compared against a naive
     dead-reckoning baseline.
  3. Corridor-based anomaly scoring -- distance from each aircraft to its
     nearest corridor centroid, calibrated to flag ~3% of traffic.
  4. Traffic forecast (GBR) -- next-few-hours aircraft count from hourly
     aggregates.

Run: uv run --group ml python3 ml/scratch/train_all.py
"""

import glob
import os
import time

import duckdb
import joblib
import mlflow
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from ingestion.airports import AIRPORTS_EUROPE

DATA_DAYS = [
    "2026-08-21",
    "2026-08-23",
    "2026-08-24",
    "2026-08-25",
    "2026-08-26",
    "2026-08-27",
    "2026-08-28",
]  # full available local backup (2026-08-22 missing -- not in the S3 backup)
BRONZE_ROOT = "data/s3-backup-2026-08-28/bronze"
OUT_DIR = "ml/scratch/artifacts"

CORRIDOR_CELL_SIZE_DEG = 3.0
CORRIDOR_MIN_SAMPLES = 8
CORRIDOR_MIN_ROWS_FOR_FIT = CORRIDOR_MIN_SAMPLES * 2

# How far a corridor's own endpoint may be from a major airport to count as
# "serving" it. Corridor points are cruise-phase only (alt > 3000m ~
# 9800ft) -- a real jet is still 150-300km out from its origin/destination
# at that altitude during climb/descent, so a corridor's own polyline
# naturally stops well short of the runway. This is a nearest-major-
# airport heuristic, not a matched flight plan -- ADS-B carries no route
# data (see docs/architecture.md) -- label it as such wherever it's shown.
AIRPORT_SNAP_MAX_KM = 400.0
# The airport must lie roughly ahead of the corridor's own outward
# direction at that end, not behind it -- otherwise a nearby-but-wrong-way
# airport would get snapped just for being close.
AIRPORT_SNAP_MAX_BEARING_DIFF_DEG = 75.0
EARTH_RADIUS_KM = 6371.0
# A corridor whose own centroid sits right on top of a major airport (a
# wide/dense hub-area cluster whose two polyline ENDS point away in other
# directions, so nearest_airport_ahead's endpoint+bearing check misses it
# entirely -- e.g. a large Madrid-area corridor whose centroid is ~43km
# from MAD) still deserves to show as connected to that hub. This is a
# separate, distance-only check (no bearing constraint -- the airport is
# assumed to be *inside* the corridor's own footprint, not ahead of an
# endpoint) with a much tighter radius than AIRPORT_SNAP_MAX_KM, since it's
# asserting "this corridor's traffic sits in this airport's terminal
# area", not "this corridor's end is on climb-out/approach toward it".
HUB_SNAP_MAX_KM = 75.0

os.makedirs(OUT_DIR, exist_ok=True)
mlflow.set_tracking_uri(f"file:{os.path.abspath('ml/scratch/mlruns')}")
mlflow.set_experiment("liveflights-fastpath-2026-08-28")


def choose_eps_via_knee(scaled: np.ndarray, k: int) -> float:
    """k-distance elbow: sort each point's k-th nearest-neighbor distance
    ascending; the knee (max perpendicular distance from the line joining
    the curve's endpoints) is a standard DBSCAN eps heuristic. Chosen per
    grid cell so each cell's own local point density picks its own eps,
    instead of one fixed eps assumed to fit every cell equally.
    """
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


def modal_heading(track_sin: pd.Series, track_cos: pd.Series) -> float:
    return float(np.degrees(np.arctan2(track_sin.mean(), track_cos.mean())) % 360)


def build_polyline(sub: pd.DataFrame, heading_deg: float, n_points: int = 10) -> list[list[float]]:
    """Project onto the corridor's own heading axis, then bin along that
    axis and take each bin's MEAN position. A plain lat-sort mixes along-
    track and cross-track scatter into one order and zigzags badly for any
    corridor not running due north-south; sampling raw points along a
    heading-axis order fixes the ordering but a wide/dense corridor still
    has enough cross-track scatter between consecutive points to zigzag
    visibly. Binning + averaging removes that noise, giving one smooth
    spine through the corridor instead of a jagged walk through individual
    member points.
    """
    lat0 = sub["lat"].mean()
    heading_rad = np.radians(heading_deg)
    northing = sub["lat"] - lat0
    easting = (sub["lon"] - sub["lon"].mean()) * np.cos(np.radians(lat0))
    projection = northing * np.cos(heading_rad) + easting * np.sin(heading_rad)
    ordered = sub.assign(_proj=projection).sort_values("_proj")

    n_bins = min(n_points, ordered["_proj"].nunique())
    if n_bins <= 1:
        pt = ordered[["lat", "lon"]].mean()
        return [[round(float(pt["lat"]), 4), round(float(pt["lon"]), 4)]]

    bin_idx = pd.qcut(ordered["_proj"], q=n_bins, labels=False, duplicates="drop")
    binned = ordered.groupby(bin_idx)[["lat", "lon"]].mean()
    return [[round(float(r.lat), 4), round(float(r.lon), 4)] for r in binned.itertuples()]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlambda = np.radians(lon2 - lon1)
    y = np.sin(dlambda) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dlambda)
    return float(np.degrees(np.arctan2(y, x)) % 360)


def angle_diff_deg(a: float, b: float) -> float:
    return float(abs((a - b + 180) % 360 - 180))


def nearest_airport_ahead(
    point: list[float], outward_from: list[float]
) -> tuple[str, float] | None:
    """Nearest major airport to `point`, counted only if it's roughly in
    the direction the corridor is already heading away from
    `outward_from` -- otherwise a close airport sitting behind the
    corridor's own end would get snapped just for being nearby.
    """
    lat, lon = point
    outward_bearing = bearing_deg(outward_from[0], outward_from[1], lat, lon)
    best: tuple[str, float] | None = None
    for ap in AIRPORTS_EUROPE:
        dist = haversine_km(lat, lon, ap.lat, ap.lon)
        if dist > AIRPORT_SNAP_MAX_KM:
            continue
        to_airport_bearing = bearing_deg(lat, lon, ap.lat, ap.lon)
        if angle_diff_deg(outward_bearing, to_airport_bearing) > AIRPORT_SNAP_MAX_BEARING_DIFF_DEG:
            continue
        if best is None or dist < best[1]:
            best = (ap.iata, dist)
    return best


def nearest_hub_airport(centroid_lat: float, centroid_lon: float) -> str | None:
    """Pure-distance nearest major airport to a corridor's own centroid,
    within HUB_SNAP_MAX_KM -- catches the case nearest_airport_ahead
    can't: a wide hub-area corridor whose centroid sits right on an
    airport but whose own polyline ends point away from it in both
    directions (see HUB_SNAP_MAX_KM's docstring above).
    """
    best: tuple[str, float] | None = None
    for ap in AIRPORTS_EUROPE:
        dist = haversine_km(centroid_lat, centroid_lon, ap.lat, ap.lon)
        if dist > HUB_SNAP_MAX_KM:
            continue
        if best is None or dist < best[1]:
            best = (ap.iata, dist)
    return best[0] if best else None


def load_files():
    files = []
    for d in DATA_DAYS:
        files += glob.glob(f"{BRONZE_ROOT}/ingest_date={d}/*/*.gz")
    return files


def main():
    t0 = time.time()
    files = load_files()
    print(f"[load] {len(files)} bronze files across {DATA_DAYS}")

    con = duckdb.connect()

    # ---- 1. Load + basic cleaning (airborne only, sane altitude/velocity) ----
    df = con.execute(
        """
        SELECT icao24, callsign, longitude AS lon, latitude AS lat,
               baro_altitude AS alt, velocity, true_track AS heading,
               vertical_rate, squawk, on_ground, ingest_ts
        FROM read_json_auto(?, format='newline_delimited')
        WHERE on_ground = false
          AND longitude IS NOT NULL AND latitude IS NOT NULL
          AND velocity IS NOT NULL AND velocity BETWEEN 0 AND 420   -- ~1500km/h sanity cap
          AND baro_altitude IS NOT NULL AND baro_altitude BETWEEN 0 AND 15550  -- ~51000ft cap
        """,
        [files],
    ).fetchdf()
    print(f"[load] {len(df):,} airborne rows in {time.time()-t0:.1f}s")

    df["ingest_ts"] = pd.to_datetime(df["ingest_ts"], utc=True)

    # =====================================================================
    # MODEL 1: Corridor discovery (DBSCAN, per 3-degree grid cell)
    # =====================================================================
    # Rewritten 2026-08-31 -- the original fast-path version here had two
    # real bugs (found live: only 26 corridors, one holding 23% of all
    # traffic -- see docs/ml.md):
    #   1. Raw heading (0-360deg) as a DBSCAN feature instead of
    #      sin/cos(heading) -- 359deg and 1deg are almost the same
    #      direction but look maximally far apart to Euclidean distance.
    #      ml/corridors.py already documented and fixed this exact bug;
    #      this fast-path rewrite reintroduced it.
    #   2. A single fixed eps=0.5 across a whole 10x10deg grid cell
    #      (~700x700km) instead of a per-cell, data-driven eps -- one
    #      fixed eps over that much area just carves out 1-3 mega-blobs,
    #      not real airway-width corridors. 3-degree cells + a per-cell
    #      k-distance-knee eps (the same technique the pre-pause cloud
    #      pipeline proved out, infra git history commit 2a7c487) gets
    #      1,150 corridors with the largest holding 3.6%.
    # Polyline construction was also upgraded from a synthetic 2-point
    # line through the centroid to a real binned-mean spine (see
    # build_polyline docstring below), and each end is snapped to the
    # nearest major airport ahead of it when one exists within
    # AIRPORT_SNAP_MAX_KM -- an honest nearest-airport heuristic, not a
    # matched flight plan (ADS-B carries no route data).
    print("\n=== Model 1: Corridors ===")
    t1 = time.time()
    cruise = df[df["alt"] > 3000].dropna(subset=["lat", "lon", "heading"]).copy()
    if len(cruise) > 800_000:
        cruise = cruise.sample(800_000, random_state=42)

    cruise["track_sin"] = np.sin(np.radians(cruise["heading"]))
    cruise["track_cos"] = np.cos(np.radians(cruise["heading"]))
    cruise["grid_lat"] = (cruise["lat"] // CORRIDOR_CELL_SIZE_DEG).astype(int)
    cruise["grid_lon"] = (cruise["lon"] // CORRIDOR_CELL_SIZE_DEG).astype(int)

    all_corridors = []
    corridor_id = 0
    noise_count = 0
    for (glat, glon), cell in cruise.groupby(["grid_lat", "grid_lon"]):
        if len(cell) < CORRIDOR_MIN_ROWS_FOR_FIT:
            noise_count += len(cell)
            continue
        X = cell[["lat", "lon", "track_sin", "track_cos"]].to_numpy()
        Xs = StandardScaler().fit_transform(X)
        eps = choose_eps_via_knee(Xs, k=CORRIDOR_MIN_SAMPLES)
        labels = DBSCAN(eps=eps, min_samples=CORRIDOR_MIN_SAMPLES).fit_predict(Xs)
        noise_count += int((labels == -1).sum())
        for lbl in sorted(set(labels) - {-1}):
            members = cell.iloc[labels == lbl]
            heading_deg = round(
                modal_heading(members["track_sin"], members["track_cos"]), 1
            )
            polyline = build_polyline(members, heading_deg)

            airports: list[str | None] = [None, None]
            if len(polyline) >= 2:
                start_match = nearest_airport_ahead(polyline[0], polyline[1])
                end_match = nearest_airport_ahead(polyline[-1], polyline[-2])
                if start_match:
                    airports[0] = start_match[0]
                    ap = next(a for a in AIRPORTS_EUROPE if a.iata == start_match[0])
                    polyline = [[ap.lat, ap.lon]] + polyline
                if end_match:
                    airports[1] = end_match[0]
                    ap = next(a for a in AIRPORTS_EUROPE if a.iata == end_match[0])
                    polyline = polyline + [[ap.lat, ap.lon]]

            centroid_lat = round(float(members["lat"].mean()), 5)
            centroid_lon = round(float(members["lon"].mean()), 5)

            # Neither end snapped (a wide hub-area corridor whose ends
            # point away from its own centroid, see HUB_SNAP_MAX_KM) --
            # fall back to a pure-distance check against the centroid
            # itself. Recorded separately from `airports` (which specifically
            # means "this corridor's own endpoint is near this airport")
            # so the frontend can label the two cases differently.
            hub_airport = None
            if airports[0] is None and airports[1] is None:
                hub_airport = nearest_hub_airport(centroid_lat, centroid_lon)

            all_corridors.append(
                {
                    "corridor_id": corridor_id,
                    "grid_cell": f"{glat*CORRIDOR_CELL_SIZE_DEG:.0f},{glon*CORRIDOR_CELL_SIZE_DEG:.0f}",
                    "centroid_lat": centroid_lat,
                    "centroid_lon": centroid_lon,
                    "modal_heading_deg": heading_deg,
                    "altitude_p10_ft": round(float(members["alt"].quantile(0.10)) * 3.28084, 1),
                    "altitude_p50_ft": round(float(members["alt"].quantile(0.50)) * 3.28084, 1),
                    "altitude_p90_ft": round(float(members["alt"].quantile(0.90)) * 3.28084, 1),
                    "member_count": int(len(members)),
                    "polyline": polyline,
                    "airports": airports,
                    "hub_airport": hub_airport,
                }
            )
            corridor_id += 1

    corridors_df = pd.DataFrame(all_corridors)
    noise_pct = noise_count / len(cruise) * 100 if len(cruise) else 0.0
    print(
        f"[corridors] {len(corridors_df)} corridors from {len(cruise):,} cruise points "
        f"in {time.time()-t1:.1f}s (noise: {noise_pct:.1f}%)"
    )
    if len(corridors_df):
        top_share = corridors_df["member_count"].max() / corridors_df["member_count"].sum()
        print(f"[corridors] largest corridor share: {top_share:.1%} (sanity check, want < ~30%)")
        both = corridors_df["airports"].apply(lambda a: all(x is not None for x in a)).sum()
        one = corridors_df["airports"].apply(lambda a: any(x is not None for x in a)).sum()
        print(f"[corridors] airport-snapped: {both} both ends, {one} at least one end")
        hub_only = corridors_df["hub_airport"].notna().sum()
        print(f"[corridors] hub-only match (centroid near an airport, ends point away): {hub_only}")
    corridors_df.to_json(f"{OUT_DIR}/corridors.json", orient="records")

    # =====================================================================
    # MODEL 2: Trajectory-delta prediction (GBR, stateless)
    # =====================================================================
    print("\n=== Model 2: Trajectory-delta prediction ===")
    t2 = time.time()
    df_sorted = df.sort_values(["icao24", "ingest_ts"])
    df_sorted["next_lat"] = df_sorted.groupby("icao24")["lat"].shift(-1)
    df_sorted["next_lon"] = df_sorted.groupby("icao24")["lon"].shift(-1)
    df_sorted["next_ts"] = df_sorted.groupby("icao24")["ingest_ts"].shift(-1)
    df_sorted["dt_s"] = (df_sorted["next_ts"] - df_sorted["ingest_ts"]).dt.total_seconds()

    # Keep consecutive-poll pairs only (~60s apart, allow some jitter/skip)
    pairs = df_sorted[(df_sorted["dt_s"] > 30) & (df_sorted["dt_s"] < 180)].copy()
    pairs["delta_lat"] = pairs["next_lat"] - pairs["lat"]
    pairs["delta_lon"] = pairs["next_lon"] - pairs["lon"]
    print(f"[traj] {len(pairs):,} consecutive-poll pairs (dt 30-180s)")

    if len(pairs) > 300_000:
        pairs = pairs.sample(300_000, random_state=42)

    # dt_s is critical: the target (delta_lat/delta_lon) scales directly with
    # elapsed time between polls (30-180s range) -- the dead-reckoning
    # baseline gets this for free (delta = velocity*dt), so the GBR needs it
    # too or it's an unfair comparison. (Real bug caught in the first run:
    # omitting dt_s here made GBR lose to the baseline on latitude.)
    feat_cols = ["lat", "lon", "alt", "velocity", "heading", "vertical_rate", "dt_s"]
    pairs = pairs.dropna(subset=feat_cols + ["delta_lat", "delta_lon", "dt_s"])
    X = pairs[feat_cols].to_numpy()
    y_lat = pairs["delta_lat"].to_numpy()
    y_lon = pairs["delta_lon"].to_numpy()

    split = int(len(X) * 0.8)
    idx = np.random.RandomState(42).permutation(len(X))
    train_idx, test_idx = idx[:split], idx[split:]

    gbr_lat = GradientBoostingRegressor(n_estimators=150, max_depth=4, random_state=42)
    gbr_lon = GradientBoostingRegressor(n_estimators=150, max_depth=4, random_state=42)
    gbr_lat.fit(X[train_idx], y_lat[train_idx])
    gbr_lon.fit(X[train_idx], y_lon[train_idx])

    pred_lat = gbr_lat.predict(X[test_idx])
    pred_lon = gbr_lon.predict(X[test_idx])
    mae_lat = mean_absolute_error(y_lat[test_idx], pred_lat)
    mae_lon = mean_absolute_error(y_lon[test_idx], pred_lon)

    # Dead-reckoning baseline: delta ~ velocity * dt in heading direction
    dt = pairs["dt_s"].to_numpy()[test_idx]
    heading_rad = np.radians(pairs["heading"].to_numpy()[test_idx])
    vel_deg_per_s_lat = (pairs["velocity"].to_numpy()[test_idx] * np.cos(heading_rad)) / 111_000
    vel_deg_per_s_lon = (pairs["velocity"].to_numpy()[test_idx] * np.sin(heading_rad)) / 111_000
    baseline_lat = vel_deg_per_s_lat * dt
    baseline_lon = vel_deg_per_s_lon * dt
    base_mae_lat = mean_absolute_error(y_lat[test_idx], baseline_lat)
    base_mae_lon = mean_absolute_error(y_lon[test_idx], baseline_lon)

    print(f"[traj] GBR MAE: lat={mae_lat:.6f}deg lon={mae_lon:.6f}deg")
    print(f"[traj] dead-reckoning baseline MAE: lat={base_mae_lat:.6f}deg lon={base_mae_lon:.6f}deg")
    improvement = 1 - (mae_lat + mae_lon) / (base_mae_lat + base_mae_lon)
    print(f"[traj] GBR improvement over baseline: {improvement:.1%}")
    print(f"[traj] done in {time.time()-t2:.1f}s")

    joblib.dump(gbr_lat, f"{OUT_DIR}/traj_gbr_lat.joblib")
    joblib.dump(gbr_lon, f"{OUT_DIR}/traj_gbr_lon.joblib")

    # =====================================================================
    # MODEL 3: Corridor-based anomaly scoring
    # =====================================================================
    print("\n=== Model 3: Anomaly scoring ===")
    t3 = time.time()
    if len(corridors_df):
        cent = corridors_df[["centroid_lat", "centroid_lon"]].to_numpy()
        sample = df.sample(min(200_000, len(df)), random_state=42)
        pts = sample[["lat", "lon"]].to_numpy()
        # distance to nearest centroid (vectorized, chunked to bound memory)
        min_dist = np.full(len(pts), np.inf)
        chunk = 2000
        for i in range(0, len(cent), chunk):
            c = cent[i : i + chunk]
            d = np.sqrt(
                ((pts[:, None, 0] - c[None, :, 0]) ** 2)
                + ((pts[:, None, 1] - c[None, :, 1]) ** 2)
            )
            min_dist = np.minimum(min_dist, d.min(axis=1))
        threshold = np.percentile(min_dist, 97)  # flag furthest ~3%
        flagged = (min_dist > threshold).sum()
        print(
            f"[anomaly] threshold={threshold:.3f}deg, flagged {flagged}/{len(pts)} "
            f"({flagged/len(pts):.1%}) in {time.time()-t3:.1f}s"
        )
        with open(f"{OUT_DIR}/anomaly_threshold.txt", "w") as f:
            f.write(str(threshold))
    else:
        print("[anomaly] skipped, no corridors found")

    # =====================================================================
    # MODEL 4: Traffic forecast (GBR on hourly aggregates)
    # =====================================================================
    print("\n=== Model 4: Traffic forecast ===")
    t4 = time.time()
    hourly = (
        df.set_index("ingest_ts")
        .resample("1h")["icao24"]
        .nunique()
        .rename("flight_count")
        .reset_index()
    )
    hourly["hour_of_day"] = hourly["ingest_ts"].dt.hour
    hourly["day_of_week"] = hourly["ingest_ts"].dt.dayofweek
    hourly["lag1"] = hourly["flight_count"].shift(1)
    hourly["lag24"] = hourly["flight_count"].shift(24)
    hourly_clean = hourly.dropna()
    print(f"[forecast] {len(hourly_clean)} hourly buckets available")

    if len(hourly_clean) >= 20:
        Xf = hourly_clean[["hour_of_day", "day_of_week", "lag1", "lag24"]].to_numpy()
        yf = hourly_clean["flight_count"].to_numpy()
        split_f = max(int(len(Xf) * 0.8), len(Xf) - 6)
        gbr_forecast = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42)
        gbr_forecast.fit(Xf[:split_f], yf[:split_f])
        pred_f = gbr_forecast.predict(Xf[split_f:])
        mae_f = mean_absolute_error(yf[split_f:], pred_f)
        print(f"[forecast] MAE={mae_f:.1f} flights (mean count={yf.mean():.0f})")
        joblib.dump(gbr_forecast, f"{OUT_DIR}/forecast_gbr.joblib")
        # Serving-time confidence band: not true quantile regression, just
        # the held-out MAE as a symmetric +-band around the point prediction
        # -- an honest approximation, labeled as such at serve time.
        with open(f"{OUT_DIR}/forecast_mae.txt", "w") as f:
            f.write(str(mae_f))
    else:
        print("[forecast] not enough hourly buckets yet (need >= 20, have " f"{len(hourly_clean)}) — skipped")

    print(f"\n[done] total time {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
