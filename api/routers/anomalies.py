"""Recent anomaly events (paginated) + on-demand scoring."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api.models.anomalies import AnomaliesResponse, PredictAnomalyRequest, PredictAnomalyResponse
from api.services import anomaly_service

router = APIRouter(prefix="/api", tags=["anomalies"])


@router.get(
    "/anomalies",
    response_model=AnomaliesResponse,
    summary="Recent anomaly events",
    description=(
        "Paginated, most recent first. Includes corridor context (nearest "
        "corridor, lateral distance, heading deviation, altitude z-score) "
        "and the contributing reason(s)."
    ),
)
def list_anomalies(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)
) -> AnomaliesResponse:
    result = anomaly_service.list_anomalies(page, page_size)
    return AnomaliesResponse(**result)


@router.post(
    "/predict/anomaly",
    response_model=PredictAnomalyResponse,
    summary="Score an arbitrary flight state",
    description=(
        "Runs the same rule flags as silver's enrichment plus corridor-based "
        "contextual ML scoring (Model 3) against an arbitrary state."
    ),
)
def predict_anomaly(request: PredictAnomalyRequest) -> PredictAnomalyResponse:
    result = anomaly_service.score_flight_state(request.model_dump())
    return PredictAnomalyResponse(**result)
