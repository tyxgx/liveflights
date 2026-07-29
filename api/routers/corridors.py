"""Discovered air corridors."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api.models.corridors import CorridorsResponse
from api.services import corridor_service

router = APIRouter(prefix="/api/corridors", tags=["corridors"])


@router.get(
    "",
    response_model=CorridorsResponse,
    summary="Discovered air corridors",
    description=(
        "DBSCAN-discovered corridors (Model 1), sorted by member count. "
        "Capped by `limit` so the map never tries to render all corridors at once."
    ),
)
def corridors(limit: int = Query(20, ge=1, le=238)) -> CorridorsResponse:
    result = corridor_service.list_corridors(limit)
    return CorridorsResponse(**result)
