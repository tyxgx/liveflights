"""Raw OpenSky `/states/all` contract, derived directly from a live capture.

Field order, types, and nullability below were verified against two
independent real anonymous API responses, captured 2026-07-28:
  - tests/fixtures/opensky_real_sample.json    (Europe bbox, 1234 states)
  - tests/fixtures/opensky_real_sample_us.json (continental US bbox, 879 states)
Checking two different bboxes/times guards against mistaking "happened to be
non-null in one snapshot" for a guaranteed contract — see
`tests/test_schema_contract.py::test_non_optional_fields_are_never_null_in_any_real_fixture`,
which runs across both and fails loudly if a field needs widening. Do not
hand-edit field order without re-verifying against a fresh capture; OpenSky
documents this as a positional array, not a JSON object, so an off-by-one
here silently corrupts every downstream field.

Observed in the 2026-07-28 Europe capture (index: field, nulls/1234, dtype(s)):
  0  icao24            0 nulls   str
  1  callsign          0 nulls   str   (right-padded with spaces; blank/None-able per API docs)
  2  origin_country    0 nulls   str
  3  time_position     0 nulls   int   (nullable per API docs for craft w/o position report)
  4  last_contact      0 nulls   int
  5  longitude         0 nulls   float (nullable per API docs)
  6  latitude          0 nulls   float (nullable per API docs)
  7  baro_altitude   145 nulls   float | int
  8  on_ground         0 nulls   bool
  9  velocity          0 nulls   float | int (nullable per API docs)
  10 true_track        0 nulls   float | int (nullable per API docs)
  11 vertical_rate   154 nulls   float | int
  12 sensors        1234 nulls   list[int] (only populated for the requesting user's own receivers)
  13 geo_altitude    185 nulls   float | int
  14 squawk          205 nulls   str
  15 spi               0 nulls   bool
  16 position_source   0 nulls   int
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ingestion.schemas.flight_state import FlightState, Source

# Positional order of the OpenSky /states/all row format. Index into this
# list is the field's position in the raw JSON array.
RAW_FIELD_ORDER: tuple[str, ...] = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
)


class OpenSkyStateVector(BaseModel):
    """One row of OpenSky's `/states/all` response, typed per the live capture."""

    icao24: str = Field(..., description="Unique ICAO 24-bit address, index 0")
    callsign: str | None = Field(None, description="Callsign, may be blank/whitespace, index 1")
    origin_country: str = Field(..., description="Country inferred from ICAO24 address, index 2")
    time_position: int | None = Field(None, description="Unix time of last position report, idx 3")
    last_contact: int = Field(..., description="Unix time of last update of any kind, index 4")
    longitude: float | None = Field(None, ge=-180, le=180, description="Degrees, index 5")
    latitude: float | None = Field(None, ge=-90, le=90, description="Degrees, index 6")
    baro_altitude: float | None = Field(None, description="Barometric altitude, meters, index 7")
    on_ground: bool = Field(..., description="index 8")
    velocity: float | None = Field(None, ge=0, description="Ground speed, m/s, index 9")
    true_track: float | None = Field(None, ge=0, le=360, description="Degrees, index 10")
    vertical_rate: float | None = Field(None, description="m/s, index 11")
    sensors: list[int] | None = Field(None, description="Receiver serials, index 12 (auth-only)")
    geo_altitude: float | None = Field(None, description="Geometric altitude, meters, index 13")
    squawk: str | None = Field(None, description="Transponder code, index 14")
    spi: bool = Field(..., description="Special purpose indicator, index 15")
    position_source: int = Field(..., description="0=ADS-B 1=ASTERIX 2=MLAT 3=FLARM, index 16")

    @classmethod
    def from_row(cls, row: list) -> OpenSkyStateVector:
        """Parse one positional row from `/states/all` into a typed model."""
        return cls(**dict(zip(RAW_FIELD_ORDER, row, strict=True)))

    def to_flight_state(self, source: Source = "opensky") -> FlightState:
        """Map onto the canonical `FlightState` used by every downstream layer.

        Drops `sensors` (not part of the canonical contract) and stamps
        ingest metadata.
        """
        data = self.model_dump(exclude={"sensors"})
        return FlightState(source=source, **data)
