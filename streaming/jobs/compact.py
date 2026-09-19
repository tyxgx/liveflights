"""Delta table maintenance: OPTIMIZE (file compaction) + VACUUM (remove
stale files past the retention window) on silver and gold.

Structured Streaming (bronze/silver) and gold_batch's per-run overwrites
both produce many small Delta files over time — small-file buildup slows
every downstream read (dbt, the API's Postgres mirror is unaffected, but
any future direct Delta read is not). This job is the maintenance side of
that: it does not touch data values, only file layout and old-version
cleanup. Intended to run hourly via Airflow's `hourly_compaction` DAG
(P8), before gold_batch.py rebuilds the gold aggregates.
"""

from __future__ import annotations

import argparse
import logging

from delta.tables import DeltaTable

from streaming.config import settings
from streaming.session import get_spark_session

logger = logging.getLogger("streaming.compact")

# Layers with a Delta table directly at the layer root (silver). Gold is a
# directory of per-metric Delta tables (gold/<table_name>/), enumerated at
# runtime instead of hardcoded here.
SIMPLE_LAYERS = ["silver"]

# Default VACUUM retention. Delta's own safety floor is 168h (7 days) unless
# the retention check is explicitly disabled; we don't disable it — this is
# demo data, not a compliance archive, and the default is a sane guardrail
# against vacuuming files a still-in-flight reader needs.
DEFAULT_RETENTION_HOURS = 168


def _optimize_and_vacuum(spark, path: str, retention_hours: int) -> None:
    if not DeltaTable.isDeltaTable(spark, path):
        logger.info("skip %s: not a Delta table (nothing written yet)", path)
        return
    table = DeltaTable.forPath(spark, path)
    table.optimize().executeCompaction()
    table.vacuum(retention_hours)
    logger.info("compacted + vacuumed (retention=%dh): %s", retention_hours, path)


def run(retention_hours: int = DEFAULT_RETENTION_HOURS) -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spark = get_spark_session("liveflights-compact")
    spark.sparkContext.setLogLevel("WARN")

    for layer in SIMPLE_LAYERS:
        _optimize_and_vacuum(spark, settings.lake_path(layer), retention_hours)

    gold_root = settings.lake_path("gold")
    hadoop_conf = spark._jsc.hadoopConfiguration()
    gold_path = spark._jvm.org.apache.hadoop.fs.Path(gold_root)
    # FileSystem.get(conf) resolves the *default* FS (local file:///) —
    # gold_root is s3a://, so listing it needs the FS resolved from the
    # path's own scheme instead, or every call here 404s against the local
    # filesystem with "Wrong FS: s3a://..., expected: file:///".
    fs = gold_path.getFileSystem(hadoop_conf)
    if fs.exists(gold_path):
        for status in fs.listStatus(gold_path):
            if status.isDirectory():
                table_path = f"{gold_root}/{status.getPath().getName()}"
                _optimize_and_vacuum(spark, table_path, retention_hours)
    else:
        logger.info("skip gold/*: %s does not exist yet", gold_root)

    spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="liveflights Delta compaction + vacuum")
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=DEFAULT_RETENTION_HOURS,
        help="VACUUM retention window in hours (default: 168 = 7 days)",
    )
    args = parser.parse_args()
    run(retention_hours=args.retention_hours)


if __name__ == "__main__":
    main()
