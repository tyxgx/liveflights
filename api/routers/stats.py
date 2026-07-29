"""KPI and rollup stats, Redis-cached (60s TTL)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from api.deps.cache import cached
from api.models.stats import ByCountryResponse, OverviewStats, TrafficByHourResponse
from api.services import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])

_overview_cached = cached("stats:overview")(stats_service.overview)
_traffic_by_hour_cached = cached("stats:traffic_by_hour")(stats_service.traffic_by_hour)
_by_country_cached = cached("stats:by_country")(stats_service.by_country)


@router.get(
    "/overview",
    response_model=OverviewStats,
    summary="KPI cards",
    description="Active flights, distinct countries, average altitude, and anomaly count.",
)
def overview(response: Response) -> OverviewStats:
    data, hit = _overview_cached()
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return OverviewStats(**data)


@router.get(
    "/traffic-by-hour",
    response_model=TrafficByHourResponse,
    summary="Hourly traffic timeseries",
    description=(
        "Real observed hourly flight counts (dbt's stg_traffic_by_hour) — "
        "every row is real, explicitly flagged is_synthetic=false."
    ),
)
def traffic_by_hour(response: Response) -> TrafficByHourResponse:
    data, hit = _traffic_by_hour_cached()
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return TrafficByHourResponse(**data)


@router.get(
    "/by-country",
    response_model=ByCountryResponse,
    summary="Top countries by flight activity",
)
def by_country(response: Response, limit: int = Query(10, ge=1, le=100)) -> ByCountryResponse:
    data, hit = _by_country_cached(limit)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return ByCountryResponse(**data)
