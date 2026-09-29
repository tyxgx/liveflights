"""Build sliding-window training data for a sequence (GRU) trajectory model.

For every aircraft, the last HIST (=10) one-minute readings are the INPUT and
the positions 1..HORIZON (=5) minutes later are the TARGET. Read-only on the
bronze backup; writes compressed numpy files to data/gru_windows/ (gitignored).

Design choices (all deliberate):
  * Tracks are split wherever two consecutive readings of one aircraft are not
    ~60 s apart (GAP_MIN_S..GAP_MAX_S), so every window is evenly spaced.
  * Split by DAY, never randomly: test = last 2 days (same holdout days as
    train_all.py), val = the 2 days before that, train = everything earlier.
  * Windows overlap heavily (one per minute per plane), so we keep every
    STRIDE-th candidate and then randomly sample PER_DAY windows per day.
  * Positions are stored RELATIVE to the last observed position (degrees),
    the last absolute lat/lon is kept separately as `ctx`.
  * Windows containing an impossible jump (> MAX_STEP_DEG per minute) are dropped.

Usage (from the repo root):
    python3 ml/scratch/build_gru_windows.py --smoke   # quick sanity run
    python3 ml/scratch/build_gru_windows.py           # full build
"""

from __future__ import annotations

import argparse
import glob
import os
import time

import duckdb
import numpy as np

BRONZE_ROOT = "data/s3-backup-2026-09-19/bronze"
OUT_DIR = "data/gru_windows"

TEST_DAYS = {"2026-09-19", "2026-09-20"}
VAL_DAYS = {"2026-09-17", "2026-09-18"}
SMOKE_DAYS = {"2026-09-15", "2026-09-16", "2026-09-17", "2026-09-20"}
MIN_FILES_PER_DAY = 100

HIST = 10  # readings fed to the model (10 minutes of history)
HORIZON = 5  # minutes ahead to predict (targets at +1..+5 min)
STRIDE = 5  # keep every 5th candidate window before sampling
GAP_MIN_S = 45
GAP_MAX_S = 75
MAX_STEP_DEG = 0.5  # >0.5 deg/min (~55 km/min) is not a real aircraft
PER_DAY = {"train": 30_000, "val": 30_000, "test": 100_000}
SMOKE_PER_DAY = 5_000
SEED = 42
EARTH_RADIUS_KM = 6371.0

# Data-quality decisions (from run_logs data_quality audit, 2026-09-25):
#  * only real adsb_lol rows (the Lambda's simulator fallback is labelled simulate_cloud);
#  * time axis = the aircraft's own position time (time_position), NOT ingest_ts, which is
#    the Lambda start time and can be off by ~1-2 s (median) and up to ~30 s for stale fixes;
#  * Europe bounding box, because stray fixes exist (e.g. lon -74 on 2026-08-27, India rows
#    from before the Aug 2026 region switch).
RAW_SQL = """
CREATE OR REPLACE TEMP TABLE raw AS
SELECT icao24, longitude AS lon, latitude AS lat, velocity AS vel,
       true_track AS hd, baro_altitude AS alt,
       COALESCE(vertical_rate, 0) AS vr, (vertical_rate IS NULL) AS vr_null,
       make_timestamp(CAST(time_position AS BIGINT) * 1000000) AS ts
FROM read_json_auto(?, format='newline_delimited', union_by_name=true)
WHERE source = 'adsb_lol' AND time_position IS NOT NULL
  AND on_ground = false AND longitude IS NOT NULL AND latitude IS NOT NULL
  AND latitude BETWEEN 35 AND 64 AND longitude BETWEEN -13 AND 33
  AND velocity BETWEEN 0 AND 420 AND baro_altitude BETWEEN 0 AND 15550
  AND true_track IS NOT NULL
"""

TRACK_SQL = f"""
CREATE OR REPLACE TEMP TABLE t AS
WITH g AS (
  SELECT *, epoch(ts) - epoch(lag(ts) OVER (PARTITION BY icao24 ORDER BY ts)) AS gap
  FROM raw
), r AS (
  SELECT *, sum(CASE WHEN gap IS NULL OR gap < {GAP_MIN_S} OR gap > {GAP_MAX_S}
                     THEN 1 ELSE 0 END)
            OVER (PARTITION BY icao24 ORDER BY ts ROWS UNBOUNDED PRECEDING) AS rid
  FROM g
)
SELECT *, row_number() OVER (PARTITION BY icao24, rid ORDER BY ts) AS rn,
          count(*) OVER (PARTITION BY icao24, rid) AS cnt
FROM r
"""


def split_of(day: str) -> str:
    """Which split a day belongs to (by date, never random)."""
    if day in TEST_DAYS:
        return "test"
    if day in VAL_DAYS:
        return "val"
    return "train"


