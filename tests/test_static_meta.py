"""Live serving must compute wake class / military flag exactly the way training did."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ml.static_meta import UNKNOWN_WAKE, StaticMeta, load

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "predict" / "static_meta.json"
VRS = ROOT / "data" / "vrs" / "standing-data"


@pytest.fixture(scope="module")
def meta() -> StaticMeta:
    return load(META)


def test_known_types(meta: StaticMeta) -> None:
    assert meta.wake_id("B738") == 1  # medium
    assert meta.wake_id("A388") == 2  # VRS lists the A380 as H (no J codes in this data)
    assert meta.wake_id("B744") == 2  # heavy
    assert meta.wake_id("C172") == 0  # light


def test_unknown_or_missing_type_is_minus_one(meta: StaticMeta) -> None:
    assert meta.wake_id(None) == UNKNOWN_WAKE
    assert meta.wake_id("") == UNKNOWN_WAKE
    assert meta.wake_id("ZZZZ") == UNKNOWN_WAKE


def test_military_ranges(meta: StaticMeta) -> None:
    assert meta.is_military("ae1234") == 1  # US military block
    assert meta.is_military("4840d6") == 0  # civil (Dutch registry)
    assert meta.is_military("not-hex") == 0
    assert meta.is_military(None) == 0


@pytest.mark.skipif(not VRS.exists(), reason="VRS standing data not present (gitignored)")
def test_json_matches_a_fresh_build_from_vrs() -> None:
    script = ROOT / "scripts" / "build_static_meta.py"
    spec = importlib.util.spec_from_file_location("build_static_meta", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fresh = module.build()
    stored = load(META)
    assert stored.wake_by_type == fresh["wake_by_type"]
    assert stored.mil_ranges == sorted((s, e) for s, e in fresh["mil_ranges"])
