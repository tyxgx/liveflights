"""Replay a saved real OpenSky capture through the producer.

Useful for demos/tests that want real (not simulated) flight-state shapes
without hitting the live API. Loads a fixture saved in OpenSky's native
`{"time": ..., "states": [[...], ...]}` format and re-emits it, batch by
batch, exactly like `OpenSkyClient.fetch_states` would.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from ingestion.schemas.opensky_raw import OpenSkyStateVector

logger = logging.getLogger(__name__)

DEFAULT_FIXTURE_PATH = Path("tests/fixtures/opensky_real_sample.json")


class ReplaySource:
    """Streams a captured OpenSky response, one full batch per `fetch_states()` call."""

    def __init__(self, fixture_path: Path | str = DEFAULT_FIXTURE_PATH) -> None:
        self.fixture_path = Path(fixture_path)
        payload = json.loads(self.fixture_path.read_text())
        self._rows: list[list] = payload["states"]
        logger.info("Loaded %d states from replay fixture %s", len(self._rows), self.fixture_path)

    def fetch_states(self) -> list[dict]:
        """Return the full captured batch, parsed and validated per row."""
        states = []
        for row in self._rows:
            try:
                vector = OpenSkyStateVector.from_row(row)
            except (ValidationError, TypeError) as exc:
                logger.warning("Dropping malformed replay row: %s", exc)
                continue
            states.append(vector.model_dump(exclude={"sensors"}))
        return states
