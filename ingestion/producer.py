"""Kafka producer: polls a source (simulator or OpenSky) and publishes
validated flight states onto `flights.raw`. Records that fail schema
validation are published to `flights.raw.dlq` with the error attached.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import orjson
from confluent_kafka import Producer
from pydantic import ValidationError

from ingestion.config import IngestionSettings, settings
from ingestion.opensky import OpenSkyClient
from ingestion.replay import DEFAULT_FIXTURE_PATH, ReplaySource
from ingestion.schemas.flight_state import FlightState
from ingestion.simulator import FlightSimulator

logger = logging.getLogger("ingestion.producer")


def _delivery_callback(err, msg) -> None:
    if err is not None:
        logger.error("Delivery failed for %s: %s", msg.topic(), err)


class FlightProducer:
    """Wraps a confluent_kafka Producer with schema validation + DLQ routing."""

    def __init__(self, cfg: IngestionSettings) -> None:
        self.cfg = cfg
        self.producer = Producer({"bootstrap.servers": cfg.kafka_bootstrap_servers})
        self.sent = 0
        self.dead_lettered = 0

    def _publish(self, topic: str, key: str, payload: dict) -> None:
        self.producer.produce(
            topic,
            key=key.encode(),
            value=orjson.dumps(payload),
            callback=_delivery_callback,
        )

    def publish_raw_states(self, raw_states: list[dict], source: str) -> None:
        for raw in raw_states:
            try:
                state = FlightState(source=source, **raw)
            except ValidationError as exc:
                self.dead_lettered += 1
                self._publish(
                    self.cfg.kafka_topic_flights_dlq,
                    key=str(raw.get("icao24", "unknown")),
                    payload={"raw": raw, "error": str(exc), "source": source},
                )
                continue
            self.sent += 1
            self._publish(
                self.cfg.kafka_topic_flights_raw,
                key=state.icao24,
                payload=state.model_dump(mode="json"),
            )
        self.producer.poll(0)

    def flush(self) -> None:
        self.producer.flush(10)


def run(mode: str, once: bool, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
    log_format = "%(asctime)s %(levelname)s %(name)s %(message)s"
    logging.basicConfig(level=settings.log_level, format=log_format)
    producer = FlightProducer(settings)

    if mode == "simulate":
        sim = FlightSimulator(
            settings.simulator_aircraft_count,
            settings.simulator_anomaly_rate,
            region=settings.region,
        )
        interval = settings.poll_interval_seconds
        source = "simulate"

        def poll() -> list[dict]:
            return sim.tick()
    elif mode == "replay":
        replay = ReplaySource(fixture_path)
        interval = settings.poll_interval_seconds
        source = "opensky"

        def poll() -> list[dict]:
            return replay.fetch_states()
    else:
        client = OpenSkyClient(settings)
        source = "opensky"

        def poll() -> list[dict]:
            return client.fetch_states()

        interval = client.poll_interval_seconds

    logger.info("Starting producer in mode=%s interval=%ss", mode, interval)
    while True:
        started = time.time()
        try:
            raw_states = poll()
        except Exception:
            logger.exception("Poll failed, will retry next interval")
            raw_states = []
        if raw_states:
            producer.publish_raw_states(raw_states, source)
            logger.info(
                "Published batch: sent=%d dead_lettered=%d total_sent=%d total_dlq=%d",
                len(raw_states),
                0,
                producer.sent,
                producer.dead_lettered,
            )
        producer.flush()
        if once:
            break
        elapsed = time.time() - started
        time.sleep(max(0.0, interval - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser(description="liveflights ingestion producer")
    parser.add_argument(
        "--mode", choices=["simulate", "opensky", "replay"], default=settings.ingest_mode
    )
    parser.add_argument("--once", action="store_true", help="Publish a single batch and exit")
    parser.add_argument(
        "--fixture-path",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="Path to a captured OpenSky JSON response, for --mode replay",
    )
    args = parser.parse_args()
    run(args.mode, args.once, args.fixture_path)


if __name__ == "__main__":
    main()
