"""predict/handler.py live evaluation: time-corrected actuals and whole-day stats."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")  # predict Lambda dependency, not in the default test group
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "predict"), str(ROOT / "ml")]
os.environ.setdefault("LAKE_BUCKET_NAME", "unused")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import handler as h  # noqa: E402


def reading(ts: float, lat: float, lon: float, gs: float, track: float) -> list[float]:
    """[ts, lat, lon, alt, gs_ms, track_deg, ...] as stored in live/history.json."""
    return [ts, lat, lon, 10000.0, gs, track] + [0.0] * 12


def test_position_at_flies_the_aircraft_to_the_requested_time() -> None:
    r = reading(1000.0, 50.0, 10.0, 250.0, 90.0)  # due east at 250 m/s
    lat, lon = h._position_at(r, 1020.0)  # 20 s later -> 5 km east
    assert lat == pytest.approx(50.0, abs=1e-4)
    east_km = (lon - 10.0) * 111.32 * np.cos(np.radians(50.0))
    assert east_km == pytest.approx(5.0, abs=0.01)
    back_lat, back_lon = h._position_at(r, 980.0)  # 20 s earlier -> 5 km west
    assert back_lon < 10.0


def test_position_at_without_speed_returns_the_raw_position() -> None:
    r = reading(1000.0, 50.0, 10.0, float("nan"), 90.0)
    assert h._position_at(r, 1030.0) == (50.0, 10.0)


def test_late_reading_no_longer_counts_as_model_error() -> None:
    """A perfect prediction, matched against a reading 20 s after the target: error was ~5 km."""
    target = 5000.0
    pred = {"target_ts": target, "made_at": target - 300, "start_lat": 50.0, "start_lon": 9.0,
            "pred_lat_5min": 50.0, "pred_lon_5min": 10.0, "route": None}
    on_time = reading(target, 50.0, 10.0, 250.0, 90.0)
    late = reading(target + 20, *h._position_at(on_time, target + 20), 250.0, 90.0)
    still, records = h._eval_pending({"abc": pred}, {"abc": {"readings": [late]}})
    assert not still and len(records) == 1
    assert records[0]["error_km"] < 0.05


def test_hist_quantile() -> None:
    hist = [0] * h.HIST_BINS
    for e in (1.0, 1.0, 2.0, 3.0, 10.0):
        hist[int(e / h.HIST_BIN_KM)] += 1
    assert 1.0 <= h.hist_quantile(hist, 0.5) <= 2.1
    assert 9.9 <= h.hist_quantile(hist, 0.99) <= 10.1
    assert h.hist_quantile([0] * h.HIST_BINS, 0.5) == 0.0


def test_daily_median_is_whole_day_not_last_2000(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict = {}
    monkeypatch.setattr(h, "_get_json", lambda key, default: store.get(key, default))
    monkeypatch.setattr(h, "_put_json", lambda key, data: store.__setitem__(key, data))
    h._update_metrics([5.0] * 3000)  # earlier in the day: hard, busy traffic
    h._update_metrics([1.0] * 2000)  # latest: easy, quiet traffic
    day = store[h.METRICS_KEY]["days"][-1]
    assert day["day"] == time.strftime("%Y-%m-%d", time.gmtime())
    assert day["n"] == 5000
    assert day["median_km"] == pytest.approx(5.0, abs=0.15)  # old behaviour would report 1.0
    assert day["mean_km"] == pytest.approx(3.4, abs=0.01)
    assert "sample_p90_km" not in day
