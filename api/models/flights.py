"""Live flight + trajectory response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LiveFlight(BaseModel):
    icao24: str
    callsign: str | None = None
    origin_country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    baro_altitude: float | None = Field(None, description="Meters")
    velocity: float | None = Field(None, description="m/s")
    true_track: float | None = None
    vertical_rate: float | None = None
    on_ground: bool = False
    time_position: int | None = None
    source: str | None = None


class LiveFlightsResponse(BaseModel):
    count: int
    flights: list[LiveFlight]


class TrackPoint(BaseModel):
    time_position: int | None = None
    latitude: float | None = None
    longitude: float | None = None


class GhostPoint(BaseModel):
    predicted_latitude: float
    predicted_longitude: float
    horizon_seconds: int = 300


class TrajectoryResponse(BaseModel):
    icao24: str
    recent_track: list[TrackPoint]
    predicted: GhostPoint | None = Field(
        None, description="Null if not enough recent history to compute lag features"
    )
