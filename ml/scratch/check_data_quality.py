"""Read-only data-quality audit of the bronze ADS-B archive (nothing is written).

Answers, per day:
  1. `source` mix: real adsb_lol rows vs simulator fallback rows (simulate_cloud).
  2. Position freshness: ingest_ts - time_position (how old was the position
     when it was polled) and how often the same position timestamp repeats.
  3. Physics self-consistency: distance between two consecutive real positions
     of one aircraft / time between them, compared with the reported ground
     speed. For genuine ADS-B this ratio should sit very close to 1.0.
  4. Geographic bounds of the real rows (Europe box expected).

Usage (from the repo root):
    python3 ml/scratch/check_data_quality.py
"""

from __future__ import annotations

import glob
import os
import time

import duckdb

BRONZE_ROOT = "data/s3-backup-2026-09-19/bronze"
MIN_FILES_PER_DAY = 100

LOAD_SQL = """
CREATE OR REPLACE TEMP TABLE d AS
SELECT source, icao24, latitude AS lat, longitude AS lon, velocity AS vel,
       on_ground AS og, time_position AS tp,
       epoch(CAST(ingest_ts AS TIMESTAMP)) AS its
FROM read_json_auto(?, format='newline_delimited', union_by_name=true)
"""

FRESH_SQL = """
SELECT count(*), avg((tp IS NULL)::INT), median(its - tp),
       quantile_cont(its - tp, 0.99), avg(((its - tp) > 30)::INT),
       min(lat), max(lat), min(lon), max(lon),
       sum((lat < 35 AND lon > 60)::INT)
FROM d
WHERE source = 'adsb_lol' AND NOT coalesce(og, false)
  AND lat IS NOT NULL AND lon IS NOT NULL
"""

PHYS_SQL = """
WITH a AS (
  SELECT icao24, lat, lon, vel, tp FROM d
  WHERE source = 'adsb_lol' AND NOT coalesce(og, false)
    AND lat IS NOT NULL AND lon IS NOT NULL AND tp IS NOT NULL
), p AS (
  SELECT lat, lon, vel, tp,
         lag(lat) OVER w AS plat, lag(lon) OVER w AS plon,
         lag(vel) OVER w AS pvel, lag(tp) OVER w AS ptp
  FROM a WINDOW w AS (PARTITION BY icao24 ORDER BY tp)
), q AS (
  SELECT *, tp - ptp AS dt,
         2 * 6371000 * asin(sqrt(
             pow(sin(radians(lat - plat) / 2), 2)
             + cos(radians(plat)) * cos(radians(lat))
               * pow(sin(radians(lon - plon) / 2), 2))) AS dist_m
  FROM p WHERE ptp IS NOT NULL
)
SELECT
  avg((dt = 0)::INT) AS same_ts_share,
  count(*) FILTER (WHERE dt BETWEEN 30 AND 90 AND vel > 100 AND pvel > 100) AS n_phys,
  median((dist_m / dt) / ((vel + pvel) / 2)) FILTER (
      WHERE dt BETWEEN 30 AND 90 AND vel > 100 AND pvel > 100) AS ratio_median,
  avg(((dist_m / dt) / ((vel + pvel) / 2) BETWEEN 0.9 AND 1.1)::INT) FILTER (
      WHERE dt BETWEEN 30 AND 90 AND vel > 100 AND pvel > 100) AS within_10pct
FROM q
"""


DROPOUT_SQL = """
WITH r AS (
  SELECT its,
         count(*) AS total,
         count(*) FILTER (WHERE lat >= 58) AS scand,
         count(*) FILTER (WHERE lat <= 41 AND lon <= -1) AS iberia,
         count(*) FILTER (WHERE lat <= 39 AND lon >= 21) AS balkans,
         count(*) FILTER (WHERE lat >= 48 AND lon >= 26) AS east
  FROM d
  WHERE source = 'adsb_lol' AND NOT coalesce(og, false)
    AND lat IS NOT NULL AND lon IS NOT NULL
  GROUP BY its
), m AS (
  SELECT median(total) AS mt, median(scand) AS ms, median(iberia) AS mi,
         median(balkans) AS mb, median(east) AS me FROM r
)
SELECT count(*), any_value(mt), min(total),
       avg((total < 0.7 * mt)::INT), avg((total < 0.5 * mt)::INT),
       avg((scand < 0.25 * ms)::INT), avg((iberia < 0.25 * mi)::INT),
       avg((balkans < 0.25 * mb)::INT), avg((east < 0.25 * me)::INT)
FROM r, m
"""


