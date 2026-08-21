"""Load the OpenSky historical files already downloaded by
`scripts/download_opensky_historical_raw.sh` (into
`data/opensky_historical_raw/<date>/states_<date>-<hour>.csv.gz`) into
bronze + silver — no re-downloading, just the map + land step, reusing
`land_hour_into_bronze_and_silver` from `backfill_opensky_historical.py`
(same mapping, same bronze/silver shapes, same Delta MERGE the live
pipeline uses).

Usage:
    uv run python -m scripts.load_raw_historical_to_silver
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from scripts.backfill_opensky_historical import (
    MIN_FREE_DISK_GB,
    _free_disk_gb,
    land_hour_into_bronze_and_silver,
)
from streaming.config import StreamingSettings, settings
from streaming.jobs.silver_stream import make_foreach_batch_writer
from streaming.session import get_spark_session

# This machine has 8 cores; the shared default (spark_cores=2 in
# streaming/config.py) is sized for several concurrent local Spark drivers
# coexisting (see that file's comment) — fine for the live pipeline's
# bronze/silver/gold running together, but this one-off backfill runs
# alone, so it can safely claim more cores for a large speedup.
_BACKFILL_SPARK_CFG = StreamingSettings(spark_cores=6, shuffle_partitions=12)

logger = logging.getLogger("scripts.load_raw_historical_to_silver")

RAW_ROOT = Path(__file__).resolve().parent.parent / "data" / "opensky_historical_raw"
FILENAME_RE = re.compile(r"states_(\d{4}-\d{2}-\d{2})-(\d{2})\.csv\.gz$")


def find_downloaded_files() -> list[tuple[str, int, Path]]:
    """Every already-downloaded (date, hour, path) tuple, sorted."""
    found = []
    for path in sorted(RAW_ROOT.glob("*/*.csv.gz")):
        m = FILENAME_RE.match(path.name)
        if not m:
            logger.warning("skipping unrecognized filename: %s", path)
            continue
        date, hour = m.group(1), int(m.group(2))
        found.append((date, hour, path))
    return found


def load_all() -> None:
    files = find_downloaded_files()
    if not files:
        logger.warning("no downloaded files found under %s", RAW_ROOT)
        return
    logger.info("found %d downloaded hourly files to load", len(files))

    spark = get_spark_session("liveflights-load-historical", cfg=_BACKFILL_SPARK_CFG)
    spark.sparkContext.setLogLevel("WARN")

    bronze_path = settings.lake_path("bronze")
    silver_path = settings.lake_path("silver")
    writer = make_foreach_batch_writer(spark, silver_path)

    total_rows = 0
    for date, hour, csv_gz_path in files:
        free_gb = _free_disk_gb()
        if free_gb < MIN_FREE_DISK_GB:
            raise RuntimeError(
                f"Free disk space ({free_gb:.1f} GB) below safety margin "
                f"({MIN_FREE_DISK_GB} GB) — stopping before {date} hour={hour:02d}."
            )

        n = land_hour_into_bronze_and_silver(
            csv_gz_path, date, hour, spark, bronze_path, writer
        )
        total_rows += n
        logger.info(
            "%s hour=%02d: landed %d rows into bronze+silver (free disk: %.1f GB)",
            date,
            hour,
            n,
            free_gb,
        )

    logger.info(
        "Load complete: %d total rows across %d hourly files.", total_rows, len(files)
    )


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_all()
