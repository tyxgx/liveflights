"""Configuration for the ingestion service, loaded from environment/.env."""

from pydantic_settings import BaseSettings, SettingsConfigDict

# Bounding boxes (lamin, lomin, lamax, lomax) per selectable REGION. "all" is
# a loose superset spanning all three so a single OpenSky poll can cover
# everything at once (at the cost of a much bigger response payload).
REGION_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "europe": (34.0, -25.0, 71.0, 40.0),
    "us": (24.0, -125.0, 49.0, -66.0),
    "india": (6.0, 68.0, 37.0, 97.5),
    "all": (6.0, -125.0, 60.0, 97.5),
}


class IngestionSettings(BaseSettings):
    """All tunables for the flight-state producer.

    Every field maps 1:1 to a variable in .env.example — keep them in sync.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ingest_mode: str = "simulate"  # simulate | opensky

    # Which airport pool / OpenSky bounding box to use: europe | us | india | all.
    # India is the default target-audience region; Europe and US remain fully
    # selectable, not replaced.
    region: str = "india"

    poll_interval_seconds: int = 15

    # Explicit bbox override (takes precedence over `region`'s default bbox
    # when any of the four are set to a non-None value). Left unset by
    # default so `region` alone drives the bounding box.
    bbox_lamin: float | None = None
    bbox_lomin: float | None = None
    bbox_lamax: float | None = None
    bbox_lomax: float | None = None

    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    opensky_anon_poll_interval_seconds: int = 60

    simulator_aircraft_count: int = 150
    simulator_anomaly_rate: float = 0.02

    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic_flights_raw: str = "flights.raw"
    kafka_topic_flights_dlq: str = "flights.raw.dlq"

    log_level: str = "INFO"

    def resolved_bbox(self) -> tuple[float, float, float, float]:
        """Return the active (lamin, lomin, lamax, lomax), region default
        unless every one of the four explicit bbox_* fields is overridden."""
        if None not in (self.bbox_lamin, self.bbox_lomin, self.bbox_lamax, self.bbox_lomax):
            return (self.bbox_lamin, self.bbox_lomin, self.bbox_lamax, self.bbox_lomax)
        try:
            return REGION_BBOXES[self.region.lower()]
        except KeyError:
            raise ValueError(
                f"Unknown REGION {self.region!r}, expected one of: europe, us, india, all"
            ) from None


settings = IngestionSettings()
