"""OpenSky historical sample-archive -> canonical field mapping.

Source: https://s3.opensky-network.org/data-samples/states/ — OpenSky's free,
no-login public sample of its full historical state-vector database. Covers
one full day (all 24 hourly files) per week, extracted every Tuesday night
for the preceding Monday, from 2020-05-25 through 2022-06-27. Global (no
bounding box), ADS-B only (confirmed by the archive's own README).

Column names differ from the live `/states/all` REST response mapped by
`opensky_raw.py` (this is a CSV export of the same underlying database, not
the same wire format), so this is a separate mapping rather than a reuse of
`OpenSkyStateVector`:

  archive column    -> FlightState field
  time               (unused directly; see ingest_ts below)
  icao24             -> icao24
  lat                -> latitude
  lon                -> longitude
  velocity           -> velocity
  heading            -> true_track
  vertrate           -> vertical_rate
  callsign           -> callsign
  onground           -> on_ground
  alert              -> (dropped, no FlightState equivalent)
  spi                -> spi
  squawk             -> squawk
  baroaltitude       -> baro_altitude
  geoaltitude        -> geo_altitude
  lastposupdate      -> time_position
  lastcontact        -> last_contact

Two deliberate simplifications, not bugs:
  - `origin_country` is always "Unknown" — the archive has no registration
    field to derive it from (unlike adsb.lol's `r` field, see
    `adsb_lol_mapping.py`), and none of the 4 ML models use origin_country
    (corridors/trajectory/anomaly key off lat/lon/velocity/track/altitude;
    the India-domestic proxy discussed for live data uses callsign prefix).
  - `position_source` is fixed to 0 (ADS-B) — the archive's own README
    states this dataset is ADS-B-only, so this isn't a guess.

`ingest_ts` is set to the row's own historical timestamp (not wall-clock
"now"), so downstream hour-bucket aggregations in silver correctly bucket
these rows into 2022, not whatever day the backfill script happens to run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _is_nan(value: Any) -> bool:
    # pandas represents missing numeric/string cells as float NaN; NaN is
    # truthy in Python (`nan or "x"` stays nan) and fails pydantic's
    # ge/le range checks outright, so every optional field needs this
    # explicit check rather than relying on `is None` / falsy-ness.
    return isinstance(value, float) and value != value


def _num_or_none(value: Any) -> float | None:
    return None if value is None or _is_nan(value) else value


def _squawk_to_str(value: Any) -> str | None:
    # pandas infers `squawk` as int/float (values look numeric, e.g. 5001),
    # but FlightState.squawk is a string — cast, dropping a spurious ".0"
    # from float-typed columns (introduced whenever the column has any nulls).
    if value is None or _is_nan(value):
        return None
    return str(int(value)) if isinstance(value, float) else str(value)


def map_historical_row_to_flight_state_dict(row: dict[str, Any]) -> dict:
    """Map one row (dict, e.g. from a pandas/Spark row) of the OpenSky
    historical archive's state-vector CSV onto the canonical FlightState
    field set. Returns a plain dict, ready for `FlightState(**result)`.
    """
    last_contact = int(row["lastcontact"])
    raw_time_position = row.get("lastposupdate")
    time_position = (
        int(raw_time_position)
        if raw_time_position is not None and not _is_nan(raw_time_position)
        else None
    )
    raw_callsign = row.get("callsign")
    callsign = (
        None
        if raw_callsign is None or _is_nan(raw_callsign)
        else str(raw_callsign).strip() or None
    )

    return {
        "source": "opensky_historical",
        "icao24": row["icao24"],
        "callsign": callsign,
        "origin_country": "Unknown",
        "time_position": time_position,
        "last_contact": last_contact,
        "longitude": _num_or_none(row.get("lon")),
        "latitude": _num_or_none(row.get("lat")),
        "baro_altitude": _num_or_none(row.get("baroaltitude")),
        "on_ground": bool(row.get("onground", False)),
        "velocity": _num_or_none(row.get("velocity")),
        "true_track": _num_or_none(row.get("heading")),
        "vertical_rate": _num_or_none(row.get("vertrate")),
        "geo_altitude": _num_or_none(row.get("geoaltitude")),
        "squawk": _squawk_to_str(row.get("squawk")),
        "spi": bool(row.get("spi", False)),
        "position_source": 0,
        "ingest_ts": datetime.fromtimestamp(last_contact, tz=UTC).isoformat(),
    }
