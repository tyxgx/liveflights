"""Check that the EXTRA fields mean what we think they mean (signs, units, consistency).

Before a neural network is trained on roll, track_rate, wind, autopilot targets ... we verify each of
them against something independent that is also in the data:

  1. track_rate  vs the real change of track over the next 10 s  (sign + scale)
  2. roll        vs the real change of track, and vs the physics of a banked turn  (sign + scale)
  3. wind (wd, ws) vs the wind triangle  (ground velocity - air velocity)  -> which direction
     convention readsb uses (blowing FROM or TO), and whether the speed units are right
  4. mach, tas, oat  vs the speed of sound
  5. ias/tas  by altitude  (must fall with altitude)
  6. nav_alt_mcp (autopilot altitude) vs the vertical rate  (does the aircraft go towards it?)
  7. nav_heading (autopilot heading) vs the turn direction
  8. vrate_baro vs the real altitude change; geometric-minus-barometric altitude

Usage (from the repo root):
    python3 ml/scratch/validate_extras.py                       # uses 2 days
    python3 ml/scratch/validate_extras.py 2026-09-05 2026-09-13
"""

from __future__ import annotations

import glob
import os
import sys

import duckdb

DAYS = sys.argv[1:] or ["2026-09-05", "2026-09-13"]
EXTRACT_DIR = os.environ.get("EXTRACT_DIR", "data/globe_extract")  # e.g. EXTRACT_DIR=data/globe_extract_v4


def show(title: str, cols: list[str], row: tuple, hint: str) -> None:
    """Print one check."""
    print(f"\n--- {title}")
    print("    " + " | ".join(f"{c}={v:.3f}" if isinstance(v, float) else f"{c}={v}"
                              for c, v in zip(cols, row, strict=True)))
    print(f"    expected: {hint}")