# The "% of daily median" dropout numbers above also pick up NORMAL night-time traffic dips.
# A real fetch failure (HTTP 429 on some hub circles) is a SUDDEN fall versus the polls right
# before/after it, so compare each poll with the mean of its 2 previous + 2 next polls.
SUDDEN_SQL = """
WITH r AS (
  SELECT its,
         count(*) AS total,
         count(*) FILTER (WHERE lat >= 58) AS scand,
         count(*) FILTER (WHERE lat <= 41 AND lon <= -1) AS iberia,
         count(*) FILTER (WHERE lat <= 39 AND lon >= 21) AS balkans,
         count(*) FILTER (WHERE lat >= 48 AND lon >= 26) AS east
  FROM d
  WHERE source = 'adsb_lol' AND NOT coalesce(og, false)
    AND lat IS NOT NULL AND lon IS NOT NULL
  GROUP BY its
), n AS (
  SELECT *,
    (lag(total,1) OVER w + lead(total,1) OVER w + lag(total,2) OVER w + lead(total,2) OVER w) / 4.0 AS rt,
    (lag(scand,1) OVER w + lead(scand,1) OVER w + lag(scand,2) OVER w + lead(scand,2) OVER w) / 4.0 AS rs,
    (lag(iberia,1) OVER w + lead(iberia,1) OVER w + lag(iberia,2) OVER w + lead(iberia,2) OVER w) / 4.0 AS ri,
    (lag(balkans,1) OVER w + lead(balkans,1) OVER w + lag(balkans,2) OVER w + lead(balkans,2) OVER w) / 4.0 AS rb,
    (lag(east,1) OVER w + lead(east,1) OVER w + lag(east,2) OVER w + lead(east,2) OVER w) / 4.0 AS re
  FROM r WINDOW w AS (ORDER BY its)
)
SELECT
  avg((total < 0.6 * rt)::INT) FILTER (WHERE rt >= 200),
  avg((scand < 0.4 * rs)::INT) FILTER (WHERE rs >= 20),
  avg((iberia < 0.4 * ri)::INT) FILTER (WHERE ri >= 20),
  avg((balkans < 0.4 * rb)::INT) FILTER (WHERE rb >= 20),
  avg((east < 0.4 * re)::INT) FILTER (WHERE re >= 20)
FROM n
"""


def main() -> None:
    """Print one audit block per day."""
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")
    t_all = time.time()
    tot_real = tot_sim = 0
    for path in sorted(glob.glob(f"{BRONZE_ROOT}/ingest_date=*")):
        day = os.path.basename(path).split("=")[1]
        files = glob.glob(f"{path}/*/*.gz")
        if len(files) < MIN_FILES_PER_DAY:
            print(f"{day} SKIP files={len(files)}", flush=True)
            continue
        con.execute(LOAD_SQL, [files])
        src = con.execute("SELECT source, count(*) FROM d GROUP BY 1 ORDER BY 2 DESC").fetchall()
        n_fresh, tp_null, lag_med, lag_p99, lag_gt30, la0, la1, lo0, lo1, n_india = con.execute(
            FRESH_SQL
        ).fetchone()
        same_ts, n_phys, ratio_med, within = con.execute(PHYS_SQL).fetchone()
        tot_real += sum(c for s, c in src if s == "adsb_lol")
        tot_sim += sum(c for s, c in src if s != "adsb_lol")
        print(f"\n== {day}  files={len(files)}")
        print("  source mix:", ", ".join(f"{s}={c:,}" for s, c in src))
        print(
            f"  position age (ingest - time_position): median={lag_med:.1f}s p99={lag_p99:.1f}s "
            f">30s={lag_gt30:.1%} tp_null={tp_null:.1%} repeated_same_tp={same_ts:.1%}"
        )
        print(
            f"  physics check: n={n_phys:,} implied_speed/reported_speed median={ratio_med:.3f} "
            f"within±10%={within:.1%}"
        )
        print(
            f"  bounds (real rows): lat {la0:.1f}..{la1:.1f}  lon {lo0:.1f}..{lo1:.1f}  "
            f"rows_in_india_box={n_india:,}"
        )
        n_polls, med_total, min_total, lt70, lt50, d_sc, d_ib, d_ba, d_ea = con.execute(
            DROPOUT_SQL
        ).fetchone()
        print(
            f"  polls={n_polls:,} aircraft/poll median={med_total:,.0f} min={min_total:,} | "
            f"polls below 70% of median={lt70:.1%}, below 50%={lt50:.1%}"
        )
        print(
            f"  region dropouts (share of polls with <25% of that region's median): "
            f"scandinavia={d_sc:.1%} iberia={d_ib:.1%} balkans={d_ba:.1%} east={d_ea:.1%}"
        )
        s_tot, s_sc, s_ib, s_ba, s_ea = con.execute(SUDDEN_SQL).fetchone()
        fmt = lambda v: "n/a" if v is None else f"{v:.1%}"  # noqa: E731
        print(
            f"  SUDDEN drops vs neighbouring polls (real fetch failures, not night dips): "
            f"all={fmt(s_tot)} scandinavia={fmt(s_sc)} iberia={fmt(s_ib)} "
            f"balkans={fmt(s_ba)} east={fmt(s_ea)}",
            flush=True,
        )
    print(f"\nTOTAL real(adsb_lol)={tot_real:,}  non-real(simulator etc)={tot_sim:,} "
          f"| {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
