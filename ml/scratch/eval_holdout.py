"""
Old-vs-new artifact comparison on HOLDOUT days (days train_all.py never saw).

  corridors : how close real cruise traffic on unseen days sits to a corridor
  anomaly   : share of unseen traffic beyond each artifact's own threshold
  traj      : next-position MAE vs the dead-reckoning baseline
  forecast  : hourly flight-count MAE on unseen hours

Run: uv run --group ml python3 ml/scratch/eval_holdout.py [old_dir] [new_dir]
"""

import glob
import json
import os
import sys

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.neighbors import NearestNeighbors

sys.path.insert(0, os.path.dirname(__file__))
import train_all as T  # noqa: E402  (reuses DAY_SQL, BRONZE_ROOT, HOLDOUT_DAYS)

OLD = sys.argv[1] if len(sys.argv) > 1 else "ml/scratch/artifacts_prev_2026-09-09"
NEW = sys.argv[2] if len(sys.argv) > 2 else "ml/scratch/artifacts"
KM_PER_DEG = 111.0


def load_holdout():
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    cruise, pairs, all_pts, hourly = [], [], [], []
    for d in T.HOLDOUT_DAYS:
        files = glob.glob(f"{T.BRONZE_ROOT}/ingest_date={d}/*/*.gz")
        con.execute(T.DAY_SQL, [files])
        cruise.append(con.execute(
            "SELECT lat, lon FROM day WHERE alt > 3000 AND heading IS NOT NULL "
            "USING SAMPLE 2 PERCENT (bernoulli, 7)").fetchdf())
        all_pts.append(con.execute(
            "SELECT lat, lon FROM day USING SAMPLE 1 PERCENT (bernoulli, 7)").fetchdf())
        pairs.append(con.execute("""
            SELECT lat, lon, alt, velocity, heading, vertical_rate, dt_s, delta_lat, delta_lon FROM (
              SELECT *, lead(lat) OVER w - lat AS delta_lat, lead(lon) OVER w - lon AS delta_lon,
                     epoch(lead(ts) OVER w) - epoch(ts) AS dt_s
              FROM day WINDOW w AS (PARTITION BY icao24 ORDER BY ts))
            WHERE dt_s > 30 AND dt_s < 180 AND heading IS NOT NULL AND vertical_rate IS NOT NULL
              AND delta_lat IS NOT NULL AND delta_lon IS NOT NULL
            USING SAMPLE 1 PERCENT (bernoulli, 7)""").fetchdf())
    # forecast needs the day before the holdout too, for the lag24 feature
    all_days = sorted(os.path.basename(p).split("=")[1] for p in glob.glob(f"{T.BRONZE_ROOT}/ingest_date=*"))
    first = all_days.index(T.HOLDOUT_DAYS[0])
    for d in all_days[max(first - 1, 0): all_days.index(T.HOLDOUT_DAYS[-1]) + 1]:
        files = glob.glob(f"{T.BRONZE_ROOT}/ingest_date={d}/*/*.gz")
        con.execute(T.DAY_SQL, [files])
        hourly.append(con.execute(
            "SELECT date_trunc('hour', ts) AS ts, count(DISTINCT icao24) AS flight_count "
            "FROM day GROUP BY 1").fetchdf())
    return (pd.concat(cruise), pd.concat(pairs), pd.concat(all_pts),
            pd.concat(hourly).sort_values("ts"))


def densify(polyline, step_deg=0.1):
    pts = []
    for (a, b), (c, d) in zip(polyline[:-1], polyline[1:]):
        n = max(int(np.hypot(c - a, d - b) / step_deg), 1)
        for t in np.linspace(0, 1, n, endpoint=False):
            pts.append((a + (c - a) * t, b + (d - b) * t))
    pts.append(tuple(polyline[-1]))
    return pts


def flat(lat, lon):
    return np.column_stack([np.asarray(lat) * KM_PER_DEG,
                            np.asarray(lon) * KM_PER_DEG * np.cos(np.radians(48.0))])


