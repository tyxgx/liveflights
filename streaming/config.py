"""Configuration for Spark streaming/batch jobs, loaded from environment/.env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamingSettings(BaseSettings):
    """Tunables shared by bronze/silver/gold jobs. Every field maps 1:1 to a
    variable in .env.example — keep them in sync.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic_flights_raw: str = "flights.raw"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "liveflights"

    database_url: str = "postgresql://liveflights:liveflights@localhost:5433/liveflights"
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "liveflights"
    postgres_user: str = "liveflights"
    postgres_password: str = "liveflights"

    shuffle_partitions: int = 8
    driver_memory: str = "2g"
    executor_memory: str = "2g"
    # Bounded, not local[*]: multiple concurrent local drivers (bronze,
    # silver, gold, ad-hoc queries, and later Airflow tasks) each grabbing
    # every core caused a multi-minute scheduling stall during P3 — local[*]
    # is fine for a single one-off job, but this codebase always expects
    # several Spark drivers to coexist on one machine.
    spark_cores: int = 2

    # Local (not object-store) checkpoint root. Structured Streaming
    # checkpoints need strict atomic-rename semantics that MinIO's S3A
    # implementation doesn't guarantee under concurrent access; the actual
    # lake data still lives on MinIO, only checkpoint metadata is local.
    checkpoint_root: str = "./data/checkpoints"

    @property
    def s3a_endpoint_url(self) -> str:
        return f"http://{self.minio_endpoint}"

    @property
    def jdbc_url(self) -> str:
        return f"jdbc:postgresql://{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    def lake_path(self, layer: str) -> str:
        """s3a:// path for a medallion layer, e.g. lake_path('bronze')."""
        return f"s3a://{self.minio_bucket}/{layer}"


settings = StreamingSettings()
