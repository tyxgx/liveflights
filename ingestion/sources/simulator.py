"""Simulator adapter: wraps `FlightSimulator.tick()` behind the `FlightSource` contract."""

from __future__ import annotations

from ingestion.config import IngestionSettings
from ingestion.simulator import FlightSimulator


class SimulatorAdapter:
    def __init__(self, settings: IngestionSettings) -> None:
        self._sim = FlightSimulator(
            aircraft_count=settings.simulator_aircraft_count,
            anomaly_rate=settings.simulator_anomaly_rate,
            region=settings.region,
        )

    def fetch_states(self) -> list[dict]:
        return self._sim.tick()
