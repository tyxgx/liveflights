"""Silver layer: bronze Parquet -> Delta at s3a://<bucket>/silver/.

Parses the raw JSON payload against an explicit schema (never inferred on a
stream), dedups on (icao24, time_position), and enriches each row with
speed_kmh, altitude_ft, flight_phase, geohash5, region, and
data_quality_flags. Idempotency across replays/restarts comes from a Delta
MERGE (upsert) keyed on the same natural key, applied in `foreachBatch` —
not from streaming `dropDuplicates` state alone, since that only covers a
bounded watermark window and is lost if a query is torn down and rebuilt
from scratch outside its checkpoint lineage.
"""

from __future__ import annotations

import logging

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, from_json, to_timestamp, udf
from pyspark.sql.types import ArrayType, DoubleType, StringType

from streaming.config import settings
from streaming.schemas import BRONZE_SCHEMA, FLIGHT_STATE_SCHEMA
from streaming.session import get_spark_session
from streaming.utils.enrich import (
    altitude_ft,
    data_quality_flags,
    flight_phase,
    geohash5,
    region_bucket,
    speed_kmh,
)

logger = logging.getLogger("streaming.silver")

_region_udf = udf(region_bucket, StringType())
_geohash_udf = udf(geohash5, StringType())
_phase_udf = udf(flight_phase, StringType())
_speed_udf = udf(speed_kmh, DoubleType())
_altitude_udf = udf(altitude_ft, DoubleType())
_quality_flags_udf = udf(data_quality_flags, ArrayType(StringType()))


def parse_and_enrich(bronze_df: DataFrame) -> DataFrame:
    """Parse bronze's raw JSON payload and add silver enrichment columns."""
    parsed = bronze_df.select(
        from_json(col("raw_payload"), FLIGHT_STATE_SCHEMA).alias("f"),
        col("kafka_partition"),
        col("kafka_offset"),
    ).where(col("f").isNotNull())

    flat = parsed.select("f.*", "kafka_partition", "kafka_offset").withColumn(
        "ingest_ts", to_timestamp(col("ingest_ts"))
    )

    enriched = (
        flat.withColumn("speed_kmh", _speed_udf(col("velocity")))
        .withColumn("altitude_ft", _altitude_udf(col("baro_altitude"), col("geo_altitude")))
        .withColumn("flight_phase", _phase_udf(col("on_ground"), col("vertical_rate")))
        .withColumn("geohash5", _geohash_udf(col("latitude"), col("longitude")))
        .withColumn("region", _region_udf(col("latitude"), col("longitude")))
        .withColumn(
            "data_quality_flags",
            _quality_flags_udf(
                col("latitude"),
                col("longitude"),
                col("time_position"),
                col("last_contact"),
                col("velocity"),
                col("baro_altitude"),
                col("vertical_rate"),
                col("squawk"),
            ),
        )
    )
    return enriched.dropDuplicates(["icao24", "time_position"])


_MERGE_KEY = "t.icao24 = s.icao24 AND t.time_position <=> s.time_position"


def make_foreach_batch_writer(spark: SparkSession, silver_path: str):
    """Returns a foreachBatch function that upserts each micro-batch into
    the silver Delta table, keyed on (icao24, time_position). Re-delivering
    the same record (a replay, or Kafka's at-least-once redelivery) updates
    the existing row instead of inserting a duplicate.
    """

    def _write_batch(batch_df: DataFrame, batch_id: int) -> None:
        batch_df = batch_df.dropDuplicates(["icao24", "time_position"])
        if batch_df.rdd.isEmpty():
            logger.info("batch %d: empty, skipping", batch_id)
            return
        if DeltaTable.isDeltaTable(spark, silver_path):
            target = DeltaTable.forPath(spark, silver_path)
            (
                target.alias("t")
                .merge(batch_df.alias("s"), _MERGE_KEY)
                .whenMatchedUpdateAll()
                .whenNotMatchedInsertAll()
                .execute()
            )
        else:
            batch_df.write.format("delta").mode("append").save(silver_path)
        logger.info("batch %d: merged %d rows into silver", batch_id, batch_df.count())

    return _write_batch


def run(timeout_seconds: int | None = None) -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spark = get_spark_session("liveflights-silver")
    spark.sparkContext.setLogLevel("WARN")

    bronze_stream = spark.readStream.schema(BRONZE_SCHEMA).format("parquet").load(
        settings.lake_path("bronze")
    )

    silver_df = parse_and_enrich(bronze_stream)
    silver_path = settings.lake_path("silver")
    writer = make_foreach_batch_writer(spark, silver_path)

    query = (
        silver_df.writeStream.foreachBatch(writer)
        .option("checkpointLocation", f"{settings.checkpoint_root}/silver")
        .trigger(processingTime="30 seconds")
        .outputMode("append")
        .start()
    )

    logger.info("silver_stream started: writing to %s", silver_path)
    if timeout_seconds:
        query.awaitTermination(timeout_seconds)
        query.stop()
    else:
        query.awaitTermination()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="liveflights silver streaming job")
    parser.add_argument("--timeout-seconds", type=int, default=None)
    args = parser.parse_args()
    run(args.timeout_seconds)


if __name__ == "__main__":
    main()
