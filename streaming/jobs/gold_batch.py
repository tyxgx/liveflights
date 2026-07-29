"""Gold layer: batch job reading the silver Delta table (not a stream).

Streaming aggregations would force watermark/output-mode complexity we
don't need here — gold aggregates are cheap to fully recompute from silver
at this data volume, so this runs as a plain batch job (intended to be
triggered hourly by Airflow's `hourly_compaction` DAG in P8). Writes each
gold table to Delta (overwrite) and to Postgres via a single JDBC batch
write per table (not row-by-row inserts).
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import (
    avg,
    col,
    countDistinct,
    date_trunc,
    udf,
    when,
)
from pyspark.sql.types import StringType

from streaming.config import settings
from streaming.session import get_spark_session
from streaming.utils.airlines import callsign_to_airline

logger = logging.getLogger("streaming.gold")

_airline_udf = udf(callsign_to_airline, StringType())


def _altitude_band(altitude_ft_col):
    return (
        when(altitude_ft_col.isNull(), "unknown")
        .when(altitude_ft_col < 2000, "0-2000ft")
        .when(altitude_ft_col < 6000, "2000-6000ft")
        .when(altitude_ft_col < 10000, "6000-10000ft")
        .when(altitude_ft_col < 20000, "10000-20000ft")
        .when(altitude_ft_col < 30000, "20000-30000ft")
        .when(altitude_ft_col < 40000, "30000-40000ft")
        .otherwise("40000ft+")
    )


def build_traffic_by_hour(silver: DataFrame) -> DataFrame:
    return silver.groupBy(date_trunc("hour", col("ingest_ts")).alias("hour_bucket")).agg(
        countDistinct("icao24").alias("flight_count"),
        avg("altitude_ft").alias("avg_altitude_ft"),
        avg("speed_kmh").alias("avg_speed_kmh"),
    )


def build_traffic_by_country(silver: DataFrame) -> DataFrame:
    return silver.groupBy(col("origin_country")).agg(
        countDistinct("icao24").alias("flight_count"),
        avg("altitude_ft").alias("avg_altitude_ft"),
        avg("speed_kmh").alias("avg_speed_kmh"),
    )


def build_airline_activity(silver: DataFrame) -> DataFrame:
    return (
        silver.withColumn("airline", _airline_udf(col("callsign")))
        .groupBy("airline")
        .agg(
            countDistinct("icao24").alias("flight_count"),
            avg("speed_kmh").alias("avg_speed_kmh"),
            avg("altitude_ft").alias("avg_altitude_ft"),
        )
    )


def build_altitude_band_distribution(silver: DataFrame) -> DataFrame:
    return (
        silver.withColumn("altitude_band", _altitude_band(col("altitude_ft")))
        .groupBy("altitude_band")
        .agg(
            countDistinct("icao24").alias("flight_count"),
            avg("speed_kmh").alias("avg_speed_kmh"),
        )
    )


def write_gold_table(spark: SparkSession, df: DataFrame, name: str) -> None:
    delta_path = f"{settings.lake_path('gold')}/{name}"
    df.write.format("delta").mode("overwrite").save(delta_path)
    row_count = df.count()
    logger.info("gold.%s: wrote %d rows to %s", name, row_count, delta_path)

    (
        df.write.format("jdbc")
        .option("url", settings.jdbc_url)
        .option("dbtable", f"gold.{name}")
        .option("user", settings.postgres_user)
        .option("password", settings.postgres_password)
        .option("driver", "org.postgresql.Driver")
        .option("batchsize", "1000")
        # truncate=true makes Spark's "overwrite" mode issue TRUNCATE instead
        # of DROP+CREATE, so the table identity (and dbt's staging views built
        # on top of it) survive a gold_batch run untouched. Without this,
        # DROP TABLE fails outright once a dbt view depends on the table.
        .option("truncate", "true")
        .mode("overwrite")
        .save()
    )
    logger.info("gold.%s: upserted %d rows into Postgres", name, row_count)


def run() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spark = get_spark_session("liveflights-gold")
    spark.sparkContext.setLogLevel("WARN")

    silver_path = settings.lake_path("silver")
    silver = spark.read.format("delta").load(silver_path)
    logger.info("gold_batch: read %d rows from silver at %s", silver.count(), silver_path)

    # anomaly_events is NOT built here — ml/anomaly.py owns that table
    # exclusively (corridor-aware ML scoring). This job used to also write
    # a naive rule-based placeholder version of the same table; if this
    # job and ml/anomaly.py both ran on a schedule (as planned for P8's
    # hourly_compaction + daily_ml_retrain DAGs), gold_batch.py would
    # periodically clobber the real ML-scored anomaly data with the
    # placeholder. Removed rather than left to collide silently.
    tables = {
        "traffic_by_hour": build_traffic_by_hour(silver),
        "traffic_by_country": build_traffic_by_country(silver),
        "airline_activity": build_airline_activity(silver),
        "altitude_band_distribution": build_altitude_band_distribution(silver),
    }
    for name, df in tables.items():
        write_gold_table(spark, df, name)

    spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="liveflights gold batch job")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
