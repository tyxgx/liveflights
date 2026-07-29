"""Explicit Spark schemas mirroring `ingestion.schemas.flight_state.FlightState`.

Structured Streaming must never infer schema from a live stream (it's
non-deterministic and can silently drop/misname columns on payload drift),
so the JSON shape published onto `flights.raw` — i.e. `FlightState.model_dump
(mode="json")` — is mirrored here by hand. Keep in sync with
`ingestion/schemas/flight_state.py`; `tests/test_region_bucketing.py` and the
schema-contract test in `tests/test_schema_contract.py` are the tripwires
that catch drift between the two.
"""

from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Matches FlightState's field order (JSON payload on flights.raw), plus
# ingest_ts/source which FlightState stamps as pipeline metadata.
FLIGHT_STATE_SCHEMA = StructType(
    [
        StructField("icao24", StringType(), nullable=False),
        StructField("callsign", StringType(), nullable=True),
        StructField("origin_country", StringType(), nullable=False),
        StructField("time_position", LongType(), nullable=True),
        StructField("last_contact", LongType(), nullable=False),
        StructField("longitude", DoubleType(), nullable=True),
        StructField("latitude", DoubleType(), nullable=True),
        StructField("baro_altitude", DoubleType(), nullable=True),
        StructField("on_ground", BooleanType(), nullable=False),
        StructField("velocity", DoubleType(), nullable=True),
        StructField("true_track", DoubleType(), nullable=True),
        StructField("vertical_rate", DoubleType(), nullable=True),
        StructField("geo_altitude", DoubleType(), nullable=True),
        StructField("squawk", StringType(), nullable=True),
        StructField("spi", BooleanType(), nullable=False),
        StructField("position_source", IntegerType(), nullable=False),
        StructField("ingest_ts", StringType(), nullable=False),  # ISO-8601, parsed downstream
        StructField("source", StringType(), nullable=False),
    ]
)

# Schema of the bronze Parquet sink written by bronze_stream.py. This is our
# own controlled output (not third-party payload), so it's safe to keep
# explicit here without inferring — Structured Streaming file sources refuse
# to infer schema on a stream anyway.
BRONZE_SCHEMA = StructType(
    [
        StructField("raw_payload", StringType(), nullable=True),
        StructField("kafka_key", StringType(), nullable=True),
        StructField("kafka_partition", IntegerType(), nullable=True),
        StructField("kafka_offset", LongType(), nullable=True),
        StructField("kafka_timestamp", TimestampType(), nullable=True),
        StructField("source_mode", StringType(), nullable=True),
        StructField("ingest_date", StringType(), nullable=True),
        StructField("ingest_hour", StringType(), nullable=True),
    ]
)
