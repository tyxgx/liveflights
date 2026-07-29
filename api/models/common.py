"""Shared response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ComponentStatus(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str = Field(..., description="'ok' if every component is healthy, else 'degraded'")
    database: ComponentStatus
    redis: ComponentStatus
    kafka_live_store: ComponentStatus
    trajectory_model: ComponentStatus
    forecast_model: ComponentStatus
