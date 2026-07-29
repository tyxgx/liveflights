"""Single shared SparkSession builder. Every job imports `get_spark_session`
from here — jar coordinates and S3A/Delta config must never be duplicated
per-job, or a future version bump only gets applied to one job.

Version pins (verified compatible as a set — see PLAN.md/PROGRESS.md for the
reasoning): PySpark 3.5.3 bundles hadoop-client 3.3.4, so hadoop-aws is
pinned to the same 3.3.4, with the aws-java-sdk-bundle version hadoop-aws
3.3.4 itself depends on (1.12.262). The Kafka connector and Delta packages
must track the exact Spark/Scala line (2.12) they were built for.
"""

from __future__ import annotations

from pyspark.sql import SparkSession

from streaming.config import StreamingSettings, settings

SPARK_VERSION = "3.5.3"
SCALA_BINARY_VERSION = "2.12"
DELTA_VERSION = "3.2.1"
HADOOP_AWS_VERSION = "3.3.4"
AWS_SDK_BUNDLE_VERSION = "1.12.262"

MAVEN_PACKAGES = ",".join(
    [
        f"io.delta:delta-spark_{SCALA_BINARY_VERSION}:{DELTA_VERSION}",
        f"org.apache.spark:spark-sql-kafka-0-10_{SCALA_BINARY_VERSION}:{SPARK_VERSION}",
        f"org.apache.spark:spark-token-provider-kafka-0-10_{SCALA_BINARY_VERSION}:{SPARK_VERSION}",
        f"org.apache.hadoop:hadoop-aws:{HADOOP_AWS_VERSION}",
        f"com.amazonaws:aws-java-sdk-bundle:{AWS_SDK_BUNDLE_VERSION}",
        "org.postgresql:postgresql:42.7.4",
    ]
)


def get_spark_session(app_name: str, cfg: StreamingSettings = settings) -> SparkSession:
    """Build (or fetch) the shared SparkSession with Delta + S3A + Kafka wired up."""
    builder = (
        SparkSession.builder.appName(app_name)
        .master(f"local[{cfg.spark_cores}]")
        .config("spark.jars.packages", MAVEN_PACKAGES)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.hadoop.fs.s3a.endpoint", cfg.s3a_endpoint_url)
        .config("spark.hadoop.fs.s3a.access.key", cfg.minio_access_key)
        .config("spark.hadoop.fs.s3a.secret.key", cfg.minio_secret_key)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
        )
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.sql.shuffle.partitions", str(cfg.shuffle_partitions))
        .config("spark.driver.memory", cfg.driver_memory)
        .config("spark.executor.memory", cfg.executor_memory)
        .config("spark.sql.session.timeZone", "UTC")
    )
    return builder.getOrCreate()
