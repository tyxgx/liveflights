"""Next-6-hour traffic forecast."""

from __future__ import annotations

from fastapi import APIRouter, Response

from api.deps.cache import cached
from api.models.forecast import ForecastResponse
from api.services import forecast_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

_forecast_cached = cached("forecast:traffic", ttl=60)(forecast_service.get_forecast)


@router.get(
    "/traffic",
    response_model=ForecastResponse,
    summary="Next 6 hours of traffic, with an approximate confidence band",
    description=(
        "Model 4 (GradientBoostingRegressor), trained on synthetic history "
        "— flagged explicitly in the response."
    ),
)
def forecast_traffic(response: Response) -> ForecastResponse:
    data, hit = _forecast_cached()
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return ForecastResponse(**data)
