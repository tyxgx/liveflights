"""Anomaly response + prediction request/response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AnomalyEvent(BaseModel):
    icao24: str
    callsign: str | None = None
    origin_country: str | None = None
    ingest_ts: datetime
    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = None
    speed_kmh: float | None = None
    anomaly_score: float
    anomaly_type: str = Field(..., description="Comma-joined rule flags and/or ML reasons")
    nearest_corridor_id: int | None = None
    lateral_distance_km: float | None = None
    heading_deviation_deg: float | None = None
    altitude_z: float | None = None
    unassigned_corridor: bool | None = None


class AnomaliesResponse(BaseModel):
    total: int
    page: int
    page_size: int
    events: list[AnomalyEvent]


class PredictAnomalyRequest(BaseModel):
    icao24: str
    latitude: float | None = None
    longitude: float | None = None
    velocity: float | None = Field(None, ge=0, description="m/s")
    true_track: float | None = Field(None, ge=0, le=360)
    vertical_rate: float | None = None
    baro_altitude: float | None = None
    time_position: int | None = None
    last_contact: int | None = None
    squawk: str | None = None


class PredictAnomalyResponse(BaseModel):
    is_anomaly: bool
    anomaly_score: float
    rule_flags: list[str]
    ml_reasons: list[str]
    nearest_corridor_id: int | None = None
    lateral_distance_km: float | None = None
    heading_deviation_deg: float | None = None
    altitude_z: float | None = None
