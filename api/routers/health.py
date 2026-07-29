"""Service health: DB, Redis, live Kafka feed, and model-load status."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import cache as cache_dep
from api.deps import db as db_dep
from api.models.common import ComponentStatus, HealthResponse
from api.services import live_store, models_loader

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Reports Postgres, Redis, the live Kafka feed, and ML model-load status.",
)
def health() -> HealthResponse:
    db_ok = db_dep.check_connection()
    redis_ok = cache_dep.check_connection()
    kafka_ok = live_store.store.is_receiving()

    traj_ok = models_loader.bundle.trajectory_ready
    fc_ok = models_loader.bundle.forecast_ready

    overall = "ok" if all([db_ok, redis_ok, traj_ok, fc_ok]) else "degraded"
    return HealthResponse(
        status=overall,
        database=ComponentStatus(ok=db_ok),
        redis=ComponentStatus(ok=redis_ok),
        kafka_live_store=ComponentStatus(
            ok=kafka_ok, detail="no messages received recently" if not kafka_ok else None
        ),
        trajectory_model=ComponentStatus(ok=traj_ok, detail=models_loader.bundle.trajectory_source),
        forecast_model=ComponentStatus(ok=fc_ok, detail=models_loader.bundle.forecast_source),
    )
