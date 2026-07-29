"""FastAPI app: liveflights REST + WebSocket API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.logging_config import configure_logging
from api.metrics import PrometheusMiddleware, metrics_endpoint
from api.middleware import RequestIDLoggingMiddleware
from api.routers import anomalies, corridors, flights, forecast, health, stats, ws
from api.services import live_store, models_loader

logger = logging.getLogger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Starting liveflights API")
    live_store.start()
    models_loader.load_all()
    yield
    logger.info("Stopping liveflights API")
    live_store.stop()


app = FastAPI(
    title="liveflights API",
    description="Real-time flight intelligence: live positions, stats, discovered air corridors, "
    "contextual anomaly detection, trajectory prediction, and traffic forecasting.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(RequestIDLoggingMiddleware)

app.include_router(health.router)
app.include_router(flights.router)
app.include_router(stats.router)
app.include_router(anomalies.router)
app.include_router(corridors.router)
app.include_router(forecast.router)
app.include_router(ws.router)


@app.get("/metrics", tags=["metrics"], summary="Prometheus metrics")
def metrics():
    return metrics_endpoint()
