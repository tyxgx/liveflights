"""Pure-Python enrichment logic shared by the silver Spark job and its tests.

Kept dependency-free of Spark so `tests/test_region_bucketing.py` can
validate the region bucketing directly against both real fixtures without
spinning up a SparkSession. `silver_stream.py` wraps these as Spark UDFs.
"""

from __future__ import annotations

# Rough bounding boxes, good enough for demo-grade regional bucketing.
# Order matters: first matching box wins — South Asia must be checked before
# the broader Asia catch-all, since India/Pakistan/Bangladesh/Sri Lanka/Nepal
# all fall inside Asia's box too.
_REGION_BOXES: list[tuple[str, float, float, float, float]] = [
    # name, lat_min, lat_max, lon_min, lon_max
    ("Europe", 34.0, 72.0, -25.0, 45.0),
    ("North America", 5.0, 72.0, -170.0, -50.0),
    ("South America", -56.0, 15.0, -82.0, -34.0),
    ("Africa", -35.0, 37.0, -20.0, 52.0),
    ("South Asia", 5.0, 38.0, 60.0, 100.0),
    ("Asia", -10.0, 55.0, 45.0, 150.0),
    ("Oceania", -50.0, 0.0, 110.0, 180.0),
]

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}
MAX_PLAUSIBLE_VELOCITY_MPS = 400.0
MAX_PLAUSIBLE_ALTITUDE_M = 15000.0
MAX_PLAUSIBLE_VERTICAL_RATE_MPS = 50.0
STALE_CONTACT_THRESHOLD_S = 60


def region_bucket(latitude: float | None, longitude: float | None) -> str:
    """Bucket a position into a coarse geographic region."""
    if latitude is None or longitude is None:
        return "Unknown"
    for name, lat_min, lat_max, lon_min, lon_max in _REGION_BOXES:
        if lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max:
            return name
    return "Other"


def geohash5(latitude: float | None, longitude: float | None) -> str | None:
    """5-character geohash, ~4.9km x 4.9km precision.

    Lazy-imports pygeohash: only the silver Spark job needs it, and this
    keeps it out of the API's dependency footprint, which only needs
    data_quality_flags() from this module.
    """
    if latitude is None or longitude is None:
        return None
    import pygeohash as pgh

    return pgh.encode(latitude, longitude, precision=5)


def flight_phase(on_ground: bool, vertical_rate: float | None) -> str:
    """ground / climb / cruise / descent, from on_ground + vertical_rate."""
    if on_ground:
        return "ground"
    if vertical_rate is None:
        return "cruise"
    if vertical_rate > 1.0:
        return "climb"
    if vertical_rate < -1.0:
        return "descent"
    return "cruise"


def speed_kmh(velocity_mps: float | None) -> float | None:
    if velocity_mps is None:
        return None
    return round(velocity_mps * 3.6, 2)


def altitude_ft(baro_altitude_m: float | None, geo_altitude_m: float | None) -> float | None:
    """Barometric altitude preferred; falls back to geometric altitude."""
    meters = baro_altitude_m if baro_altitude_m is not None else geo_altitude_m
    if meters is None:
        return None
    return round(meters * 3.28084, 1)


def data_quality_flags(
    latitude: float | None,
    longitude: float | None,
    time_position: int | None,
    last_contact: int | None,
    velocity: float | None,
    baro_altitude: float | None,
    vertical_rate: float | None,
    squawk: str | None,
) -> list[str]:
    """Flags describing known data-quality issues for a state vector.

    Thresholds correspond to the anomaly types the simulator injects
    (`ingestion/simulator.py`), so a healthy pipeline should flag roughly the
    simulator's configured anomaly rate.
    """
    flags: list[str] = []
    if latitude is None or longitude is None:
        flags.append("missing_position")
    if time_position is None or (
        last_contact is not None and time_position is not None
        and last_contact - time_position > STALE_CONTACT_THRESHOLD_S
    ):
        flags.append("stale_contact")
    if velocity is not None and (velocity > MAX_PLAUSIBLE_VELOCITY_MPS or velocity < 0):
        flags.append("implausible_speed")
    if baro_altitude is not None and (
        baro_altitude > MAX_PLAUSIBLE_ALTITUDE_M or baro_altitude < -500
    ):
        flags.append("implausible_altitude")
    if vertical_rate is not None and abs(vertical_rate) > MAX_PLAUSIBLE_VERTICAL_RATE_MPS:
        flags.append("implausible_vertical_rate")
    if squawk in EMERGENCY_SQUAWKS:
        flags.append("emergency_squawk")
    return flags
