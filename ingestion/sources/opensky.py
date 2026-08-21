"""OpenSky adapter: wraps the existing `OpenSkyClient` behind the `FlightSource` contract.

**Known unreachable from AWS Lambda** — connections to both
`auth.opensky-network.org` and `opensky-network.org` time out at the TCP
level from AWS egress IPs, while general internet egress from the same
Lambda works fine. Confirmed via a one-shot diagnostic invocation; see
`docs/aws-architecture.md`. This adapter still works from any non-AWS
network (local dev, most other clouds) — it's registered here for parity
and for local `--mode opensky` runs, not removed just because AWS blocks it.
"""

from __future__ import annotations

from ingestion.config import IngestionSettings
from ingestion.opensky import OpenSkyClient


class OpenSkyAdapter:
    def __init__(self, settings: IngestionSettings) -> None:
        self._client = OpenSkyClient(settings)

    def fetch_states(self) -> list[dict]:
        return self._client.fetch_states()

    @property
    def poll_interval_seconds(self) -> int:
        return self._client.poll_interval_seconds
