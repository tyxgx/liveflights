"""
Phase 2 -- validate the 4 fast-path-trained models against a REAL live poll
from the deployed cloud API (not the training data). Run:
    uv run --group ml python3 ml/scratch/validate_live.py
"""

import json
import time

import httpx
import joblib
import numpy as np
import pandas as pd

API_BASE = "https://m9o2yg64dj.execute-api.us-east-1.amazonaws.com"
ARTIFACT_DIR = "ml/scratch/artifacts"


def load_live_flights():
    r = httpx.get(f"{API_BASE}/api/flights/live", timeout=30)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data["flights"])
    return df, data["updated_at"], data["count"]


def main():
    print(f"[fetch] pulling live snapshot from {API_BASE} ...")
    df, updated_at, count = load_live_flights()
    print(f"[fetch] {count} aircraft, snapshot updated_at={updated_at}")

    df = df.rename(columns={"longitude": "lon", "latitude": "lat", "baro_altitude": "alt"})
    df = df[df["on_ground"] == False].dropna(  # noqa: E712
        subset=["lat", "lon", "alt", "velocity", "true_track", "vertical_rate"]
    ).copy()
    df = df.rename(columns={"true_track": "heading"})
    print(f"[fetch] {len(df)} airborne aircraft with complete kinematic data")

    # =====================================================================
    # Model 1: Corridors -- sanity check geography + coverage
    # =====================================================================
    print("\n=== Validating Model 1: Corridors ===")
    corridors = pd.read_json(f"{ARTIFACT_DIR}/corridors.json")
    print(f"[corridors] {len(corridors)} corridors loaded")
    print(
        f"[corridors] centroid lat range: {corridors['centroid_lat'].min():.1f} to "
        f"{corridors['centroid_lat'].max():.1f}"
    )
    print(
        f"[corridors] centroid lon range: {corridors['centroid_lon'].min():.1f} to "
        f"{corridors['centroid_lon'].max():.1f}"
    )
    europe_bbox = corridors["centroid_lat"].between(30, 72).all() and corridors[
        "centroid_lon"
    ].between(-25, 45).all()
    print(f"[corridors] all centroids inside Europe bbox: {europe_bbox}")

    # =====================================================================
    # Model 2: Trajectory-delta prediction -- predict 60s-ahead for each
    # live aircraft, sanity-check the predicted deltas are physically small
    # =====================================================================
    print("\n=== Validating Model 2: Trajectory prediction ===")
    gbr_lat = joblib.load(f"{ARTIFACT_DIR}/traj_gbr_lat.joblib")
    gbr_lon = joblib.load(f"{ARTIFACT_DIR}/traj_gbr_lon.joblib")

    live_sample = df.sample(min(2000, len(df)), random_state=1).copy()
    live_sample["dt_s"] = 60.0  # predicting 60s ahead, matching poll cadence
    feat_cols = ["lat", "lon", "alt", "velocity", "heading", "vertical_rate", "dt_s"]
    X_live = live_sample[feat_cols].to_numpy()
    pred_delta_lat = gbr_lat.predict(X_live)
    pred_delta_lon = gbr_lon.predict(X_live)

    # Sanity bound: at max ~420 m/s velocity, 60s move is at most ~25.2km =~
    # 0.23 deg lat. Any prediction wildly outside that is a red flag.
    max_plausible_deg = 0.30
    bad_lat = (np.abs(pred_delta_lat) > max_plausible_deg).sum()
    bad_lon = (np.abs(pred_delta_lon) > max_plausible_deg).sum()
    print(
        f"[traj] predicted deltas for {len(live_sample)} live aircraft: "
        f"mean |delta_lat|={np.abs(pred_delta_lat).mean():.5f}deg, "
        f"mean |delta_lon|={np.abs(pred_delta_lon).mean():.5f}deg"
    )
    print(
        f"[traj] physically-implausible predictions (> {max_plausible_deg}deg in 60s): "
        f"{bad_lat} lat, {bad_lon} lon out of {len(live_sample)}"
    )

    # Concrete example
    ex = live_sample.iloc[0]
    print(
        f"[traj] example: {ex.get('callsign','?')} at ({ex['lat']:.3f},{ex['lon']:.3f}) "
        f"velocity={ex['velocity']:.0f}m/s heading={ex['heading']:.0f}deg -> "
        f"predicted position in 60s: ({ex['lat']+pred_delta_lat[0]:.3f},"
        f"{ex['lon']+pred_delta_lon[0]:.3f})"
    )

    # =====================================================================
    # Model 3: Anomaly scoring -- score all live aircraft, check flag rate
    # =====================================================================
    print("\n=== Validating Model 3: Anomaly scoring ===")
    with open(f"{ARTIFACT_DIR}/anomaly_threshold.txt") as f:
        threshold = float(f.read().strip())

    cent = corridors[["centroid_lat", "centroid_lon"]].to_numpy()
    pts = df[["lat", "lon"]].to_numpy()
    min_dist = np.full(len(pts), np.inf)
    chunk = 2000
    for i in range(0, len(cent), chunk):
        c = cent[i : i + chunk]
        d = np.sqrt(
            ((pts[:, None, 0] - c[None, :, 0]) ** 2) + ((pts[:, None, 1] - c[None, :, 1]) ** 2)
        )
        min_dist = np.minimum(min_dist, d.min(axis=1))
    flagged = min_dist > threshold
    print(
        f"[anomaly] live flag rate: {flagged.sum()}/{len(pts)} "
        f"({flagged.sum()/len(pts):.1%}) -- trained-on flag rate was 3.0%"
    )
    if flagged.sum() > 0:
        worst_idx = np.argmax(min_dist)
        worst = df.iloc[worst_idx]
        print(
            f"[anomaly] furthest-from-any-corridor example: "
            f"{worst.get('callsign','?')} at ({worst['lat']:.2f},{worst['lon']:.2f}), "
            f"{min_dist[worst_idx]:.2f}deg from nearest corridor centroid"
        )

    # =====================================================================
    # Model 4: Traffic forecast -- pull recent hourly stats, predict next hour
    # =====================================================================
    print("\n=== Validating Model 4: Traffic forecast ===")
    try:
        r = httpx.get(f"{API_BASE}/api/stats/overview", timeout=15)
        r.raise_for_status()
        overview = r.json()
        print(f"[forecast] current live overview: {json.dumps(overview, indent=2)[:400]}")
    except Exception as e:
        print(f"[forecast] couldn't fetch /api/stats/overview: {e}")

    import os

    if os.path.exists(f"{ARTIFACT_DIR}/forecast_gbr.joblib"):
        forecast_model = joblib.load(f"{ARTIFACT_DIR}/forecast_gbr.joblib")
        try:
            # Fixed: use the REAL hourly-aggregate history the live stack
            # already maintains (stats/hourly.json), not the previous
            # instantaneous-count proxy -- that was comparing apples to
            # oranges (per-hour distinct-aircraft count vs a single-moment
            # snapshot count).
            hr = httpx.get(f"{API_BASE}/api/stats/traffic-by-hour", timeout=15)
            hr.raise_for_status()
            hourly_hist = pd.DataFrame(hr.json()["points"])
            hourly_hist["hour"] = pd.to_datetime(hourly_hist["hour_bucket"], utc=True)
            hourly_hist = hourly_hist.sort_values("hour")
            print(
                f"[forecast] fetched {len(hourly_hist)} real hourly buckets from "
                "/api/stats/traffic-by-hour"
            )

            now = hourly_hist["hour"].iloc[-1] + pd.Timedelta(hours=1)
            lag1 = hourly_hist["flight_count"].iloc[-1]
            lag24 = (
                hourly_hist["flight_count"].iloc[-24]
                if len(hourly_hist) >= 24
                else hourly_hist["flight_count"].mean()
            )
            Xf = np.array([[now.hour, now.dayofweek, lag1, lag24]])
            pred = forecast_model.predict(Xf)
            print(
                f"[forecast] predicted next hour ({now}): {pred[0]:.0f} flights "
                f"(lag1={lag1}, lag24={lag24:.0f}, recent actual mean="
                f"{hourly_hist['flight_count'].tail(6).mean():.0f})"
            )
        except Exception as e:
            print(f"[forecast] couldn't fetch real hourly history, falling back skipped: {e}")
    else:
        print("[forecast] model artifact not found, skipped")

    print("\n[done] live validation complete")


if __name__ == "__main__":
    main()
