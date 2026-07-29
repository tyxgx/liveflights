"""Configuration for the FastAPI app, loaded from environment/.env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """Every field maps 1:1 to a variable in .env.example — keep them in sync."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000"
    ws_push_interval_seconds: float = 3.0

    database_url: str = "postgresql://liveflights:liveflights@localhost:5433/liveflights"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_cache_ttl_seconds: int = 60

    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic_flights_raw: str = "flights.raw"

    mlflow_tracking_uri: str = "http://localhost:5500"

    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


settings = APISettings()
