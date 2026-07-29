"""Loads models 2 (trajectory) and 4 (forecast) at API startup.

Thin wrapper around ml/registry.py that also tracks *how* each model was
loaded (MLflow registry vs local pickle fallback), so /health can report it
and the fallback path can be verified end-to-end (stop MLflow, restart the
API, confirm it still serves predictions).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("api.models")


class ModelBundle:
    def __init__(self) -> None:
        self.trajectory_lat = None
        self.trajectory_lon = None
        self.forecast = None
        self.trajectory_source = "not_loaded"
        self.forecast_source = "not_loaded"

    @property
    def trajectory_ready(self) -> bool:
        return self.trajectory_lat is not None and self.trajectory_lon is not None

    @property
    def forecast_ready(self) -> bool:
        return self.forecast is not None


bundle = ModelBundle()


def load_all() -> None:
    _load_trajectory()
    _load_forecast()


def _load_trajectory() -> None:
    import mlflow

    from ml.config import settings as ml_settings
    from ml.registry import load_trajectory_models

    try:
        mlflow.set_tracking_uri(ml_settings.mlflow_tracking_uri)
        lat, lon = load_trajectory_models()
        bundle.trajectory_lat, bundle.trajectory_lon = lat, lon
        # ml.registry falls back internally and only raises if BOTH paths
        # fail; distinguish which path succeeded via a lightweight probe.
        bundle.trajectory_source = "mlflow_registry" if hasattr(lat, "metadata") else "local_pickle"
        logger.info("Trajectory models loaded via %s", bundle.trajectory_source)
    except Exception:
        logger.exception("Trajectory models failed to load from both MLflow and local pickle")
        bundle.trajectory_source = "unavailable"


def _load_forecast() -> None:
    import mlflow

    from ml.config import settings as ml_settings
    from ml.registry import load_forecast_model

    try:
        mlflow.set_tracking_uri(ml_settings.mlflow_tracking_uri)
        model = load_forecast_model()
        bundle.forecast = model
        bundle.forecast_source = "mlflow_registry" if hasattr(model, "metadata") else "local_pickle"
        logger.info("Forecast model loaded via %s", bundle.forecast_source)
    except Exception:
        logger.exception("Forecast model failed to load from both MLflow and local pickle")
        bundle.forecast_source = "unavailable"
