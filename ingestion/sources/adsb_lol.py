"""adsb.lol adapter: community ADS-B aggregator, reachable from AWS Lambda (unlike OpenSky).

Uses stdlib `urllib` only (no `httpx`) so this adapter can be vendored into
the AWS ingest Lambda zip unchanged, the same way `ingestion/simulator.py`
already is — see `infra/terraform/lambda_ingest.tf`.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from ingestion.config import IngestionSettings
from ingestion.schemas.adsb_lol_raw import AdsbLolAircraft

BASE_URL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"

# Center point + radius (nm) per region — adsb.lol takes a point + radius,
# not a bounding box, so this is a distinct notion from OpenSky's REGION_BBOXES.
REGION_POINTS: dict[str, tuple[float, float, int]] = {
    "india": (21.0, 78.0, 250),
    "europe": (50.5, 12.5, 300),
    "us": (39.5, -98.5, 400),
    "all": (20.0, 30.0, 400),
}


class AdsbLolAdapter:
    def __init__(self, settings: IngestionSettings) -> None:
        self._region = settings.region.lower()

    def fetch_states(self) -> list[dict]:
        lat, lon, dist = REGION_POINTS.get(self._region, REGION_POINTS["india"])
        url = BASE_URL.format(lat=lat, lon=lon, dist=dist)
        req = urllib.request.Request(url, headers={"User-Agent": "liveflights/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed adsb.lol URL
            payload: dict[str, Any] = json.loads(resp.read())

        now_ms = payload.get("now")
        now = (now_ms / 1000) if now_ms else time.time()  # response's "now" is unix milliseconds
        states = []
        for row in payload.get("ac") or []:
            try:
                aircraft = AdsbLolAircraft(**row)
            except Exception:  # noqa: BLE001 - malformed row from a third-party feed, skip it
                continue
            state = aircraft.to_flight_state(now=now)
            states.append(state.model_dump(mode="json", exclude={"source", "ingest_ts"}))
        return states