def load_day(con: duckdb.DuckDBPyConnection, files: list[str]) -> float:
    """Load one day of bronze into temp table `t` (tracks with run ids).

    Returns the share of readings whose vertical_rate was NULL (filled with 0).
    """
    con.execute(RAW_SQL, [files])
    vr_null = con.execute("SELECT avg(vr_null::INT) FROM raw").fetchone()[0]
    con.execute(TRACK_SQL)
    return float(vr_null or 0.0)


def sample_keys(con: duckdb.DuckDBPyConnection, n_target: int) -> tuple[int, int]:
    """Pick which windows to build. Returns (n_candidates, n_sampled)."""
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE cand AS
        SELECT icao24, rid, rn FROM t
        WHERE rn >= {HIST} AND rn + {HORIZON} <= cnt AND rn % {STRIDE} = 0"""
    )
    n_cand = con.execute("SELECT count(*) FROM cand").fetchone()[0]
    pct = min(100.0, 100.0 * n_target / max(n_cand, 1))
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE keys AS
        SELECT row_number() OVER () AS wid, icao24, rid, rn
        FROM cand USING SAMPLE {pct} PERCENT (bernoulli, {SEED})"""
    )
    n_keys = con.execute("SELECT count(*) FROM keys").fetchone()[0]
    return n_cand, n_keys


def fetch_windows(con: duckdb.DuckDBPyConnection) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pull all sampled windows. Returns (vals, ts_last, icao24_last).

    vals has shape (n, HIST+HORIZON, 6) with columns lat, lon, vel, hd, alt, vr.
    """
    steps = HIST + HORIZON
    df = con.execute(
        f"""
        SELECT k.wid, o.off, t.icao24, epoch(t.ts) AS ts,
               t.lat, t.lon, t.vel, t.hd, t.alt, t.vr
        FROM keys k
        CROSS JOIN (SELECT unnest(range({1 - HIST}, {HORIZON + 1})) AS off) o
        JOIN t ON t.icao24 = k.icao24 AND t.rid = k.rid AND t.rn = k.rn + o.off
        ORDER BY k.wid, o.off"""
    ).fetchdf()
    n = len(df) // steps
    if n == 0:
        return np.empty((0, steps, 6)), np.empty(0), np.empty(0, dtype=str)
    if n * steps != len(df):
        raise RuntimeError("incomplete windows returned by the join")
    wid = df["wid"].to_numpy().reshape(n, steps)
    if not (wid == wid[:, :1]).all():
        raise RuntimeError("window rows are not grouped correctly")
    cols = ["lat", "lon", "vel", "hd", "alt", "vr"]
    vals = df[cols].to_numpy(dtype=np.float64).reshape(n, steps, len(cols))
    ts_last = df["ts"].to_numpy().reshape(n, steps)[:, HIST - 1]
    icao = df["icao24"].to_numpy().reshape(n, steps)[:, HIST - 1]
    return vals, ts_last, icao


def _wrap_deg(d: np.ndarray) -> np.ndarray:
    """Wrap a longitude difference into [-180, 180)."""
    return (d + 180.0) % 360.0 - 180.0


def to_features(vals: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Turn raw windows into (X, Y, ctx, keep).

    X   (n, HIST, 7): d_lat, d_lon (vs last reading), velocity, sin(track),
                      cos(track), vertical_rate, altitude
    Y   (n, HORIZON, 2): d_lat, d_lon of the future positions vs the last reading
    ctx (n, 2): last absolute lat, lon
    keep (n,): False for windows containing an impossible jump
    """
    hist, fut = vals[:, :HIST], vals[:, HIST:]
    last_lat, last_lon = hist[:, -1, 0], hist[:, -1, 1]
    h_dlat = hist[:, :, 0] - last_lat[:, None]
    h_dlon = _wrap_deg(hist[:, :, 1] - last_lon[:, None])
    f_dlat = fut[:, :, 0] - last_lat[:, None]
    f_dlon = _wrap_deg(fut[:, :, 1] - last_lon[:, None])

    step_lat = np.abs(np.diff(vals[:, :, 0], axis=1)).max(axis=1)
    step_lon = np.abs(_wrap_deg(np.diff(vals[:, :, 1], axis=1))).max(axis=1)
    keep = np.maximum(step_lat, step_lon) <= MAX_STEP_DEG

    hd = np.radians(hist[:, :, 3])
    x = np.stack(
        [h_dlat, h_dlon, hist[:, :, 2], np.sin(hd), np.cos(hd), hist[:, :, 5], hist[:, :, 4]],
        axis=-1,
    ).astype(np.float32)
    y = np.stack([f_dlat, f_dlon], axis=-1).astype(np.float32)
    ctx = np.stack([last_lat, last_lon], axis=-1).astype(np.float32)
    return x, y, ctx, keep


