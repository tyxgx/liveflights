"""Bronze layer: Kafka(flights.raw) -> Parquet on s3a://<bucket>/bronze/.

No parsing beyond capturing the raw JSON string and ingest metadata — schema
validation and enrichment happen in silver. Partitioned by ingest_date/
ingest_hour derived from the Kafka broker's own record timestamp (not the
payload), so bronze never depends on the payload being well-formed.
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql.functions import col, date_format, lit

from streaming.config import settings
from streaming.session import get_spark_session

logger = logging.getLogger("streaming.bronze")


def run(source_mode: str, timeout_seconds: int | None = None) -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spark = get_spark_session("liveflights-bronze")
    spark.sparkContext.setLogLevel("WARN")

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka_bootstrap_servers)
        .option("subscribe", settings.kafka_topic_flights_raw)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    bronze = raw.select(
        col("value").cast("string").alias("raw_payload"),
        col("key").cast("string").alias("kafka_key"),
        col("partition").alias("kafka_partition"),
        col("offset").alias("kafka_offset"),
        col("timestamp").alias("kafka_timestamp"),
        lit(source_mode).alias("source_mode"),
        date_format(col("timestamp"), "yyyy-MM-dd").alias("ingest_date"),
        date_format(col("timestamp"), "HH").alias("ingest_hour"),
    )

    query = (
        bronze.writeStream.format("parquet")
        .option("path", settings.lake_path("bronze"))
        .option("checkpointLocation", f"{settings.checkpoint_root}/bronze")
        .partitionBy("ingest_date", "ingest_hour")
        .trigger(processingTime="30 seconds")
        .outputMode("append")
        .start()
    )

    logger.info("bronze_stream started: writing to %s", settings.lake_path("bronze"))
    if timeout_seconds:
        query.awaitTermination(timeout_seconds)
        query.stop()
    else:
        query.awaitTermination()


def main() -> None:
    parser = argparse.ArgumentParser(description="liveflights bronze streaming job")
    parser.add_argument("--source-mode", default="simulate", help="Informational tag only")
    parser.add_argument("--timeout-seconds", type=int, default=None)
    args = parser.parse_args()
    run(args.source_mode, args.timeout_seconds)


if __name__ == "__main__":
    main()