def eval_corridors(art, cruise):
    corr = json.load(open(f"{art}/corridors.json"))
    pts = [p for c in corr for p in densify(c["polyline"])]
    nn = NearestNeighbors(n_neighbors=1).fit(flat(*zip(*pts)))
    dist = nn.kneighbors(flat(cruise["lat"], cruise["lon"]))[0][:, 0]
    counts = np.array([c["member_count"] for c in corr])
    return {
        "corridors": len(corr),
        "largest_share_%": round(100 * counts.max() / counts.sum(), 1),
        "median_km_to_corridor": round(float(np.median(dist)), 1),
        "within_25km_%": round(100 * float((dist <= 25).mean()), 1),
        "within_50km_%": round(100 * float((dist <= 50).mean()), 1),
    }


def eval_anomaly(art, pts):
    corr = json.load(open(f"{art}/corridors.json"))
    cent = np.array([[c["centroid_lat"], c["centroid_lon"]] for c in corr])
    nn = NearestNeighbors(n_neighbors=1).fit(cent)
    d = nn.kneighbors(pts[["lat", "lon"]].to_numpy())[0][:, 0]
    thr = float(open(f"{art}/anomaly_threshold.txt").read())
    return {"threshold_deg": round(thr, 3), "holdout_flagged_%": round(100 * float((d > thr).mean()), 2)}


def eval_traj(art, pairs):
    cols = ["lat", "lon", "alt", "velocity", "heading", "vertical_rate", "dt_s"]
    X = pairs[cols].to_numpy()
    lat = joblib.load(f"{art}/traj_gbr_lat.joblib").predict(X)
    lon = joblib.load(f"{art}/traj_gbr_lon.joblib").predict(X)
    h = np.radians(pairs["heading"].to_numpy())
    base_lat = pairs["velocity"].to_numpy() * np.cos(h) / 111_000 * pairs["dt_s"].to_numpy()
    base_lon = pairs["velocity"].to_numpy() * np.sin(h) / 111_000 * pairs["dt_s"].to_numpy()
    y_lat, y_lon = pairs["delta_lat"].to_numpy(), pairs["delta_lon"].to_numpy()
    m = mean_absolute_error(y_lat, lat) + mean_absolute_error(y_lon, lon)
    b = mean_absolute_error(y_lat, base_lat) + mean_absolute_error(y_lon, base_lon)
    return {"mae_sum_deg": round(m, 6), "baseline_mae_sum_deg": round(b, 6),
            "improvement_over_baseline_%": round(100 * (1 - m / b), 1)}


def eval_forecast(art, hourly):
    hourly = hourly.groupby("ts", as_index=False)["flight_count"].max()
    idx = pd.date_range(hourly["ts"].min(), hourly["ts"].max(), freq="1h")
    h = hourly.set_index("ts").reindex(idx).rename_axis("ts").reset_index()
    h["hour_of_day"], h["day_of_week"] = h["ts"].dt.hour, h["ts"].dt.dayofweek
    h["lag1"], h["lag24"] = h["flight_count"].shift(1), h["flight_count"].shift(24)
    h = h.dropna()
    h = h[h["ts"].dt.strftime("%Y-%m-%d").isin(T.HOLDOUT_DAYS)]
    h = h.iloc[:-1]  # last hour of a still-running day is partial
    model = joblib.load(f"{art}/forecast_gbr.joblib")
    pred = model.predict(h[["hour_of_day", "day_of_week", "lag1", "lag24"]].to_numpy())
    y = h["flight_count"].to_numpy()
    return {"hours": len(h), "mae": round(mean_absolute_error(y, pred), 1),
            "mean_count": round(float(y.mean())),
            "naive_lag1_mae": round(mean_absolute_error(y, h["lag1"].to_numpy()), 1)}


def main():
    cruise, pairs, all_pts, hourly = load_holdout()
    print(f"holdout days {T.HOLDOUT_DAYS}: {len(cruise):,} cruise pts, {len(pairs):,} pairs, "
          f"{len(hourly)} hourly buckets\n")
    for name, fn, data in [("corridors", eval_corridors, cruise), ("anomaly", eval_anomaly, all_pts),
                           ("trajectory", eval_traj, pairs), ("forecast", eval_forecast, hourly)]:
        print(f"== {name}")
        for label, art in [("old", OLD), ("new", NEW)]:
            print(f"  {label}: {fn(art, data)}")


if __name__ == "__main__":
    main()
