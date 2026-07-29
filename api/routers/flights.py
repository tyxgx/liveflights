"""Live flight positions + per-aircraft trajectory prediction."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.models.flights import LiveFlightsResponse, TrajectoryResponse
from api.services import trajectory_service
from api.services.live_store import store

router = APIRouter(prefix="/api/flights", tags=["flights"])


@router.get(
    "/live",
    response_model=LiveFlightsResponse,
    summary="Current aircraft positions",
    description=(
        "Live positions from the in-memory Kafka-fed store. "
        "Optional bounding box and result limit."
    ),
)
def live_flights(
    lamin: float | None = Query(None, description="Bounding box min latitude"),
    lomin: float | None = Query(None, description="Bounding box min longitude"),
    lamax: float | None = Query(None, description="Bounding box max latitude"),
    lomax: float | None = Query(None, description="Bounding box max longitude"),
    limit: int = Query(500, ge=1, le=5000),
) -> LiveFlightsResponse:
    bbox = None
    if None not in (lamin, lomin, lamax, lomax):
        bbox = (lamin, lomin, lamax, lomax)
    flights = store.get_all(bbox=bbox, limit=limit)
    return LiveFlightsResponse(count=len(flights), flights=flights)


@router.get(
    "/{icao24}/trajectory",
    response_model=TrajectoryResponse,
    summary="Recent track + 5-minute-ahead ghost trail",
    description=(
        "Recent observed positions plus a predicted position 5 minutes ahead "
        "(Model 2), if enough recent history exists."
    ),
)
def flight_trajectory(icao24: str) -> TrajectoryResponse:
    result = trajectory_service.get_trajectory(icao24)
    if not result["recent_track"]:
        raise HTTPException(status_code=404, detail=f"No recent live data for icao24={icao24}")
    return TrajectoryResponse(**result)
