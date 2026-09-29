"""Read-only sanity report for the Parquet files written by extract_globe_day.py.

Checks: row/aircraft counts, share of non-empty values per column (= how many rows have that
field), value ranges, grid spacing (should be exactly 10 s inside a track), a physics
self-check (distance between consecutive rows / time vs reported ground speed), and the
straight-line ("dead reckoning") error at +5 min, which on real ADS-B should be ~1.5-2 km.

Usage (from the repo root):
    python3 ml/scratch/inspect_extract.py data/globe_extract_smoke
    python3 ml/scratch/inspect_extract.py data/globe_extract
"""

from __future__ import annotations

import sys

import duckdb

HAV = """2 * 6371.0 * asin(sqrt(pow(sin(radians({lat2} - {lat1}) / 2), 2)
       + cos(radians({lat1})) * cos(radians({lat2})) * pow(sin(radians({lon2} - {lon1}) / 2), 2)))"""


def ok(c: str) -> str:
    """SQL condition: the value is neither NULL nor NaN (pandas NaN is stored as NULL)."""
    return f"({c} IS NOT NULL AND NOT isnan({c}))"


def main() -> None:
    """Print the report."""
    folder = sys.argv[1] if len(sys.argv) > 1 else "data/globe_extract"
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute(f"CREATE VIEW d AS SELECT * FROM read_parquet('{folder}/*_p*.parquet')")
    n, ac, dirs, t0, t1 = con.execute(
        "SELECT count(*), count(DISTINCT icao), count(DISTINCT dir2), min(ts), max(ts) FROM d"
    ).fetchone()
    print(f"rows={n:,} aircraft={ac:,} hex-dirs={dirs} | ts span={(t1 - t0) / 3600:.1f} h")

    cols = [r[0] for r in con.execute("DESCRIBE d").fetchall()]
    num = [c for c in cols if c not in ("icao", "dir2", "type_code", "category", "callsign")]
    print("\nshare of rows where the field is present (not NaN):")
    shares = con.execute(
        "SELECT " + ", ".join(f"avg({ok(c)}::INT)" for c in num) + " FROM d"
    ).fetchone()
    line = ""
    for c, s in zip(num, shares, strict=True):
        line += f"{c}={s:.0%}  "
        if len(line) > 110:
            print("  " + line)
            line = ""
    print("  " + line)
    ac_share = con.execute(
        "SELECT " + ", ".join(f"avg(m_{c}) " for c in ["roll_deg", "track_rate", "nav_alt_mcp_m",
                                                       "nav_heading", "wd_deg", "mach"])
        + " FROM (SELECT icao, "
        + ", ".join(f"max({ok(c)}::INT) AS m_{c}" for c in ["roll_deg", "track_rate",
                                                                   "nav_alt_mcp_m", "nav_heading",
                                                                   "wd_deg", "mach"])
        + " FROM d GROUP BY icao)"
    ).fetchone()
    print("share of AIRCRAFT that report at least once: roll="
          f"{ac_share[0]:.0%} track_rate={ac_share[1]:.0%} nav_alt={ac_share[2]:.0%} "
          f"nav_heading={ac_share[3]:.0%} wind={ac_share[4]:.0%} mach={ac_share[5]:.0%}")

    print("\nvalue ranges (min / median / max):")
    for c in ["alt_baro_m", "gs_ms", "vrate_baro_ms", "roll_deg", "track_rate", "mach", "gap_s",
              "nav_alt_mcp_m", "ws_ms", "oat_c"]:
        mn, md, mx = con.execute(
            f"SELECT min({c}), median({c}), max({c}) FROM d WHERE {ok(c)}"
        ).fetchone()
        print(f"  {c:14s} {mn:>10.2f} {md:>10.2f} {mx:>10.2f}")

    print("\ngrid spacing inside tracks (seconds between consecutive rows of one aircraft):")
    print("  ", con.execute(
        "SELECT quantile_cont(dt, 0.5), avg((dt = 10)::INT), avg((dt > 10 AND dt <= 60)::INT), "
        "avg((dt > 60)::INT) FROM (SELECT ts - lag(ts) OVER (PARTITION BY icao ORDER BY ts) AS dt "
        "FROM d) WHERE dt IS NOT NULL"
    ).fetchone(), "(median, ==10s, 10-60s gap, >60s gap)")

    h = HAV.format(lat1="plat", lon1="plon", lat2="lat", lon2="lon")
    r = con.execute(f"""
        WITH p AS (SELECT lat, lon, gs_ms, ts, lag(lat) OVER w AS plat, lag(lon) OVER w AS plon,
                          lag(gs_ms) OVER w AS pgs, lag(ts) OVER w AS pts
                   FROM d WINDOW w AS (PARTITION BY icao ORDER BY ts))
        SELECT count(*), median(({h}) * 1000 / (ts - pts) / ((gs_ms + pgs) / 2)),
               avg(((({h}) * 1000 / (ts - pts)) / ((gs_ms + pgs) / 2) BETWEEN 0.9 AND 1.1)::INT)
        FROM p WHERE pts IS NOT NULL AND ts - pts = 10 AND gs_ms > 100 AND pgs > 100""").fetchone()
    print(f"\nphysics check (10 s steps, cruise): n={r[0]:,} implied/reported speed median={r[1]:.3f} "
          f"within +-10% = {r[2]:.1%}   (S3 data gave 0.997 / 99%)")

    hh = HAV.format(lat1="dlat", lon1="dlon", lat2="b.lat", lon2="b.lon")
    r = con.execute(f"""
        WITH a AS (
          SELECT icao, ts, lat, lon, gs_ms, radians(track_deg) AS th,
                 gs_ms * 300 / 1000.0 / 6371.0 AS ang FROM d WHERE gs_ms IS NOT NULL
                 AND NOT isnan(gs_ms) AND NOT isnan(track_deg)),
        dr AS (SELECT icao, ts + 300 AS ts2, degrees(asin(sin(radians(lat)) * cos(ang)
                 + cos(radians(lat)) * sin(ang) * cos(th))) AS dlat,
               lon + degrees(atan2(sin(th) * sin(ang) * cos(radians(lat)),
                 cos(ang) - sin(radians(lat)) * sin(asin(sin(radians(lat)) * cos(ang)
                 + cos(radians(lat)) * sin(ang) * cos(th))))) AS dlon FROM a)
        SELECT count(*), median({hh}), quantile_cont({hh}, 0.9)
        FROM dr JOIN d b ON b.icao = dr.icao AND b.ts = dr.ts2""").fetchone()
    print(f"dead-reckoning error @+5 min over {r[0]:,} pairs: median={r[1]:.2f} km p90={r[2]:.2f} km "
          "(S3 windows gave median ~1.5-1.8, p90 ~14-16)")


if __name__ == "__main__":
    main()
