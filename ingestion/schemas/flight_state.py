"""Canonical flight-state schema shared across ingestion, streaming, and the API.

Field names mirror the OpenSky /states/all response so downstream layers
don't need per-source translation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Source = Literal["simulate", "opensky"]


class FlightState(BaseModel):
    """A single aircraft state vector, as produced onto `flights.raw`."""

    icao24: str = Field(..., min_length=1, max_length=6, description="Unique ICAO 24-bit address")
    callsign: str | None = Field(None, description="Callsign, may be blank/whitespace")
    origin_country: str = Field(..., description="Country inferred from ICAO24 address")
    time_position: int | None = Field(None, description="Unix time of last position update")
    last_contact: int = Field(..., description="Unix time of last update of any kind")
    longitude: float | None = Field(None, ge=-180, le=180)
    latitude: float | None = Field(None, ge=-90, le=90)
    baro_altitude: float | None = Field(None, description="Barometric altitude, meters")
    on_ground: bool = Field(False, description="Whether the ADS-B transponder reports ground state")
    velocity: float | None = Field(None, ge=0, description="Ground speed, m/s")
    true_track: float | None = Field(None, ge=0, le=360, description="Track angle, degrees")
    vertical_rate: float | None = Field(None, description="Vertical rate, m/s")
    geo_altitude: float | None = Field(None, description="Geometric altitude, meters")
    squawk: str | None = Field(None, description="Transponder squawk code")
    spi: bool = Field(False, description="Special purpose indicator (e.g. ident)")
    position_source: int = Field(0, description="0=ADS-B, 1=ASTERIX, 2=MLAT, 3=FLARM")

    # Ingest metadata, appended by the producer, not part of the OpenSky payload.
    ingest_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: Source = "simulate"

    @field_validator("callsign")
    @classmethod
    def strip_callsign(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @property
    def ingest_date(self) -> str:
        return self.ingest_ts.strftime("%Y-%m-%d")

    @property
    def ingest_hour(self) -> str:
        return self.ingest_ts.strftime("%H")
