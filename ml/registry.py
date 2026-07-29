"""Load-with-fallback for models 2 and 4, used by the API in P6.

Tries the MLflow Model Registry's Production stage first; falls back to a
local pickle in ml/artifacts/ if MLflow is unreachable or nothing has been
promoted yet, so the API can still start in a fully offline demo.
"""

from __future__ import annotations

import logging

import joblib
import mlflow

from ml.config import settings

logger = logging.getLogger("ml.registry")


def _load_from_registry(model_name: str):
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    return mlflow.pyfunc.load_model(f"models:/{model_name}/Production")


def load_trajectory_models():
    """Returns (model_delta_lat, model_delta_lon)."""
    try:
        return (
            _load_from_registry(f"{settings.mlflow_trajectory_model_name}-lat"),
            _load_from_registry(f"{settings.mlflow_trajectory_model_name}-lon"),
        )
    except Exception as exc:
        logger.warning(
            "MLflow registry load failed for trajectory models (%s), using local pickle fallback",
            exc,
        )
        return (
            joblib.load(f"{settings.artifacts_dir}/trajectory_model_lat.pkl"),
            joblib.load(f"{settings.artifacts_dir}/trajectory_model_lon.pkl"),
        )


def load_forecast_model():
    try:
        return _load_from_registry(settings.mlflow_forecast_model_name)
    except Exception as exc:
        logger.warning(
            "MLflow registry load failed for forecast model (%s), using local pickle fallback", exc
        )
        return joblib.load(f"{settings.artifacts_dir}/traffic_forecast_model.pkl")
