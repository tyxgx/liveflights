"""
Local test of the FIXED corridor-discovery step, against the same real
bronze backup train_all.py uses (data/s3-backup-2026-08-28/bronze/).

Two real bugs found in ml/scratch/train_all.py's Model 1 (2026-08-31):

1. Raw heading (0-360deg) used as a DBSCAN feature instead of
   sin/cos(heading) -- 359deg and 1deg are almost the same direction but
   look maximally far apart to Euclidean distance. ml/corridors.py already
   documented and fixed this exact bug; the Aug-29 fast-path rewrite
   (train_all.py) reintroduced it.
2. Fixed eps=0.5 across a whole 10x10deg grid cell (~700x700km) instead of
   a per-cell, data-driven eps (k-distance knee) -- one fixed eps over that
   much area just carves out 1-3 mega-blobs per cell, not real airway-width
   corridors. The pre-pause cloud pipeline (infra git history, commit
   2a7c487) already proved the per-cell-knee-eps fix: 646 corridors,
   largest share ~20%, vs the 26-corridor/23%-largest result the current
   fast-path artifact produces.

This script re-applies that exact proven recipe (sin/cos heading,
MIN_SAMPLES=8, per-cell k-distance-knee eps, real member-point-sampled
polyline instead of a synthetic 2-point line) and reports a direct
before/after comparison. Read-only against the real backup -- doesn't
touch ml/scratch/artifacts/corridors.json (the one the deployed API
currently serves).

Run: uv run --group ml python3 ml/scratch/train_corridors_fixed.py
"""

import glob
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ingestion.airports import AIRPORTS_EUROPE  # noqa: E402

DATA_DAYS = [
    "2026-08-21", "2026-08-23", "2026-08-24", "2026-08-25",
    "2026-08-26", "2026-08-27", "2026-08-28",
]
BRONZE_ROOT = "data/s3-backup-2026-08-28/bronze"
OUT_DIR = "ml/scratch/artifacts"

CELL_SIZE_DEG = 3.0
MIN_SAMPLES = 8
MIN_ROWS_FOR_FIT = MIN_SAMPLES * 2

# How far a corridor's own endpoint is allowed to be from a major airport
# to count as "serving" it. Corridor points are cruise-phase only (alt >
# 3000m ~ 9800ft, see load_cruise) -- a real jet is still 150-300km out
# from its origin/destination at that altitude during climb/descent, so
# the corridor's own polyline naturally stops well short of the runway.
# This is a nearest-major-airport heuristic, not a matched flight plan:
# ADS-B carries no route data (see docs/architecture.md) -- label it as
# such wherever it's surfaced.
AIRPORT_SNAP_MAX_KM = 400.0
# The airport must lie roughly ahead of the corridor's own outward
# direction at that end, not behind it -- otherwise a nearby-but-wrong-way
# airport (e.g. one behind the aircraft's tail) would get snapped just
# because it's close.
AIRPORT_SNAP_MAX_BEARING_DIFF_DEG = 75.0

