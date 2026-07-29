"""Unit tests for FlightProducer's validation/DLQ routing (no real Kafka)."""

from unittest.mock import MagicMock, patch

from ingestion.config import IngestionSettings
from ingestion.producer import FlightProducer


def _producer() -> FlightProducer:
    cfg = IngestionSettings(kafka_bootstrap_servers="localhost:19092")
    with patch("ingestion.producer.Producer", return_value=MagicMock()):
        return FlightProducer(cfg)


def test_valid_state_goes_to_raw_topic():
    p = _producer()
    valid = {
        "icao24": "abc123",
        "callsign": "TST123",
        "origin_country": "Testland",
        "time_position": 1710000000,
        "last_contact": 1710000000,
        "longitude": 10.0,
        "latitude": 50.0,
        "baro_altitude": 10000.0,
        "on_ground": False,
        "velocity": 230.0,
        "true_track": 90.0,
        "vertical_rate": 0.0,
        "geo_altitude": 10010.0,
        "squawk": "1200",
        "spi": False,
        "position_source": 0,
    }
    p.publish_raw_states([valid], source="simulate")
    assert p.sent == 1
    assert p.dead_lettered == 0
    topics = [call.args[0] for call in p.producer.produce.call_args_list]
    assert topics == ["flights.raw"]


def test_invalid_state_goes_to_dlq():
    p = _producer()
    invalid = {"icao24": "abc123"}  # missing required fields
    p.publish_raw_states([invalid], source="simulate")
    assert p.sent == 0
    assert p.dead_lettered == 1
    topics = [call.args[0] for call in p.producer.produce.call_args_list]
    assert topics == ["flights.raw.dlq"]