def main() -> None:
    """Run all checks."""
    files = [f for d in DAYS for f in sorted(glob.glob(f"{EXTRACT_DIR}/{d}_p*.parquet"))]
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")
    have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({files!r})").fetchall()}
    age = "trate_age_s" if "trate_age_s" in have else "CAST(NULL AS FLOAT) AS trate_age_s"  # schema 5+
    con.execute(f"CREATE TEMP TABLE d AS SELECT icao, ts, alt_baro_m, alt_geom_m, gs_ms, track_deg, "
                f"vrate_baro_ms, vrate_geom_ms, roll_deg, track_rate, true_heading, mach, tas_ms, "
                f"ias_ms, oat_c, wd_deg, ws_ms, nav_alt_mcp_m, nav_heading, {age} FROM read_parquet({files!r})")
    n = con.execute("SELECT count(*) FROM d").fetchone()[0]
    print(f"days {DAYS}: {n:,} rows")
    con.execute(
        "CREATE TEMP TABLE t AS SELECT *, lead(ts) OVER w - ts AS dts, "
        "fmod(lead(track_deg) OVER w - track_deg + 540.0, 360.0) - 180.0 AS dtrk, "
        "lead(alt_baro_m) OVER w - alt_baro_m AS dalt FROM d "
        "WINDOW w AS (PARTITION BY icao ORDER BY ts)")

    show("1. track_rate (deg/s) vs real track change per second (10 s later)",
         ["n", "corr", "slope", "sign_agree"],
         con.execute("SELECT count(*), corr(track_rate, dtrk/10.0), regr_slope(dtrk/10.0, track_rate), "
                     "avg((sign(track_rate) = sign(dtrk))::INT) FROM t WHERE dts = 10 AND "
                     "track_rate IS NOT NULL AND abs(track_rate) > 0.5 AND abs(dtrk) < 90").fetchone(),
         "corr close to +1, slope close to +1, sign_agree close to 1 (positive = turning right)")

    show("1b. same, but only rows whose nearest track_rate sample is <= 10 s away (schema 5 files only)",
         ["n", "corr", "slope", "sign_agree"],
         con.execute("SELECT count(*), corr(track_rate, dtrk/10.0), regr_slope(dtrk/10.0, track_rate), "
                     "avg((sign(track_rate) = sign(dtrk))::INT) FROM t WHERE dts = 10 AND "
                     "track_rate IS NOT NULL AND trate_age_s <= 10 AND abs(track_rate) > 0.5 AND "
                     "abs(dtrk) < 90").fetchone(),
         "at least as good as check 1 (fresh samples are the real instantaneous turn rate)")

    show("2a. roll (deg) vs real track change per second",
         ["n", "corr", "sign_agree"],
         con.execute("SELECT count(*), corr(roll_deg, dtrk/10.0), "
                     "avg((sign(roll_deg) = sign(dtrk))::INT) FROM t WHERE dts = 10 AND "
                     "roll_deg IS NOT NULL AND abs(roll_deg) > 5 AND abs(dtrk) < 90").fetchone(),
         "corr clearly > 0 and sign_agree close to 1 (positive roll = right bank = right turn)")
    show("2b. roll vs physics of a banked turn: omega = g*tan(roll)/v  (compared with track_rate)",
         ["n", "corr", "slope"],
         con.execute("SELECT count(*), corr(degrees(9.81*tan(radians(roll_deg))/gs_ms), track_rate), "
                     "regr_slope(track_rate, degrees(9.81*tan(radians(roll_deg))/gs_ms)) FROM d "
                     "WHERE roll_deg IS NOT NULL AND track_rate IS NOT NULL AND gs_ms > 100 AND "
                     "abs(roll_deg) > 3").fetchone(),
         "corr close to +1 and slope near 1: roll and track_rate describe the same turn")

    w = ("SELECT gs_ms*sin(radians(track_deg)) - tas_ms*sin(radians(true_heading)) AS wx, "
         "gs_ms*cos(radians(track_deg)) - tas_ms*cos(radians(true_heading)) AS wy, ws_ms, "
         "radians(wd_deg) AS wd FROM d WHERE tas_ms IS NOT NULL AND true_heading IS NOT NULL AND "
         "ws_ms IS NOT NULL AND wd_deg IS NOT NULL AND gs_ms > 100")
    show("3. wind: the wind triangle (ground velocity - air velocity) vs reported (wd, ws)",
         ["n", "corr_x_FROM", "corr_y_FROM", "corr_x_TO", "corr_y_TO", "median_|triangle|", "median_ws"],
         con.execute(f"WITH w AS ({w}) SELECT count(*), corr(wx, -ws_ms*sin(wd)), corr(wy, -ws_ms*cos(wd)), "
                     "corr(wx, ws_ms*sin(wd)), corr(wy, ws_ms*cos(wd)), median(sqrt(wx*wx+wy*wy)), "
                     "median(ws_ms) FROM w").fetchone(),
         "one pair (FROM or TO) close to +1 (that is readsb's convention); the two medians similar (m/s)")

    show("4. mach*speed_of_sound(oat) vs tas",
         ["n", "median_tas/(mach*a)", "corr"],
         con.execute("WITH w AS (SELECT tas_ms, mach*sqrt(1.4*287.05*(oat_c+273.15)) AS a FROM d "
                     "WHERE mach > 0.3 AND tas_ms IS NOT NULL AND oat_c BETWEEN -80 AND 30) "
                     "SELECT count(*), median(tas_ms/a), corr(tas_ms, a) FROM w").fetchone(),
         "ratio close to 1.0 (tas, mach, oat are in the units we think)")

    lo, hi = con.execute(
        "SELECT median(ias_ms/tas_ms) FILTER (WHERE alt_baro_m < 2000), "
        "median(ias_ms/tas_ms) FILTER (WHERE alt_baro_m > 9000) FROM d "
        "WHERE ias_ms IS NOT NULL AND tas_ms > 50").fetchone()
    show("5. ias/tas by altitude", ["ratio_low_alt", "ratio_high_alt"], (lo, hi),
         "about 0.9-1.0 near the ground and about 0.5-0.6 at cruise")

    show("6. autopilot altitude: does the vertical rate point towards nav_alt_mcp?",
         ["n", "agree"],
         con.execute("SELECT count(*), avg((sign(nav_alt_mcp_m - alt_baro_m) = "
                     "sign(coalesce(vrate_baro_ms, vrate_geom_ms)))::INT) FROM d WHERE "
                     "nav_alt_mcp_m BETWEEN -300 AND 16000 AND abs(nav_alt_mcp_m - alt_baro_m) > 300 "
                     "AND abs(coalesce(vrate_baro_ms, vrate_geom_ms)) > 2").fetchone(),
         "agree well above 0.5 (close to 0.9): aircraft climb/descend towards the autopilot target")

    show("7. autopilot heading: is the aircraft turning towards nav_heading?",
         ["n", "agree"],
         con.execute("SELECT count(*), avg((sign(fmod(nav_heading - track_deg + 540.0, 360.0) - 180.0) "
                     "= sign(dtrk))::INT) FROM t WHERE dts = 10 AND nav_heading IS NOT NULL AND "
                     "abs(fmod(nav_heading - track_deg + 540.0, 360.0) - 180.0) > 10 AND "
                     "abs(dtrk/10.0) > 0.3 AND abs(dtrk) < 90").fetchone(),
         "agree well above 0.5")

    show("8a. vrate_baro (m/s) vs real altitude change per second",
         ["n", "corr", "slope"],
         con.execute("SELECT count(*), corr(vrate_baro_ms, dalt/10.0), regr_slope(dalt/10.0, vrate_baro_ms) "
                     "FROM t WHERE dts = 10 AND vrate_baro_ms IS NOT NULL").fetchone(),
         "corr close to +1 and slope near 1")
    show("8b. geometric minus barometric altitude (m)",
         ["n", "p10", "median", "p90"],
         con.execute("SELECT count(*), quantile_cont(alt_geom_m - alt_baro_m, 0.1), "
                     "median(alt_geom_m - alt_baro_m), quantile_cont(alt_geom_m - alt_baro_m, 0.9) "
                     "FROM d WHERE alt_geom_m IS NOT NULL").fetchone(),
         "a smooth spread of tens of metres around a small positive median")
    print("\n--- done")


if __name__ == "__main__":
    main()
