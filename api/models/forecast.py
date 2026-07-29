"""Traffic forecast response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ForecastPoint(BaseModel):
    hour_bucket: datetime
    predicted_flight_count: float
    lower_bound: float
    upper_bound: float


class ForecastResponse(BaseModel):
    trained_on_synthetic_history: bool = Field(
        True, description="Model 4 is trained on a generated synthetic history — see PROGRESS.md P5"
    )
    points: list[ForecastPoint]
