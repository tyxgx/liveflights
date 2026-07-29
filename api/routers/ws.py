"""WebSocket: pushes current live positions every WS_PUSH_INTERVAL_SECONDS."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.config import settings
from api.services.live_store import store

logger = logging.getLogger("api.ws")
router = APIRouter(tags=["websocket"])


@router.websocket("/ws/flights")
async def ws_flights(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            flights = store.get_all(limit=1000)
            await websocket.send_json({"count": len(flights), "flights": flights})
            await asyncio.sleep(settings.ws_push_interval_seconds)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
