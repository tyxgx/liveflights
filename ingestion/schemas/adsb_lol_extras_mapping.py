"""adsb.lol raw `ac` row -> the extra fields the GRU trajectory model needs, on top of the
canonical 16-field FlightState (`adsb_lol_mapping.map_to_flight_state_dict`).

Kept in a SEPARATE module, not added to `map_to_flight_state_dict`, on purpose: that function's
return shape is the canonical contract used across ingestion, Spark schemas, dbt sources and the
API's Pydantic models (see repo CLAUDE.md, "Data contract") and must not change casually. This
module is purely additive — it is used only to build the rolling `live/history.json` object the
predict Lambda reads, never written to Firehose/bronze/the canonical live snapshot.

Zero third-party dependencies (same constraint as `adsb_lol_mapping.py`), so it can be vendored
into the ingest Lambda's plain zip unchanged — no numpy/pydantic here.

Field names and units match `ml/scratch/extract_globe_day.py`'s output columns exactly (metres,
m/s, degrees), so `ml/features.py`'s `window_x()`/`window_s()` can consume a live-built window
without any renaming — this is the train/serve consistency the shared feature module depends on.
"""

from __future__ import annotations

from typing import Any

FT_TO_M = 0.3048
KNOTS_TO_MPS = 0.514444
FPM_TO_MPS = 0.00508


def map_to_ml_fields(ac: dict[str, Any], now: float) -> dict[str, Any] | None:
    """One raw adsb.lol `ac` row -> the fields ml/features.py's window builders need.

    Returns None for rows the training pipeline would also have dropped: no position, on the
    ground, or missing both altitude readings (mirrors extract_globe_day.py's `keep` filter,
    minus the ADS-B-source-type and staleness checks the Lambda already effectively does by only
    polling adsb.lol live — there is no MLAT/TIS-B mixed in the way a day-long history file has).

    Also drops a row missing `gs` (ground speed) or `track` — real, seen live on some slow/hovering
    aircraft (helicopters, ultralights). The offline extractor never has this problem because it
    INTERPOLATES gs/track from neighbouring real trace points (up to 30 s away); the live predict
    Lambda has no such neighbour to interpolate from for a single per-minute reading, and
    `ml/features.py`'s core features do not mask these two (unlike the optional extras), so a
    missing gs/track would otherwise silently turn the whole predicted path into NaN — found by
    running `ml/scratch/local_live_test.py` against real traffic (23 of 346 evaluated predictions
    were NaN, all from aircraft with gs/track missing on every reading; see the journal entry for
    2026-09-28 "local live test finished, NaN bug").
    """
    lat, lon = ac.get("lat"), ac.get("lon")
    alt_baro = ac.get("alt_baro")
    if lat is None or lon is None or alt_baro == "ground" or alt_baro is None:
        return None
    if ac.get("gs") is None or ac.get("track") is None:
        return None
    icao = ac.get("hex")
    if not icao or "~" in icao:  # non-ICAO (TIS-B/mlat-only) address, same drop rule as training
        return None

    def m(key: str, mult: float = 1.0) -> float | None:
        v = ac.get(key)
        return v * mult if isinstance(v, (int, float)) else None

    raw_callsign = ac.get("flight")
    callsign = raw_callsign.strip() or None if isinstance(raw_callsign, str) else None

    return {
        "icao24": icao,
        "ts": now,
        "lat": float(lat),
        "lon": float(lon),
        "alt_baro_m": float(alt_baro) * FT_TO_M,
        "gs_ms": m("gs", KNOTS_TO_MPS),
        "track_deg": m("track"),
        "vrate_baro_ms": m("baro_rate", FPM_TO_MPS),
        "vrate_geom_ms": m("geom_rate", FPM_TO_MPS),
        "roll_deg": m("roll"),
        "track_rate": m("track_rate"),
        "true_heading": m("true_heading"),
        "mach": m("mach"),
        "tas_ms": m("tas", KNOTS_TO_MPS),
        "ias_ms": m("ias", KNOTS_TO_MPS),
        "wd_deg": m("wd"),
        "ws_ms": m("ws", KNOTS_TO_MPS),
        "nav_alt_mcp_m": m("nav_altitude_mcp", FT_TO_M),
        "nav_heading": m("nav_heading"),
        "category": ac.get("category") or "",
        "type_code": ac.get("t") or "",
        "callsign": callsign,
    }