def dead_reckoning_error_km(x: np.ndarray, y: np.ndarray, ctx: np.ndarray) -> np.ndarray:
    """Error (km) of a straight-line physics guess at +HORIZON minutes.

    Used only as a sanity check that the windows are built correctly (it must be
    a plausible number, i.e. neither ~0 nor hundreds of km). It is also the
    baseline any trajectory model has to beat. The project docs' ~9 km median
    came from simulator data; on real ADS-B it is much smaller (~1.5-2 km).
    """
    lat1 = np.radians(ctx[:, 0].astype(np.float64))
    lon1 = np.radians(ctx[:, 1].astype(np.float64))
    bearing = np.arctan2(x[:, -1, 3], x[:, -1, 4])
    ang = x[:, -1, 2].astype(np.float64) * HORIZON * 60.0 / 1000.0 / EARTH_RADIUS_KM
    lat2 = np.arcsin(np.sin(lat1) * np.cos(ang) + np.cos(lat1) * np.sin(ang) * np.cos(bearing))
    lon2 = lon1 + np.arctan2(
        np.sin(bearing) * np.sin(ang) * np.cos(lat1), np.cos(ang) - np.sin(lat1) * np.sin(lat2)
    )
    true_lat = np.radians(ctx[:, 0].astype(np.float64) + y[:, -1, 0])
    true_lon = np.radians(ctx[:, 1].astype(np.float64) + y[:, -1, 1])
    a = (
        np.sin((true_lat - lat2) / 2) ** 2
        + np.cos(lat2) * np.cos(true_lat) * np.sin((true_lon - lon2) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def main() -> None:
    """Build train/val/test window files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="4 days, small samples")
    smoke = parser.parse_args().smoke

    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")

    names = ["X", "Y", "ctx", "ts_last", "icao24", "day"]
    acc: dict[str, dict[str, list[np.ndarray]]] = {
        s: {k: [] for k in names} for s in ("train", "val", "test")
    }
    t_all = time.time()
    for path in sorted(glob.glob(f"{BRONZE_ROOT}/ingest_date=*")):
        day = os.path.basename(path).split("=")[1]
        files = glob.glob(f"{path}/*/*.gz")
        if len(files) < MIN_FILES_PER_DAY or (smoke and day not in SMOKE_DAYS):
            continue
        split = split_of(day)
        t0 = time.time()
        vr_null = load_day(con, files)
        n_cand, n_keys = sample_keys(con, SMOKE_PER_DAY if smoke else PER_DAY[split])
        vals, ts_last, icao = fetch_windows(con)
        x, y, ctx, keep = to_features(vals)
        dropped = int((~keep).sum())
        for k, v in zip(names, (x, y, ctx, ts_last.astype(np.int64), icao.astype(str),
                                np.full(len(x), day)), strict=True):
            acc[split][k].append(v[keep])
        print(
            f"{day} [{split:5s}] candidates={n_cand:>9,} sampled={n_keys:>7,} "
            f"jump_dropped={dropped:>4} vr_null={vr_null:.1%} sec={time.time() - t0:.0f}",
            flush=True,
        )

    print("\n=== SUMMARY ===")
    for split, parts in acc.items():
        if not parts["X"]:
            print(f"{split}: no windows")
            continue
        data = {k: np.concatenate(v) for k, v in parts.items()}
        np.savez_compressed(f"{OUT_DIR}/{split}{'_smoke' if smoke else ''}.npz", **data)
        x, y, ctx = data["X"], data["Y"], data["ctx"]
        print(f"{split}: {len(x):,} windows | X{x.shape} Y{y.shape} | days={len(set(data['day']))}")
        if split in ("val", "test"):
            err = dead_reckoning_error_km(x, y, ctx)
            print(
                f"  sanity dead-reckoning error @+{HORIZON}min: median={np.median(err):.2f} km "
                f"p90={np.percentile(err, 90):.2f} km  (real data; the ~9 km in docs was simulator data)"
            )
        if split == "train":
            names_f = ["d_lat", "d_lon", "vel", "sin_trk", "cos_trk", "vrate", "alt"]
            flat = x.reshape(-1, x.shape[-1])
            for i, nm in enumerate(names_f):
                print(f"  {nm:8s} min={flat[:, i].min():>10.3f} max={flat[:, i].max():>10.3f} "
                      f"mean={flat[:, i].mean():>10.3f}")
    print(f"\nfiles in {OUT_DIR}/ | total {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
