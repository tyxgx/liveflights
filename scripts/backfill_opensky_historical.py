"""Backfill 4 Mondays of OpenSky's free historical sample archive into bronze
+ silver, reusing the same landing/transform shapes the live Kafka pipeline
writes (`streaming/jobs/bronze_stream.py`, `streaming/jobs/silver_stream.py`)
so historical and live rows are indistinguishable downstream.

Source: https://s3.opensky-network.org/data-samples/states/ — public, no
login. Global coverage, ADS-B only, one full day per week (Mondays only,
2020-05-25 through 2022-06-27). See `ingestion/schemas/opensky_historical_mapping.py`
for the column-mapping rationale and simplifications.

Processes one (date, hour) at a time — download the hourly .tar, extract,
map + write straight to bronze parquet, delete the raw files — so peak disk
usage stays low regardless of how many Mondays are requested. Then runs a
one-off static-batch pass over just the newly-written historical bronze
partitions through the existing silver `parse_and_enrich` + Delta MERGE
logic (imported from `silver_stream.py`, not duplicated).

Usage:
    uv run python -m scripts.backfill_opensky_historical
    uv run python -m scripts.backfill_opensky_historical --dates 2022-03-07 --hours 0 1 2
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pandas as pd
from pyspark.sql import DataFrame
from pyspark.sql.functions import date_format, to_timestamp

from streaming.config import settings
from streaming.jobs.silver_stream import make_foreach_batch_writer, parse_and_enrich
from streaming.session import get_spark_session

logger = logging.getLogger("scripts.backfill_opensky_historical")

ARCHIVE_URL = "https://s3.opensky-network.org/data-samples/states/{date}/{hour:02d}/states_{date}-{hour:02d}.csv.tar"

DEFAULT_DATES = [
    "2022-03-07",
    "2022-03-28",
    "2022-04-18",
    "2022-05-16",
]
ALL_HOURS = list(range(24))

MIN_FREE_DISK_GB = 5.0


def _free_disk_gb(path: str = ".") -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024**3)


def _download_and_extract_hour(date: str, hour: int, work_dir: Path) -> Path | None:
    url = ARCHIVE_URL.format(date=date, hour=hour)
    tar_path = work_dir / f"states_{date}-{hour:02d}.csv.tar"
    # curl, not urllib.request.urlretrieve — this machine's Python SSL
    # context can't verify the OpenSky archive's cert chain (missing local
    # CA bundle recognition), while curl uses the system trust store fine.
    result = subprocess.run(
        ["curl", "-sf", "-o", str(tar_path), url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not tar_path.exists():
        logger.warning(
            "skip %s hour=%02d: download failed (curl exit %d: %s)",
            date,
            hour,
            result.returncode,
            result.stderr.strip(),
        )
        return None

    with tarfile.open(tar_path) as tar:
        tar.extractall(work_dir)  # noqa: S202 - trusted, fixed OpenSky archive
    tar_path.unlink()

    gz_path = work_dir / f"states_{date}-{hour:02d}.csv.gz"
    csv_path = work_dir / f"states_{date}-{hour:02d}.csv"
    if gz_path.exists():
        with gzip.open(gz_path, "rb") as f_in, open(csv_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_path.unlink()
    return csv_path if csv_path.exists() else None


def _row_looks_valid(mapped: dict) -> bool:
    """Cheap range checks in place of constructing a `FlightState` pydantic
    model per row — building ~2 million pydantic objects per hourly file
    was the single biggest cost in this script (minutes per file), for
    validation this dataset essentially always passes anyway (it's a
    single well-formed source, not third-party user input). Mirrors
    FlightState's `ge`/`le` constraints on the two fields that actually
    have them.
    """
    velocity = mapped["velocity"]
    if velocity is not None and velocity < 0:
        return False
    track = mapped["true_track"]
    return track is None or 0 <= track <= 360


def _map_hour_to_bronze_parquet(csv_path: Path, out_path: Path) -> int:
    """Read one hourly historical CSV, map every row onto the bronze schema
    shape (raw_payload = JSON-encoded FlightState-shaped dict, matching what
    `bronze_stream.py` writes for live Kafka records), and write the result
    to `out_path` as Parquet.

    Writing a local file for Spark to read (`spark.read.parquet`) rather
    than calling `spark.createDataFrame(list_of_dicts)` — the latter
    round-trips every single row through a py4j socket RPC one at a time,
    which is fine for hundreds of rows but takes hours for the ~2 million
    rows in one historical hour. Parquet (not the newline-JSON this
    function used originally) because it's smaller on disk and faster for
    Spark to parse than re-parsing JSON text per row — pandas' `to_parquet`
    doesn't go through PySpark's pandas->Spark Arrow conversion path at all
    (that path is what imports `distutils`, removed in Python 3.12; this
    never touches it), so there's no version conflict to route around here.
    """
    from ingestion.schemas.opensky_historical_mapping import (
        map_historical_row_to_flight_state_dict,
    )

    df = pd.read_csv(csv_path)
    rows = []
    for raw in df.to_dict(orient="records"):
        if pd.isna(raw.get("lat")) or pd.isna(raw.get("lon")) or pd.isna(raw.get("lastcontact")):
            continue
        try:
            mapped = map_historical_row_to_flight_state_dict(raw)
        except Exception as exc:  # noqa: BLE001 - drop malformed historical rows, don't crash the backfill
            logger.debug("dropping malformed row icao24=%s: %s", raw.get("icao24"), exc)
            continue
        if not _row_looks_valid(mapped):
            continue
        rows.append(
            {
                "raw_payload": json.dumps(mapped),
                "kafka_key": mapped["icao24"],
                "kafka_partition": -1,
                "kafka_offset": -1,
                "kafka_timestamp": mapped["ingest_ts"],
                "source_mode": "opensky_historical",
            }
        )
    if not rows:
        return 0
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    return len(rows)


def land_hour_into_bronze_and_silver(
    csv_path: Path,
    date: str,
    hour: int,
    spark,
    bronze_path: str,
    writer,
) -> int:
    """Map one hourly historical CSV (already on local disk, gzip or plain)
    and land it into bronze parquet + silver Delta, reusing the exact
    transform the live Kafka pipeline uses. Shared by both the
    download-then-land flow (`backfill()`) and a load-only flow over
    already-downloaded files. Returns the number of rows landed (0 if the
    file had no usable rows).
    """
    with tempfile.TemporaryDirectory() as tmp:
        parquet_path = Path(tmp) / f"bronze_{date}-{hour:02d}.parquet"
        n = _map_hour_to_bronze_parquet(csv_path, parquet_path)
        if n == 0:
            return 0

        sdf = spark.read.parquet(str(parquet_path))
        sdf = sdf.withColumn("kafka_timestamp", to_timestamp("kafka_timestamp"))
        bronze_df: DataFrame = sdf.withColumn(
            "ingest_date", date_format("kafka_timestamp", "yyyy-MM-dd")
        ).withColumn("ingest_hour", date_format("kafka_timestamp", "HH"))

        (
            bronze_df.write.format("parquet")
            .mode("append")
            .partitionBy("ingest_date", "ingest_hour")
            .save(bronze_path)
        )

        # Land this hour straight into silver too, reusing the exact
        # transform + Delta MERGE the live pipeline uses. parse_and_enrich
        # only needs raw_payload/kafka_partition/kafka_offset, all present
        # on bronze_df already, so no extra read from the bronze path.
        writer(parse_and_enrich(bronze_df), 0)

    return n


def backfill(dates: list[str], hours: list[int]) -> None:
    spark = get_spark_session("liveflights-backfill-historical")
    spark.sparkContext.setLogLevel("WARN")

    bronze_path = settings.lake_path("bronze")
    silver_path = settings.lake_path("silver")
    writer = make_foreach_batch_writer(spark, silver_path)

    total_rows = 0
    for date in dates:
        for hour in hours:
            free_gb = _free_disk_gb()
            if free_gb < MIN_FREE_DISK_GB:
                raise RuntimeError(
                    f"Free disk space ({free_gb:.1f} GB) below safety margin "
                    f"({MIN_FREE_DISK_GB} GB) — stopping backfill before hour "
                    f"{date} {hour:02d}:00 to avoid filling the disk."
                )

            with tempfile.TemporaryDirectory() as tmp:
                csv_path = _download_and_extract_hour(date, hour, Path(tmp))
                if csv_path is None:
                    continue

                n = land_hour_into_bronze_and_silver(
                    csv_path, date, hour, spark, bronze_path, writer
                )
                csv_path.unlink()
                if n == 0:
                    logger.info("%s hour=%02d: no usable rows, skipping", date, hour)
                    continue

                total_rows += n
                logger.info(
                    "%s hour=%02d: landed %d rows into bronze+silver (free disk: %.1f GB)",
                    date,
                    hour,
                    n,
                    free_gb,
                )

    logger.info("Backfill complete: %d total rows across %d dates.", total_rows, len(dates))


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill OpenSky historical Mondays")
    parser.add_argument("--dates", nargs="+", default=DEFAULT_DATES)
    parser.add_argument("--hours", nargs="+", type=int, default=ALL_HOURS)
    args = parser.parse_args()
    backfill(args.dates, args.hours)


if __name__ == "__main__":
    main()
