"""Build GRU training windows from the 10-second Parquet tracks (extract_globe_day.py output).

A window = the last HIST (10) readings, spaced 60 s apart (rows 54, 48, ... 0 rows back on the
10 s grid), as INPUT, and the positions over the next HORIZON (5) minutes as TARGET, given every
10 seconds (30 points; the minute marks are Y[:, 5::6]). Every grid row that has 54 rows of
unbroken history behind it and 30 rows ahead is a possible window end, so one aircraft
contributes windows at all six 10-second phases automatically.

Why 60 s spacing for the input but 10 s for the target: the live pipeline only sees one reading per
minute, so the model must be trained on 1-minute history. The instantaneous fields (roll,
track_rate, autopilot targets) carry the fine detail about a turn. The dense 10 s target gives
the model a much richer training signal in turns and lets the dashboard draw a smooth path.

Stored per window (float32 numpy arrays, per day, under data/gru_v2/<split>/<day>/):
  X   (n, 10, 31)  per-step features: 12 core (dx, dy km vs last position, gs, sin/cos track,
                   vertical rate, altitude, "vrate missing" flag, the per-step CHANGES
                   dtrack, dgs, dalt, and dt_min) + 10 extra values (roll, track_rate, heading-minus-track,
                   mach, tas, ias, wind_u, wind_v, autopilot-altitude minus altitude,
                   autopilot-heading minus track) + 9 masks (1 = the aircraft reported that field;
                   missing values are set to 0)
  S   (n, 10)      static: last lat/lon, hour and weekday (sin/cos), ADS-B category id, wake class
                   id, is-helicopter, is-military
  Y   (n, 30, 2)   true position at +10, +20 ... +300 s relative to the last position, km (east, north)
  meta.parquet     icao, day, ts_last, unseen (aircraft held out from training), kind (0 uniform,
                   1 hard case, 2 turning/vertical by history), error @+5 min of two physics
                   baselines (straight line, constant turn rate), gap_max_s, regime (0 level cruise,
                   1 turning, 2 climbing/descending), type_code, category, wake, is_mil
Baselines are NOT stored (they are exact functions of X): straight = gs*t along the last track;
constant turn rate = same speed, heading turning at (track_now - track_60s_ago)/60 deg per second.

Splits are by DAY (train / val / test days) and by AIRCRAFT (a fixed 20% of the 256 hex folders is
never used for train/val; on test days those aircraft are flagged `unseen`). Train days are
40% uniform + 40% hard (straight-line error @+5 min > HARD_KM) + 20% turning/vertical windows;
val/test are uniform only (the real distribution).

Usage (from the repo root):
    python3 ml/scratch/build_windows_v2.py --smoke      # 2 days, small, ~1-2 min
    python3 ml/scratch/build_windows_v2.py              # everything (Round 1 days)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from features import window_s, window_s_dest, window_x  # noqa: E402  (shared with live serving)
from route_lookup import load_route_lookup  # noqa: E402  (ml/route_lookup.py, same table predict Lambda uses)

EXTRACT_DIR = "data/globe_extract"
VRS_DIR = "data/vrs/standing-data"
HIST, HORIZON, STEP = 10, 5, 6  # 10 history steps of 6 rows (=60 s); 5 minutes ahead
H_OFF = np.arange(-(HIST - 1) * STEP, 1, STEP)  # -54 ... 0
F_OFF = np.arange(1, HORIZON * STEP + 1)  # +1 ... +30 rows (10 s each)
MIN_POS, MAX_TAIL = (HIST - 1) * STEP, HORIZON * STEP
TURN_LOOKBACK = 3 * STEP  # 3 minutes back, used for the turning regime
KM_PER_DEG = 111.32
HARD_KM = 3.0
HOLDOUT_FRAC, SEED = 0.2, 42
WIND_FROM = True  # readsb `wd` = direction the wind blows FROM (to be confirmed by validate_extras.py)
SPLITS = {
    "train": ["2026.09.01", "2026.09.03", "2026.09.05", "2026.09.07", "2026.09.09", "2026.09.11",
              "2026.09.13", "2026.09.15"],
    "val": ["2026.09.17"],
    "test": ["2026.09.20", "2026.09.23"],
}
N_PER_DAY = {"train": 150_000, "val": 100_000, "test": 250_000}
SHARES = {"train": (0.4, 0.4, 0.2), "val": (1.0, 0.0, 0.0), "test": (1.0, 0.0, 0.0)}
ROW_COLS = ["alt_baro_m", "gs_ms", "track_deg", "vrate_baro_ms", "vrate_geom_ms", "roll_deg",
            "track_rate", "true_heading", "mach", "tas_ms", "ias_ms", "wd_deg", "ws_ms",
            "nav_alt_mcp_m", "nav_heading", "gap_s"]
CORE_NAMES = ["dx_km", "dy_km", "gs_ms", "sin_trk", "cos_trk", "vrate_ms", "alt_m", "vrate_missing",
              "dtrack_deg", "dgs_ms", "dalt_m", "dt_min"]  # dt_min = minutes since the previous step
MINUTE_IDX = [5, 11, 17, 23, 29]  # positions in the 30-point target that fall on whole minutes
EXTRA_NAMES = ["roll_deg", "track_rate", "hdg_minus_trk", "mach", "tas_ms", "ias_ms", "wind_u",
               "wind_v", "navalt_minus_alt", "navhdg_minus_trk"]
MASK_NAMES = ["m_roll", "m_trate", "m_hdg", "m_mach", "m_tas", "m_ias", "m_wind", "m_navalt",
              "m_navhdg"]
S_NAMES = ["lat", "lon", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "category_id", "wake_id",
           "is_heli", "is_mil"]


def wrap180(a: np.ndarray) -> np.ndarray:
    """Wrap an angle difference into [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0


def load_vrs() -> tuple[dict[str, str], list[tuple[int, int]]]:
    """type code -> wake class letter, and the military ICAO address ranges (from VRS data)."""
    wake: dict[str, str] = {}
    for f in glob.glob(f"{VRS_DIR}/model-type/schema-01/*.csv"):
        df = pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        for code, w in zip(df["ICAO"], df["WakeTurbulenceCode"], strict=True):
            if code:
                wake[code] = w
    cb = pd.read_csv(f"{VRS_DIR}/code-blocks/schema-01/code-blocks.csv", dtype=str,
                     encoding="utf-8-sig", keep_default_na=False)
    mil = [(int(s, 16), int(e, 16)) for s, e, m in
           zip(cb["Start"], cb["Finish"], cb["IsMilitary"], strict=True) if m == "1"]
    return wake, mil


def load_dest_table() -> dict[str, tuple[float, float]]:
    """callsign -> (dest_lat, dest_lon), the destination end of route_lookup.load_route_lookup()
    (ml/route_lookup.py - the SAME table the predict Lambda loads for its display-only route info,
    reused here for the ML bearing/distance feature so this data has one source, not two)."""
    routes = load_route_lookup(f"{VRS_DIR}/..")
    return {cs: (r["destination"]["lat"], r["destination"]["lon"]) for cs, r in routes.items()}


def category_id(cat: str) -> int:
    """ADS-B emitter category 'A3' -> 3, 'B2' -> 10, 'C1' -> 17, unknown -> 24."""
    if len(cat) == 2 and cat[0] in "ABC" and cat[1].isdigit():
        return "ABC".index(cat[0]) * 8 + int(cat[1])
    return 24


def static_tables(ac: pd.DataFrame, holdout: set[str], wake: dict, mil: list) -> dict:
    """Per-aircraft static arrays (indexed by aircraft id = row order of `ac`)."""
    wake_map = {"L": 0, "M": 1, "H": 2, "J": 3}
    icao = ac["icao"].to_numpy()
    cat = ac["category"].fillna("").to_numpy()
    typ = ac["type_code"].fillna("").to_numpy()

    def is_mil(h: str) -> int:
        try:
            v = int(h, 16)
        except ValueError:
            return 0
        return int(any(s <= v <= e for s, e in mil))

    return {
        "icao": icao, "type": typ, "cat": cat,
        "cat_id": np.array([category_id(c) for c in cat], np.float32),
        "wake_id": np.array([wake_map.get(wake.get(t, ""), -1) for t in typ], np.float32),
        "wake": np.array([wake.get(t, "") for t in typ], dtype=object),
        "is_heli": np.array([1.0 if c == "A7" else 0.0 for c in cat], np.float32),
        "is_mil": np.array([is_mil(h) for h in icao], np.float32),
        "unseen": np.array([d in holdout for d in ac["dir2"]], bool),
    }


def load_day(con: duckdb.DuckDBPyConnection, day: str, exclude: list[str]):
    """Load one day (sorted by aircraft, time) as numpy arrays + the per-aircraft table."""
    files = sorted(glob.glob(f"{EXTRACT_DIR}/{day.replace('.', '-')}_p*.parquet"))
    if not files:
        return None, None
    where = f"WHERE dir2 NOT IN ({','.join(repr(d) for d in exclude)})" if exclude else ""
    con.execute(f"CREATE OR REPLACE TEMP TABLE d AS SELECT * FROM read_parquet({files!r}) {where}")
    ac = con.execute("SELECT icao, any_value(dir2) AS dir2, any_value(type_code) AS type_code, "
                     "any_value(category) AS category FROM d GROUP BY icao ORDER BY icao").fetchdf()
    f32 = ", ".join(f"COALESCE(CAST({c} AS FLOAT), CAST('nan' AS FLOAT)) AS {c}" for c in ROW_COLS)
    arr = con.execute(
        "SELECT dense_rank() OVER (ORDER BY icao) - 1 AS aid, CAST(ts AS DOUBLE) AS ts, "
        f"CAST(lat AS DOUBLE) AS lat, CAST(lon AS DOUBLE) AS lon, callsign, {f32} FROM d "
        "ORDER BY icao, ts"
    ).fetchnumpy()
    return arr, ac


def candidates(arr: dict) -> np.ndarray:
    """Row indices that can be the LAST history reading of a window."""
    aid, ts = arr["aid"], arr["ts"]
    n = len(aid)
    start_flag = ~np.r_[False, (aid[1:] == aid[:-1]) & (np.abs(np.diff(ts) - 10.0) < 1e-3)]
    starts = np.nonzero(start_flag)[0]
    seg_id = np.cumsum(start_flag) - 1
    seg_len = np.diff(np.r_[starts, n])
    pos = np.arange(n) - starts[seg_id]
    ok = (pos >= MIN_POS) & (pos <= seg_len[seg_id] - 1 - MAX_TAIL)
    # only windows that START inside the serving area (the extract has a 3-degree margin for the futures)
    ok &= (arr["lat"] >= 35.0) & (arr["lat"] <= 64.0) & (arr["lon"] >= -13.0) & (arr["lon"] <= 33.0)
    return np.nonzero(ok)[0]


def straight_err5(arr: dict, e: np.ndarray) -> np.ndarray:
    """Error (km) of the straight-line guess at +5 min for windows ending at rows e."""
    lat_i, lon_i = arr["lat"][e], arr["lon"][e]
    j = e + MAX_TAIL
    dy = (arr["lat"][j] - lat_i) * KM_PER_DEG
    dx = (arr["lon"][j] - lon_i) * KM_PER_DEG * np.cos(np.radians(lat_i))
    tr = np.radians(arr["track_deg"][e])
    gs = arr["gs_ms"][e]
    return np.hypot(dx - gs * 300 * np.sin(tr) / 1000, dy - gs * 300 * np.cos(tr) / 1000)


def cand_regime(arr: dict, e: np.ndarray) -> np.ndarray:
    """0 level cruise, 1 turning (track changed > 8 deg in 3 min), 2 climbing/descending."""
    trk = arr["track_deg"]
    vr = np.where(np.isnan(arr["vrate_baro_ms"][e]), arr["vrate_geom_ms"][e], arr["vrate_baro_ms"][e])
    turn = np.abs(wrap180(trk[e] - trk[e - TURN_LOOKBACK])) > 8
    return np.where(turn, 1, np.where(np.abs(np.nan_to_num(vr)) > 2.5, 2, 0)).astype(np.int8)


def choose(cand: np.ndarray, err5: np.ndarray, regime: np.ndarray, n_total: int,
           shares: tuple[float, float, float], rng: np.random.Generator):
    """Pick window ends: uniform / hard-case / turning-or-vertical parts (no duplicates)."""
    ok = ~np.isnan(err5)
    cand, err5, regime = cand[ok], err5[ok], regime[ok]
    pools = [np.arange(len(cand)), np.nonzero(err5 > HARD_KM)[0], np.nonzero(regime > 0)[0]]
    taken = np.zeros(len(cand), bool)
    picks, kinds = [], []
    for kind, (share, pool) in enumerate(zip(shares, pools, strict=True)):
        n = int(n_total * share)
        pool = pool[~taken[pool]]
        if n == 0 or len(pool) == 0:
            continue
        sel = rng.choice(pool, size=min(n, len(pool)), replace=False)
        taken[sel] = True
        picks.append(sel)
        kinds.append(np.full(len(sel), kind, np.int8))
    return (cand[np.concatenate(picks)], np.concatenate(kinds), float((err5 > HARD_KM).mean()),
            float((regime > 0).mean()))


def build(arr: dict, e: np.ndarray, kind: np.ndarray, st: dict, day: str, dest: dict | None = None):
    """Gather features/targets for the chosen window ends; returns arrays + meta DataFrame.

    `dest`: callsign -> (lat, lon) table from load_dest_table(); when given, S gets 4 extra
    destination columns (schema v2, S width 14) via window_s_dest(); when None, the old S (width
    10) is produced via window_s(), unchanged from before this feature existed.
    """
    ih, ifu = e[:, None] + H_OFF[None, :], e[:, None] + F_OFF[None, :]
    lat_i, lon_i = arr["lat"][e], arr["lon"][e]
    coslat = np.cos(np.radians(lat_i))[:, None]

    def g(name: str) -> np.ndarray:
        return arr[name][ih]

    gs, trk_deg, alt = g("gs_ms"), g("track_deg"), g("alt_baro_m")
    vr = np.where(np.isnan(g("vrate_baro_ms")), g("vrate_geom_ms"), g("vrate_baro_ms"))
    # the SAME function the live predict Lambda uses (ml/features.py): no train/serve skew
    x = window_x(arr["lat"][ih], arr["lon"][ih], gs, trk_deg, alt, g("vrate_baro_ms"), g("vrate_geom_ms"),
                 g("roll_deg"), g("track_rate"), g("mach"), g("tas_ms"), g("ias_ms"), g("true_heading"),
                 g("wd_deg"), g("ws_ms"), g("nav_alt_mcp_m"), g("nav_heading"))
    ts_last = arr["ts"][e]
    aid = arr["aid"][e].astype(int)
    if dest is None:
        s = window_s(lat_i, lon_i, ts_last, st["cat_id"][aid], st["wake_id"][aid], st["is_heli"][aid],
                     st["is_mil"][aid])
    else:
        cs = arr["callsign"][e]
        latlon = [dest.get(c) for c in cs]  # (lat, lon) or None, per window
        dlat = np.array([v[0] if v else np.nan for v in latlon])
        dlon = np.array([v[1] if v else np.nan for v in latlon])
        s = window_s_dest(lat_i, lon_i, ts_last, st["cat_id"][aid], st["wake_id"][aid],
                          st["is_heli"][aid], st["is_mil"][aid], dlat, dlon,
                          trk_deg, g("vrate_baro_ms"), g("vrate_geom_ms"))
    yy = (arr["lat"][ifu] - lat_i[:, None]) * KM_PER_DEG
    yx = (arr["lon"][ifu] - lon_i[:, None]) * KM_PER_DEG * coslat
    y = np.stack([yx, yy], -1).astype(np.float32)  # (n, 30, 2)
    # heading and speed at the whole-minute marks: available in live (60 s) data too, and used to
    # draw a smooth (Hermite) predicted path on the dashboard
    fut_trk = np.radians(arr["track_deg"][ifu][:, MINUTE_IDX])
    yh = np.stack([np.sin(fut_trk), np.cos(fut_trk)], -1).astype(np.float32)  # (n, 5, 2)
    yv = arr["gs_ms"][ifu][:, MINUTE_IDX].astype(np.float32)  # (n, 5)

    # physics baselines at +5 min (used for meta / sampling only; exact functions of X)
    gs_i, trk_i = gs[:, -1], np.radians(trk_deg[:, -1])
    err_straight = np.hypot(y[:, -1, 0] - gs_i * 300 * np.sin(trk_i) / 1000,
                            y[:, -1, 1] - gs_i * 300 * np.cos(trk_i) / 1000)
    omega = wrap180(trk_deg[:, -1] - trk_deg[:, -2]) / 60.0  # deg/s from the last 60 s
    t10 = np.arange(1, HORIZON * STEP + 1)[None, :] * 10.0
    hh = np.radians(trk_deg[:, -1:] + omega[:, None] * (t10 - 5.0))  # heading at mid-step
    cx = np.cumsum(gs_i[:, None] * np.sin(hh) * 10.0 / 1000, axis=1)[:, -1]
    cy = np.cumsum(gs_i[:, None] * np.cos(hh) * 10.0 / 1000, axis=1)[:, -1]
    err_ctr = np.hypot(y[:, -1, 0] - cx, y[:, -1, 1] - cy)

    bad = (np.isnan(gs).any(1) | np.isnan(trk_deg).any(1) | np.isnan(alt).any(1)
           | ~np.isfinite(y).all((1, 2)) | (np.abs(y).max((1, 2)) > 300)
           | ~np.isfinite(yh).all((1, 2)) | ~np.isfinite(yv).all(1))
    keep = ~bad
    regime = np.where(np.abs(wrap180(trk_deg[:, -1] - trk_deg[:, -4])) > 8, 1,
                      np.where(np.abs(np.nan_to_num(vr[:, -1])) > 2.5, 2, 0)).astype(np.int8)
    gap_max = np.fmax(np.nanmax(arr["gap_s"][ih], 1), np.nanmax(arr["gap_s"][ifu], 1))
    meta = pd.DataFrame({
        "icao": st["icao"][aid], "day": day, "ts_last": ts_last, "unseen": st["unseen"][aid],
        "kind": kind, "err5_straight_km": err_straight.astype(np.float32),
        "err5_ctr_km": err_ctr.astype(np.float32), "gap_max_s": gap_max, "regime": regime,
        "type_code": st["type"][aid], "category": st["cat"][aid], "wake": st["wake"][aid],
        "is_mil": st["is_mil"][aid].astype(np.int8)})
    return (x[keep], s[keep], y[keep], yh[keep], yv[keep], meta[keep].reset_index(drop=True),
            int(bad.sum()))


def summarize(out: str) -> None:
    """Print counts and physics-error statistics per split from the saved meta files."""
    print("\n=== SUMMARY ===")
    names = {0: "level", 1: "turning", 2: "climb/desc"}
    for split in SPLITS:
        parts = glob.glob(f"{out}/{split}/*/meta.parquet")
        if not parts:
            continue
        m = pd.concat([pd.read_parquet(p) for p in parts])
        u = m[m["kind"] == 0]
        dest_note = f", destination known {m['m_dest'].mean():.1%}" if "m_dest" in m else ""
        print(f"\n{split}: {len(m):,} windows ({len(u):,} uniform), {m['icao'].nunique():,} aircraft, "
              f"{m['day'].nunique()} days, unseen-aircraft share {m['unseen'].mean():.1%}, "
              f"kinds {m['kind'].value_counts().sort_index().to_dict()}{dest_note}")
        for col, nm in (("err5_straight_km", "straight-line"), ("err5_ctr_km", "const-turn-rate")):
            e = u[col]
            print(f"  {nm:15s} error @+5min (uniform windows): median={e.median():.2f} km "
                  f"p90={e.quantile(.9):.2f} p99={e.quantile(.99):.1f}")
        for r, nm in names.items():
            sub = u[u["regime"] == r]
            if len(sub):
                print(f"    {nm:11s}: {len(sub) / len(u):5.1%} of windows | straight median="
                      f"{sub['err5_straight_km'].median():.2f} p90={sub['err5_straight_km'].quantile(.9):.2f}"
                      f" | const-turn median={sub['err5_ctr_km'].median():.2f} "
                      f"p90={sub['err5_ctr_km'].quantile(.9):.2f}")
    tot = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
    print(f"\ndataset size on disk: {tot / 1e9:.2f} GB in {out}/")


def main() -> None:
    """CLI."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="2 days, 20k windows each (quick test)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--extract-dir", default=EXTRACT_DIR, help="folder with the day parquet files")
    ap.add_argument("--dest", action="store_true",
                    help="add 4 destination-route columns to S (schema v2, S width 14); "
                         "needs data/vrs/routes.csv and airports.csv")
    a = ap.parse_args()
    globals()["EXTRACT_DIR"] = a.extract_dir
    out = a.out or ("data/gru_v2" + ("_smoke" if a.smoke else "") + ("_dest" if a.dest else ""))
    dest = load_dest_table() if a.dest else None
    if dest is not None:
        print(f"destination table: {len(dest):,} callsigns with a known route+airport", flush=True)
    splits = {"train": ["2026.09.15"], "val": [], "test": ["2026.09.23"]} if a.smoke else SPLITS
    n_day = {k: (20_000 if a.smoke else v) for k, v in N_PER_DAY.items()}
    rng0 = np.random.default_rng(SEED)
    holdout = sorted(rng0.choice([f"{i:02x}" for i in range(256)],
                                 size=int(256 * HOLDOUT_FRAC), replace=False).tolist())
    wake, mil = load_vrs()
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")
    t_all = time.time()
    for split, days in splits.items():
        for day in days:
            t0 = time.time()
            arr, ac = load_day(con, day, holdout if split != "test" else [])
            if arr is None:
                print(f"[{split}] {day}: no parquet files, skipped", flush=True)
                continue
            st = static_tables(ac, set(holdout), wake, mil)
            cand = candidates(arr)
            err5 = straight_err5(arr, cand)
            reg = cand_regime(arr, cand)
            rng = np.random.default_rng(SEED + int(day.replace(".", "")))
            ends, kind, hard, turn = choose(cand, err5, reg, n_day[split], SHARES[split], rng)
            x, s, y, yh, yv, meta, bad = build(arr, ends, kind, st, day, dest)
            if dest is not None:
                meta["m_dest"] = s[:, -1]  # coverage: 1 = a destination was found for this window
            d = f"{out}/{split}/{day.replace('.', '-')}"
            os.makedirs(d, exist_ok=True)
            for name, v in (("X", x), ("S", s), ("Y", y), ("YH", yh), ("YV", yv)):
                np.save(f"{d}/{name}.npy", v)
            meta.to_parquet(f"{d}/meta.parquet", index=False)
            print(f"[{split:5s}] {day}: rows={len(arr['aid']):,} aircraft={len(ac):,} "
                  f"candidates={len(cand):,} hard>{HARD_KM:.0f}km={hard:.1%} turning/vert={turn:.1%} "
                  f"-> windows={len(x):,} (dropped {bad}) X{x.shape} Y{y.shape} | "
                  f"{time.time() - t0:.0f}s", flush=True)
    with open(f"{out}/dataset.json", "w") as f:
        json.dump({"X_names": CORE_NAMES + EXTRA_NAMES + MASK_NAMES,
                   "S_names": (S_NAMES + ["dest_dist_norm", "dest_sin", "dest_cos", "m_dest"]
                              if dest is not None else S_NAMES), "features_version": 2 if dest else 1,
                   "hist_steps": HIST, "history_spacing_s": 60, "target_points": HORIZON * STEP,
                   "target_spacing_s": 10, "minute_marks_in_Y": [5, 11, 17, 23, 29],
                   "holdout_dir2": holdout, "hard_km": HARD_KM, "splits": splits,
                   "n_per_day": n_day, "shares_uniform_hard_turning": SHARES,
                   "wind_from_convention": WIND_FROM,
                   "units": "dx/dy/Y in km (east, north) relative to the last position"},
                  f, indent=1)
    summarize(out)
    print(f"\nALL DONE in {(time.time() - t_all) / 60:.1f} min")


if __name__ == "__main__":
    main()