EARTH_RADIUS_KM = 6371.0


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
    """Nearest major airport to `point`, but only counted if it's roughly
    in the direction the corridor is already heading away from
    `outward_from` (see AIRPORT_SNAP_MAX_BEARING_DIFF_DEG) -- otherwise a
    close airport sitting behind the corridor's own end would get snapped
    just for being nearby.
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


def choose_eps_via_knee(scaled: np.ndarray, k: int) -> float:
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
    axis and take each bin's MEAN position -- not raw sampled points.

    Two passes were tried:
    1. Sort by raw latitude: mixes along-track and cross-track scatter into
       one order -- zigzags badly for any corridor not running due
       north-south.
    2. Sort by heading-axis projection, then SAMPLE 10 raw points along
       that order (ml/corridors.py's original technique): fixes the
       ordering, but a wide/dense corridor (real airway bundles near a
       hub can be tens of thousands of points across a few degrees) still
       has enough cross-track scatter between consecutive sampled points
       to zigzag visibly.
    This binned-mean pass keeps the correct along-track order from (2) but
    averages out the cross-track noise within each along-track slice,
    producing one smooth spine through the corridor instead of a jagged
    walk through individual member points.
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


def load_cruise() -> pd.DataFrame:
    files = []
    for d in DATA_DAYS:
        files += glob.glob(f"{BRONZE_ROOT}/ingest_date={d}/*/*.gz")
    con = duckdb.connect()
    df = con.execute(
        """
        SELECT icao24, longitude AS lon, latitude AS lat,
               baro_altitude AS alt, velocity, true_track AS heading
        FROM read_json_auto(?, format='newline_delimited')
        WHERE on_ground = false
          AND longitude IS NOT NULL AND latitude IS NOT NULL
          AND velocity IS NOT NULL AND velocity BETWEEN 0 AND 420
          AND baro_altitude IS NOT NULL AND baro_altitude BETWEEN 0 AND 15550
        """,
        [files],
    ).fetchdf()
    cruise = df[df["alt"] > 3000].dropna(subset=["lat", "lon", "heading"]).copy()
    if len(cruise) > 800_000:
        cruise = cruise.sample(800_000, random_state=42)
    return cruise


def main():
    t0 = time.time()
    cruise = load_cruise()
    print(f"[load] {len(cruise):,} cruise-phase rows in {time.time()-t0:.1f}s")

    cruise["track_sin"] = np.sin(np.radians(cruise["heading"]))
    cruise["track_cos"] = np.cos(np.radians(cruise["heading"]))
    cruise["grid_lat"] = (cruise["lat"] // CELL_SIZE_DEG).astype(int)
    cruise["grid_lon"] = (cruise["lon"] // CELL_SIZE_DEG).astype(int)

    all_corridors = []
    corridor_id = 0
    noise_count = 0
    for (glat, glon), cell in cruise.groupby(["grid_lat", "grid_lon"]):
        if len(cell) < MIN_ROWS_FOR_FIT:
            noise_count += len(cell)
            continue
        X = cell[["lat", "lon", "track_sin", "track_cos"]].to_numpy()
        Xs = StandardScaler().fit_transform(X)
        eps = choose_eps_via_knee(Xs, k=MIN_SAMPLES)
        labels = DBSCAN(eps=eps, min_samples=MIN_SAMPLES).fit_predict(Xs)
        noise_count += int((labels == -1).sum())
        for lbl in sorted(set(labels) - {-1}):
            members = cell.iloc[labels == lbl]
            heading_deg = round(modal_heading(members["track_sin"], members["track_cos"]), 1)
            polyline = build_polyline(members, heading_deg)

            # Snap each end to the nearest major airport ahead of it, if
            # one exists within AIRPORT_SNAP_MAX_KM. Nullable both ends --
            # most corridor segments (especially the small, low-member-
            # count ones far from any hub) won't snap to anything, and
            # that's the honest answer, not a bug.
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

            all_corridors.append(
                {
                    "corridor_id": corridor_id,
                    "grid_cell": f"{glat*CELL_SIZE_DEG:.0f},{glon*CELL_SIZE_DEG:.0f}",
                    "centroid_lat": round(float(members["lat"].mean()), 5),
                    "centroid_lon": round(float(members["lon"].mean()), 5),
                    "modal_heading_deg": heading_deg,
                    "altitude_p10_ft": round(float(members["alt"].quantile(0.10)) * 3.28084, 1),
                    "altitude_p50_ft": round(float(members["alt"].quantile(0.50)) * 3.28084, 1),
                    "altitude_p90_ft": round(float(members["alt"].quantile(0.90)) * 3.28084, 1),
                    "member_count": int(len(members)),
                    "polyline": polyline,
                    "airports": airports,
                }
            )
            corridor_id += 1

    corridors_df = pd.DataFrame(all_corridors).sort_values("member_count", ascending=False)
    noise_pct = noise_count / len(cruise) * 100

    print(f"\n[FIXED] {len(corridors_df)} corridors from {len(cruise):,} cruise points "
          f"in {time.time()-t0:.1f}s")
    print(f"[FIXED] noise: {noise_pct:.1f}% of points unassigned")
    if len(corridors_df):
        top_share = corridors_df["member_count"].max() / corridors_df["member_count"].sum()
        top3_share = corridors_df["member_count"].nlargest(3).sum() / corridors_df["member_count"].sum()
        print(f"[FIXED] largest corridor share: {top_share:.1%} (old buggy result: 23.0%)")
        print(f"[FIXED] top-3 corridors share: {top3_share:.1%}")
        print(f"[FIXED] median member_count: {corridors_df['member_count'].median():.0f}")
        both = corridors_df["airports"].apply(lambda a: all(x is not None for x in a)).sum()
        one = corridors_df["airports"].apply(lambda a: any(x is not None for x in a)).sum()
        print(
            f"[FIXED] airport-snapped: {both} corridors with BOTH ends near a major airport, "
            f"{one} with at least one end (of {len(corridors_df)} total)"
        )

    out_path = f"{OUT_DIR}/corridors_fixed_test.json"
    corridors_df.to_json(out_path, orient="records")
    print(f"\n[saved] {out_path} (test artifact -- NOT the live corridors.json)")

    print("\nTop 10 corridors:")
    for row in corridors_df.head(10).itertuples():
        print(
            f"  #{row.corridor_id} cell={row.grid_cell} centroid=({row.centroid_lat:.2f},"
            f"{row.centroid_lon:.2f}) heading={row.modal_heading_deg:.0f} "
            f"members={row.member_count}"
        )


if __name__ == "__main__":
    main()
