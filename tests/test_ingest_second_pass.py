"""adsb.lol fan-out: a point that fails in the first pass gets one slower retry pass."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HANDLER = Path(__file__).resolve().parents[1] / "infra/terraform/lambda_ingest/handler.py"

P1, P2, P3 = ({"lat": float(i), "lon": 0.0, "dist": 250} for i in (1, 2, 3))


def _state(icao: str, tag: str = "a") -> dict:
    return {"icao24": icao, "tag": tag}


@pytest.fixture
def h(monkeypatch):
    monkeypatch.setenv("FIREHOSE_STREAM_NAME", "x")
    monkeypatch.setenv("LAKE_BUCKET_NAME", "lake")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("ingest_second_pass_under_test", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ADSB_LOL_POINTS", [P1, P2, P3])
    monkeypatch.setattr(mod, "ADSB_LOL_STAGGER_SECONDS", 0.0)
    monkeypatch.setattr(mod, "ADSB_LOL_SECOND_PASS_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(mod, "ADSB_LOL_SECOND_PASS_STAGGER_SECONDS", 0.0)
    return mod


def _fake(results: dict):
    """results[lat] = list of outcomes per call (a list of states, or an Exception to raise)."""
    calls: dict[float, int] = {}

    def fake(point, *, start_delay=0.0):
        n = calls.get(point["lat"], 0)
        calls[point["lat"]] = n + 1
        out = results[point["lat"]][n]
        if isinstance(out, Exception):
            raise out
        return out

    fake.calls = calls
    return fake


def test_failed_point_is_recovered_in_second_pass(h, monkeypatch):
    fake = _fake(
        {1.0: [[_state("a1")]], 2.0: [RuntimeError("429"), [_state("b1")]], 3.0: [[_state("c1")]]}
    )
    monkeypatch.setattr(h, "_fetch_one_point", fake)
    got = {s["icao24"] for s in h._fetch_adsb_lol()}
    assert got == {"a1", "b1", "c1"}
    assert fake.calls == {1.0: 1, 2.0: 2, 3.0: 1}  # only the failed point is retried


def test_no_second_pass_when_first_pass_was_slow(h, monkeypatch):
    monkeypatch.setattr(h, "ADSB_LOL_SECOND_PASS_MAX_START_SECONDS", -1.0)
    fake = _fake({1.0: [[_state("a1")]], 2.0: [RuntimeError("429")], 3.0: [[_state("c1")]]})
    monkeypatch.setattr(h, "_fetch_one_point", fake)
    assert {s["icao24"] for s in h._fetch_adsb_lol()} == {"a1", "c1"}
    assert fake.calls[2.0] == 1


def test_first_pass_observation_wins_on_overlap(h, monkeypatch):
    fake = _fake(
        {
            1.0: [[_state("x", "first")]],
            2.0: [RuntimeError("x"), [_state("x", "second")]],
            3.0: [[]],
        }
    )
    monkeypatch.setattr(h, "_fetch_one_point", fake)
    (state,) = h._fetch_adsb_lol()
    assert state["tag"] == "first"


def test_all_points_failing_twice_still_raises_for_simulator_fallback(h, monkeypatch):
    err = RuntimeError("down")
    fake = _fake({1.0: [err, err], 2.0: [err, err], 3.0: [err, err]})
    monkeypatch.setattr(h, "_fetch_one_point", fake)
    with pytest.raises(RuntimeError, match="zero aircraft"):
        h._fetch_adsb_lol()
