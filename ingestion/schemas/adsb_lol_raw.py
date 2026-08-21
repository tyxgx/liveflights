"""Raw adsb.lol `/v2/lat/{lat}/lon/{lon}/dist/{nm}` contract, derived from a live capture.

Field presence verified against a real capture, 2026-07-29, India-centered
query (`lat=21, lon=78, dist=250`, 25 aircraft) —
`tests/fixtures/adsb_lol_real_sample.json`. adsb.lol (and the tar1090/readsb
family of feeders it's built on) is a much looser contract than OpenSky's
fixed 17-column array: every field below except `hex`, `lat`, `lon` is
optional in practice (see the field-presence counts in the capture), so
nothing here is asserted non-null the way OpenSky's `last_contact` is.

**Not verified from this capture**: no grounded aircraft appeared in either
the India-wide (250nm) or Delhi-airport (15nm) captures taken while building
this adapter — real traffic just didn't include one at capture time. The
`on_ground` mapping (`alt_baro == "ground"`) follows the documented
tar1090/readsb convention used across this API family, not something this
capture proves directly. Flagged here rather than silently assumed solid.

**`origin_country` is an approximation**, not equivalent to OpenSky's. This
API doesn't return a country field at all — OpenSky derives it from the
ICAO24 24-bit address allocation blocks (a real ITU/ICAO-maintained table).
This adapter instead maps the aircraft *registration* prefix (`r`, e.g.
`VT-NHI` -> India) via `adsb_lol_mapping.REGISTRATION_COUNTRY_PREFIXES`,
which is close in practice for scheduled airline traffic but is a different,
looser signal. Unmapped prefixes and aircraft with no `r` field fall back to
`"Unknown"`.

The actual unit-conversion/field-mapping logic lives in
`adsb_lol_mapping.py` (pure Python, no pydantic) so it can be vendored into
the AWS ingest Lambda zip unchanged — this module wraps it with pydantic
validation for the locally-run producer, which publishes onto Kafka and
benefits from failing loudly on a malformed row before that happens.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ingestion.schemas.adsb_lol_mapping import map_to_flight_state_dict
from ingestion.schemas.flight_state import FlightState, Source


class AdsbLolAircraft(BaseModel):
    """One entry of adsb.lol's `ac` array, typed per the live capture.

    Almost every field is optional — this feed has no fixed schema contract
    the way OpenSky's positional array does.
    """

    hex: str = Field(..., description="ICAO24 address, hex string, lowercase")
    flight: str | None = Field(None, description="Callsign, space-padded")
    r: str | None = Field(None, description="Registration/tail number, e.g. VT-NHI")
    alt_baro: int | str | None = Field(None, description="Feet, or the literal string 'ground'")
    alt_geom: float | None = Field(None, description="Feet")
    gs: float | None = Field(None, description="Ground speed, knots")
    track: float | None = Field(None, description="Degrees")
    baro_rate: float | None = Field(None, description="Feet/minute")
    squawk: str | None = None
    spi: int | None = Field(None, description="0/1, special purpose indicator")
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    seen_pos: float | None = Field(None, description="Seconds since last position report")
    seen: float | None = Field(None, description="Seconds since any last message")

    def to_flight_state(self, now: float, source: Source = "adsb_lol") -> FlightState:
        """Map onto the canonical `FlightState`, given the response's `now` (unix seconds)."""
        mapped = map_to_flight_state_dict(self.model_dump(), now=now, source=source)
        return FlightState(**mapped)
