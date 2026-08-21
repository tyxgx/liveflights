"""Pure-Python adsb.lol -> canonical field mapping, zero third-party dependencies.

Split out from `adsb_lol_raw.py` specifically so this mapping logic can be
vendored into the AWS ingest Lambda zip unchanged (same reason
`ingestion/simulator.py` has no pydantic import at module load time) — the
Lambda fetches and maps adsb.lol data with plain dicts, no pydantic
validation layer. `adsb_lol_raw.py`'s `AdsbLolAircraft` pydantic model wraps
this same mapping for the locally-run producer, where stricter validation
before publishing onto Kafka is worth the dependency.

See `adsb_lol_raw.py` for the full contract docstring (fixture provenance,
the `origin_country` approximation caveat, the unverified on_ground mapping).
"""

from __future__ import annotations

from typing import Any

FT_TO_M = 0.3048
KNOTS_TO_MPS = 0.514444
FPM_TO_MPS = 0.00508

REGISTRATION_COUNTRY_PREFIXES: dict[str, str] = {
    "VT": "India",
    "N": "United States",
    "G": "United Kingdom",
    "D": "Germany",
    "F": "France",
    "HZ": "Saudi Arabia",
    "A6": "United Arab Emirates",
    "A7": "Qatar",
    "A4O": "Oman",
    "9V": "Singapore",
    "B": "China",
    "JA": "Japan",
    "HL": "South Korea",
    "9M": "Malaysia",
    "VH": "Australia",
    "S2": "Bangladesh",
    "AP": "Pakistan",
    "4R": "Sri Lanka",
    "9N": "Nepal",
    "A5": "Bhutan",
    "PK": "Indonesia",
    "SU": "Egypt",
    "TC": "Turkey",
    "EI": "Ireland",
    "PH": "Netherlands",
    "OE": "Austria",
    "HB": "Switzerland",
    "C": "Canada",
    # Added after the Aug 2026 India->Europe region switch: the adsb_lol
    # hub points (British Isles, France/Benelux, Central Europe,
    # Scandinavia, Iberia, Italy, Poland/Eastern Europe, Balkans/Greece)
    # cover several countries this table didn't have a prefix for yet,
    # which meant they fell through to "Unknown" — a real coverage gap,
    # not a fetch/parsing failure. ICAO nationality/registration marks.
    "SE": "Sweden",
    "LN": "Norway",
    "OY": "Denmark",
    "OH": "Finland",
    "OO": "Belgium",
    "EC": "Spain",
    "I": "Italy",
    "CS": "Portugal",
    "OK": "Czech Republic",
    "SX": "Greece",
    "SP": "Poland",
    "HA": "Hungary",
    "9A": "Croatia",
    "YR": "Romania",
    "LZ": "Bulgaria",
    "TF": "Iceland",
    "LX": "Luxembourg",
    "OM": "Slovakia",
    "S5": "Slovenia",
    "YU": "Serbia",
    "UR": "Ukraine",
    "YL": "Latvia",
    "LY": "Lithuania",
    "ES": "Estonia",
    "9H": "Malta",
    "5B": "Cyprus",
    "4X": "Israel",
}


def country_from_registration(reg: str | None) -> str:
    if not reg:
        return "Unknown"
    reg = reg.upper()
    for prefix in sorted(REGISTRATION_COUNTRY_PREFIXES, key=len, reverse=True):
        if reg.startswith(prefix):
            return REGISTRATION_COUNTRY_PREFIXES[prefix]
    return "Unknown"


def map_to_flight_state_dict(ac: dict[str, Any], now: float, source: str = "adsb_lol") -> dict:
    """Map one raw adsb.lol `ac` entry onto the canonical FlightState field set.

    Returns a plain dict, ready for `FlightState(**result)` — no pydantic
    involved, so this works unmodified inside the AWS Lambda zip.
    """
    alt_baro = ac.get("alt_baro")
    on_ground = alt_baro == "ground"
    baro_altitude = None if on_ground or alt_baro is None else float(alt_baro) * FT_TO_M
    gs = ac.get("gs")
    baro_rate = ac.get("baro_rate")
    alt_geom = ac.get("alt_geom")
    seen_pos = ac.get("seen_pos")
    seen = ac.get("seen")

    raw_callsign = ac.get("flight")
    callsign = raw_callsign.strip() or None if raw_callsign is not None else None

    return {
        "source": source,
        "icao24": ac["hex"],
        "callsign": callsign,
        "origin_country": country_from_registration(ac.get("r")),
        "time_position": int(now - seen_pos) if seen_pos is not None else None,
        "last_contact": int(now - seen) if seen is not None else int(now),
        "longitude": ac["lon"],
        "latitude": ac["lat"],
        "baro_altitude": baro_altitude,
        "on_ground": on_ground,
        "velocity": gs * KNOTS_TO_MPS if gs is not None else None,
        "true_track": ac.get("track"),
        "vertical_rate": baro_rate * FPM_TO_MPS if baro_rate is not None else None,
        "geo_altitude": alt_geom * FT_TO_M if alt_geom is not None else None,
        "squawk": ac.get("squawk"),
        "spi": bool(ac.get("spi")),
        "position_source": 0,
    }
