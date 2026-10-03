"""Unit tests for ml/features.py, the ONE feature implementation shared by training and serving.

Training (build_windows_v2.py) and the predict Lambda both call it. A silent change would cause
train/serve skew, so the contract is pinned down with small, hand-checkable cases."""

import numpy as np
import pytest

from ml import features as F


def _arrays(n=3, **over):
    """(n, 10) float arrays for every window_x input; NaN = not reported."""
    names = ("lat", "lon", "gs", "trk_deg", "alt", "vr_baro", "vr_geom", "roll", "trate", "mach",
             "tas", "ias", "true_hdg", "wd_deg", "ws", "navalt", "navhdg")
    base = {k: np.full((n, F.HIST), np.nan) for k in names}
    base["lat"] = 50.0 + 0.01 * np.arange(F.HIST)[None, :] * np.ones((n, 1))  # flying due north
    base["lon"] = np.full((n, F.HIST), 8.0)
    base["gs"] = np.full((n, F.HIST), 230.0)
    base["trk_deg"] = np.zeros((n, F.HIST))
    base["alt"] = np.full((n, F.HIST), 10000.0)
    base.update(over)
    return base


def _x(**over):
    a = _arrays(**over)
    order = ("lat", "lon", "gs", "trk_deg", "alt", "vr_baro", "vr_geom", "roll", "trate", "mach",
             "tas", "ias", "true_hdg", "wd_deg", "ws", "navalt", "navhdg")
    return F.window_x(*(a[k] for k in order))


def col(x, name):
    return x[..., F.X_NAMES.index(name)]


def test_shapes_and_dtype():
    x = _x(n=4)
    assert x.shape == (4, F.HIST, len(F.X_NAMES)) == (4, 10, 31)
    assert x.dtype == np.float32
    assert not np.isnan(x).any()          # missing fields become 0 plus a mask, never NaN


def test_positions_are_relative_to_the_last_reading():
    x = _x()
    assert np.allclose(col(x, "dx_km")[:, -1], 0) and np.allclose(col(x, "dy_km")[:, -1], 0)
    dy = col(x, "dy_km")[0]
    assert np.all(np.diff(dy) > 0)                       # flying north: dy increases towards 0
    assert dy[0] == pytest.approx(-0.09 * F.KM_PER_DEG, rel=1e-4)  # nine steps of 0.01 degree


def test_missing_extras_set_masks_to_zero():
    x = _x()
    masks = x[..., len(F.CORE_NAMES) + len(F.EXTRA_NAMES):]
    assert masks.shape[-1] == len(F.MASK_NAMES) and np.all(masks == 0)


def test_reported_extras_set_masks_to_one():
    x = _x(roll=np.full((3, F.HIST), 5.0), mach=np.full((3, F.HIST), 0.78))
    assert np.all(col(x, "m_roll") == 1) and np.all(col(x, "m_mach") == 1)
    assert np.all(col(x, "roll_deg") == 5.0)


def test_vertical_rate_falls_back_to_geometric_and_flags_when_both_missing():
    both_missing = _x()
    assert np.all(col(both_missing, "vrate_missing") == 1)
    assert np.all(col(both_missing, "vrate_ms") == 0)
    geom_only = _x(vr_geom=np.full((3, F.HIST), 2.5))
    assert np.all(col(geom_only, "vrate_missing") == 0)
    assert np.all(col(geom_only, "vrate_ms") == 2.5)
    baro_wins = _x(vr_baro=np.full((3, F.HIST), 1.0), vr_geom=np.full((3, F.HIST), 9.0))
    assert np.all(col(baro_wins, "vrate_ms") == 1.0)


def test_autopilot_altitude_outside_plausible_range_is_ignored():
    x = _x(navalt=np.full((3, F.HIST), 20000.0))          # above the 16,000 m gate
    assert np.all(col(x, "m_navalt") == 0)
    ok = _x(navalt=np.full((3, F.HIST), 11000.0))
    assert np.all(col(ok, "m_navalt") == 1) and np.allclose(col(ok, "navalt_minus_alt"), 1000.0)


def test_wind_from_east_blows_towards_west():
    x = _x(wd_deg=np.full((3, F.HIST), 90.0), ws=np.full((3, F.HIST), 10.0))
    assert np.allclose(col(x, "wind_u"), -10.0, atol=1e-4)
    assert np.allclose(col(x, "wind_v"), 0.0, atol=1e-4)


def test_per_step_changes_and_default_dt():
    gs = 200.0 + 10.0 * np.arange(F.HIST)[None, :] * np.ones((3, 1))
    x = _x(gs=gs)
    assert np.all(col(x, "dgs_ms")[:, 0] == 0) and np.allclose(col(x, "dgs_ms")[:, 1:], 10.0)
    assert np.all(col(x, "dt_min") == 1.0)


def test_wrap180():
    out = F.wrap180(np.array([190.0, -190.0, 0.0, 179.0, 360.0]))
    assert np.allclose(out, [-170.0, 170.0, 0.0, 179.0, 0.0])


@pytest.mark.parametrize(
    ("cat", "expected"), [("A3", 3), ("B2", 10), ("C1", 17), ("", 24), ("Z9", 24), ("A", 24)]
)
def test_category_id(cat, expected):
    assert F.category_id(cat) == expected


def test_static_features_encode_time_cyclically():
    one = np.array
    s = F.window_s(one([50.0]), one([8.0]), one([0.0]), one([3]), one([1]), one([0]), one([0]))
    assert s.shape == (1, 10) and s.dtype == np.float32
    names = dict(zip(F.S_NAMES, s[0], strict=True))
    assert names["hour_sin"] == pytest.approx(0.0, abs=1e-6)
    assert names["hour_cos"] == pytest.approx(1.0)
    # epoch day 0 was a Thursday
    assert names["dow_sin"] == pytest.approx(np.sin(2 * np.pi * 3 / 7), abs=1e-6)


def test_bearing_and_distance_to_destination():
    one = np.array
    dist, brg = F.bearing_distance(one([0.0]), one([0.0]), one([0.0]), one([1.0]))
    assert dist[0] == pytest.approx(111.195, rel=1e-3) and brg[0] == pytest.approx(90.0)
    dist, brg = F.bearing_distance(one([10.0]), one([10.0]), one([np.nan]), one([np.nan]))
    assert np.isnan(dist[0]) and np.isnan(brg[0])
