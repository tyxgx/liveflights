"""Validates silver-layer region bucketing against real OpenSky fixtures.

Europe-bbox records must overwhelmingly bucket to "Europe", US-bbox records
to "North America", and India-bbox records to "South Asia" — this is the one
test P3 explicitly calls out as mandatory, since a wrong bounding box here
would silently mis-tag every downstream regional rollup.
"""

from __future__ import annotations

import json
from pathlib import Path

from streaming.utils.enrich import region_bucket

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Column indices in the raw OpenSky row: latitude=6, longitude=5.
_LAT_IDX, _LON_IDX = 6, 5

# A tiny fraction of edge-of-bbox aircraft can legitimately fall just
# outside our coarse region boxes (e.g. mid-Atlantic ferry flights); require
# an overwhelming majority rather than 100%.
MIN_CORRECT_BUCKET_FRACTION = 0.9


def _bucket_fractions(fixture_name: str) -> dict[str, float]:
    payload = json.loads((FIXTURES_DIR / fixture_name).read_text())
    rows = payload["states"]
    buckets = [region_bucket(row[_LAT_IDX], row[_LON_IDX]) for row in rows]
    total = len(buckets)
    counts: dict[str, int] = {}
    for b in buckets:
        counts[b] = counts.get(b, 0) + 1
    return {name: count / total for name, count in counts.items()}


def test_europe_fixture_buckets_to_europe():
    fractions = _bucket_fractions("opensky_real_sample.json")
    assert fractions.get("Europe", 0.0) >= MIN_CORRECT_BUCKET_FRACTION, (
        f"Expected >= {MIN_CORRECT_BUCKET_FRACTION:.0%} of Europe-bbox records to "
        f"bucket as 'Europe', got distribution: {fractions}"
    )


def test_us_fixture_buckets_to_north_america():
    fractions = _bucket_fractions("opensky_real_sample_us.json")
    assert fractions.get("North America", 0.0) >= MIN_CORRECT_BUCKET_FRACTION, (
        f"Expected >= {MIN_CORRECT_BUCKET_FRACTION:.0%} of US-bbox records to "
        f"bucket as 'North America', got distribution: {fractions}"
    )


def test_india_fixture_buckets_to_south_asia():
    fractions = _bucket_fractions("opensky_real_sample_india.json")
    assert fractions.get("South Asia", 0.0) >= MIN_CORRECT_BUCKET_FRACTION, (
        f"Expected >= {MIN_CORRECT_BUCKET_FRACTION:.0%} of India-bbox records to "
        f"bucket as 'South Asia', got distribution: {fractions}"
    )


def test_unknown_position_buckets_to_unknown():
    assert region_bucket(None, None) == "Unknown"
    assert region_bucket(50.0, None) == "Unknown"


def test_out_of_range_position_buckets_to_other():
    # Southern Ocean, not covered by any defined region box.
    assert region_bucket(-70.0, 100.0) == "Other"
