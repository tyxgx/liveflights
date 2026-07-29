"""In-memory "live" flight state, fed by a background Kafka consumer.

Live positions never lived in Postgres (only gold's aggregates do) and
querying Delta/Spark per HTTP request would be far too slow for a hot
path, so the API maintains its own materialized view: a background thread
consumes `flights.raw` from `latest` (a fresh, unique consumer group, so it
only ever sees new messages — old states are stale by definition for a
"live" endpoint) and keeps the most recent state + a short history per
aircraft in memory.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque

import orjson
from confluent_kafka import Consumer

from api.config import settings

logger = logging.getLogger("api.live_store")

STALE_AFTER_SECONDS = 120
HISTORY_LEN = 5


class LiveFlightStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
        self._last_message_ts: float | None = None

    def update(self, state: dict) -> None:
        icao24 = state.get("icao24")
        if not icao24:
            return
        with self._lock:
            self._latest[icao24] = state
            self._history[icao24].append(state)
            self._last_message_ts = time.time()

    def get_all(
        self, bbox: tuple[float, float, float, float] | None = None, limit: int | None = None
    ) -> list[dict]:
        with self._lock:
            values = list(self._latest.values())
        if bbox:
            lamin, lomin, lamax, lomax = bbox
            values = [
                v
                for v in values
                if v.get("latitude") is not None
                and v.get("longitude") is not None
                and lamin <= v["latitude"] <= lamax
                and lomin <= v["longitude"] <= lomax
            ]
        if limit:
            values = values[:limit]
        return values

    def get_history(self, icao24: str) -> list[dict]:
        with self._lock:
            return list(self._history.get(icao24, []))

    def count(self) -> int:
        with self._lock:
            return len(self._latest)

    def countries(self) -> int:
        with self._lock:
            return len(
                {v["origin_country"] for v in self._latest.values() if v.get("origin_country")}
            )

    def avg_altitude_ft(self) -> float | None:
        with self._lock:
            alts = [
                v["baro_altitude"] * 3.28084
                for v in self._latest.values()
                if v.get("baro_altitude") is not None
            ]
        return sum(alts) / len(alts) if alts else None

    def is_receiving(self) -> bool:
        with self._lock:
            ts = self._last_message_ts
        return ts is not None and (time.time() - ts) < STALE_AFTER_SECONDS


store = LiveFlightStore()
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _consume_loop() -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"api-live-store-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([settings.kafka_topic_flights_raw])
    logger.info("Live-store Kafka consumer started (topic=%s)", settings.kafka_topic_flights_raw)
    try:
        while not _stop_event.is_set():
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            try:
                data = orjson.loads(msg.value())
                store.update(data)
            except Exception:
                logger.exception("Failed to process live-store message")
    finally:
        consumer.close()
        logger.info("Live-store Kafka consumer stopped")


def start() -> None:
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_consume_loop, daemon=True, name="live-store-consumer")
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
