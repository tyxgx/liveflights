"""Common interface every flight-data source adapter implements."""

from __future__ import annotations

from typing import Protocol


class FlightSource(Protocol):
    """Anything that can produce a batch of raw flight-state dicts.

    `fetch_states()` returns dicts keyed by the canonical `FlightState`
    field names (`icao24`, `callsign`, ... — see
    `ingestion/schemas/flight_state.py`), so callers can pass the result
    straight into `FlightState(source=..., **raw)` without per-adapter
    translation. Adapters do the mapping from their provider's native
    shape internally.
    """

    def fetch_states(self) -> list[dict]: ...
