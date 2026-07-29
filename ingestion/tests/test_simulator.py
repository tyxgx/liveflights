"""Unit tests for the synthetic flight simulator."""

from ingestion.schemas.flight_state import FlightState
from ingestion.simulator import FlightSimulator


def test_tick_produces_one_state_per_aircraft():
    sim = FlightSimulator(aircraft_count=10, anomaly_rate=0.0, seed=42)
    states = sim.tick()
    assert len(states) == 10


def test_states_validate_against_flight_state_schema():
    sim = FlightSimulator(aircraft_count=25, anomaly_rate=0.0, seed=1)
    for raw in sim.tick():
        state = FlightState(source="simulate", **raw)
        assert state.icao24
        assert -90 <= (state.latitude or 0) <= 90
        assert -180 <= (state.longitude or 0) <= 180


def test_anomaly_rate_roughly_matches_config():
    sim = FlightSimulator(aircraft_count=500, anomaly_rate=0.5, seed=7)
    states = sim.tick()
    # With anomaly_rate=0.5, a meaningful fraction of states should exhibit
    # anomalous values (missing position, extreme velocity, etc).
    anomalous = sum(
        1
        for s in states
        if s["longitude"] is None
        or (s["velocity"] or 0) > 300
        or abs(s["vertical_rate"] or 0) > 10
        or s["squawk"] in {"7500", "7600", "7700"}
    )
    assert anomalous > 100


def test_aircraft_respawns_after_completing_route():
    sim = FlightSimulator(aircraft_count=1, anomaly_rate=0.0, seed=3)
    ac = sim.aircraft[0]
    ac.departed_at -= ac.duration_s  # force completion
    sim.tick()
    assert sim.aircraft[0] is not ac
