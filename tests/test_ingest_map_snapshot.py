"""The ingest Lambda's browser-facing map snapshot: slim, rounded, gzipped, API-shaped."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

HANDLER = Path(__file__).resolve().parents[1] / "infra/terraform/lambda_ingest/handler.py"


def _load(monkeypatch):
    monkeypatch.setenv("FIREHOSE_STREAM_NAME", "x")
    monkeypatch.setenv("LAKE_BUCKET_NAME", "lake")
    monkeypatch.setenv("SITE_BUCKET_NAME", "site")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("ingest_handler_under_test", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FLIGHT = {
    "source": "adsb_lol", "icao24": "4ca5c6", "callsign": "EIN952", "origin_country": "Ireland",
    "time_position": 1, "last_contact": 2, "longitude": -8.698975123, "latitude": 54.200317456,
    "baro_altitude": 9479.28, "on_ground": False, "velocity": 253.3122256, "true_track": 116.41,
    "vertical_rate": -9.7536, "geo_altitude": 9936.48, "squawk": "5142", "spi": False,
    "position_source": 0, "ingest_ts": "t",
}


def test_map_snapshot_is_gzipped_slim_and_api_shaped(monkeypatch):
    h = _load(monkeypatch)
    with mock.patch.object(h, "s3") as s3:
        h._write_map_snapshot([dict(FLIGHT)], "2026-10-04T10:00:00+00:00")
    kw = s3.put_object.call_args.kwargs
    assert kw["Bucket"] == "site" and kw["Key"] == "live/map.json"
    assert kw["ContentEncoding"] == "gzip" and kw["ContentType"] == "application/json"
    assert "max-age" in kw["CacheControl"]
    doc = json.loads(gzip.decompress(kw["Body"]))
    assert set(doc) == {"count", "updated_at", "flights"} and doc["count"] == 1
    f = doc["flights"][0]
    for dropped in ("time_position", "last_contact", "geo_altitude", "spi", "position_source"):
        assert dropped not in f
    for kept in ("icao24", "callsign", "origin_country", "latitude", "longitude", "baro_altitude",
                 "on_ground", "velocity", "true_track", "vertical_rate", "squawk", "source",
                 "ingest_ts"):
        assert kept in f
    assert f["latitude"] == 54.20032 and f["longitude"] == -8.69898 and f["velocity"] == 253.3


def test_slim_keeps_nulls_and_does_not_mutate_input(monkeypatch):
    h = _load(monkeypatch)
    src = dict(FLIGHT, baro_altitude=None)
    out = h._slim_for_map(src)
    assert out["baro_altitude"] is None
    assert "geo_altitude" in src  # canonical state untouched
