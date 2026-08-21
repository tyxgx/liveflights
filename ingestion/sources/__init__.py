"""Pluggable flight-data source adapters.

Every adapter exposes the same `fetch_states() -> list[dict]` contract —
dicts keyed by the canonical `FlightState` field names, ready to validate
against it unchanged. Adding a new provider means adding one adapter here,
not touching the producer, the Lambda, or any downstream layer.

Selected via `SOURCE` (falls back to the legacy `INGEST_MODE` name for the
two adapters that predate this registry, so existing `.env` files and the
`--mode` CLI flag keep working unchanged).
"""

from __future__ import annotations

from ingestion.sources.base import FlightSource

_REGISTRY: dict[str, type[FlightSource]] = {}


def register(name: str, adapter_cls: type[FlightSource]) -> None:
    _REGISTRY[name] = adapter_cls


def get_source(name: str, **kwargs: object) -> FlightSource:
    """Instantiate the named adapter. Raises ValueError for an unknown name."""
    try:
        adapter_cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown SOURCE {name!r}, expected one of: {sorted(_REGISTRY)}"
        ) from None
    return adapter_cls(**kwargs)


# Import side effects register each adapter with the module-level registry.
from ingestion.sources import adsb_lol as _adsb_lol  # noqa: E402
from ingestion.sources import opensky as _opensky  # noqa: E402
from ingestion.sources import simulator as _simulator  # noqa: E402

register("opensky", _opensky.OpenSkyAdapter)
register("adsb_lol", _adsb_lol.AdsbLolAdapter)
register("simulate", _simulator.SimulatorAdapter)

__all__ = ["FlightSource", "get_source", "register"]
