"""OpenSky Network REST client.

Uses OAuth2 client-credentials when OPENSKY_CLIENT_ID/SECRET are configured.
Falls back to anonymous (unauthenticated) polling at a longer interval if
credentials are missing or the token request fails — the pipeline should
never be blocked on credentials.
"""

from __future__ import annotations

import logging
import time

import httpx
from pydantic import ValidationError

from ingestion.config import IngestionSettings
from ingestion.schemas.opensky_raw import OpenSkyStateVector

logger = logging.getLogger(__name__)

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
STATES_URL = "https://opensky-network.org/api/states/all"


class OpenSkyClient:
    """Thin wrapper around the OpenSky /states/all endpoint."""

    def __init__(self, settings: IngestionSettings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self.authenticated = False

    def _fetch_token(self) -> str | None:
        if not self.settings.opensky_client_id or not self.settings.opensky_client_secret:
            return None
        try:
            resp = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.opensky_client_id,
                    "client_secret": self.settings.opensky_client_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token_expires_at = time.time() + payload.get("expires_in", 1800) - 30
            return payload["access_token"]
        except Exception as exc:  # noqa: BLE001 - any auth failure falls back to anonymous
            logger.warning("OpenSky OAuth2 token request failed, falling back to anon: %s", exc)
            return None


    def _ensure_token(self) -> str | None:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        token = self._fetch_token()
        self._token = token
        self.authenticated = token is not None
        return token

    def fetch_states(self) -> list[dict]:
        """Fetch current states within the configured bounding box.

        Returns raw dicts keyed by the canonical field names, ready to be
        validated against `FlightState`. Missing values in the OpenSky
        payload become `None`.
        """
        token = self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        lamin, lomin, lamax, lomax = self.settings.resolved_bbox()
        params = {
            "lamin": lamin,
            "lomin": lomin,
            "lamax": lamax,
            "lomax": lomax,
        }
        resp = httpx.get(STATES_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        states = []
        for row in payload.get("states") or []:
            try:
                vector = OpenSkyStateVector.from_row(row)
            except (ValidationError, TypeError) as exc:
                logger.warning("Dropping malformed OpenSky row: %s", exc)
                continue
            states.append(vector.model_dump(exclude={"sensors"}))
        return states

    @property
    def poll_interval_seconds(self) -> int:
        return (
            self.settings.poll_interval_seconds
            if self.authenticated
            else self.settings.opensky_anon_poll_interval_seconds
        )
