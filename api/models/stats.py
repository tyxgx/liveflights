"""KPI/stats response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OverviewStats(BaseModel):
    active_flights: int
    countries: int
    avg_altitude_ft: float | None
    anomaly_count: int


class TrafficByHourPoint(BaseModel):
    hour_bucket: datetime
    flight_count: int
    avg_altitude_ft: float | None
    avg_speed_kmh: float | None
    is_synthetic: bool = False


class TrafficByHourResponse(BaseModel):
    points: list[TrafficByHourPoint]


class CountryStat(BaseModel):
    origin_country: str
    flight_count: int
    avg_altitude_ft: float | None
    avg_speed_kmh: float | None


class ByCountryResponse(BaseModel):
    countries: list[CountryStat]
