"""Shared feature code: the SAME function builds the model input for training windows (from GitHub
history, build_windows_v2.py) and for live serving (predict Lambda, from the last 10 one-minute readings).

Keeping one implementation is what prevents train/serve skew. Change anything here and you MUST bump
FEATURES_VERSION: models record the version they were trained with, and the serving code refuses a mismatch.

Inputs are arrays of shape (n, 10): the last 10 readings, oldest first, about 60 s apart; NaN = not reported.
Units: degrees, metres, m/s (same as the Parquet columns).
"""

from __future__ import annotations

import numpy as np

FEATURES_VERSION = 3
# v2 adds 4 destination columns to S (dest_dist_norm, dest_sin, dest_cos, m_dest), from the
# scheduled route (callsign -> VRS routes.csv -> destination airport -> bearing/distance from the
# last position). S grows from 10 to 14 columns; a model trained on one width cannot load the other
# (train_gru_v2.py checks S.shape[-1] and picks the matching path).
# v3: destination is GATED to 0 (as if unknown) whenever the aircraft's own recent history shows
# level, non-turning flight (see window_s_dest's TURN_GATE_DEG/CLIMB_GATE_MS docstring below) -
# the 2026-09-28 experiment (runs/full_B_dest) found destination clearly helped turning/climb but
# made ordinary level-cruise predictions WORSE (median +25-32%); gating removes the signal exactly
# where it was hurting, keeps it exactly where it was helping, and (crucially) uses ONLY the same
# 10-reading window already available at serve time, so live serving can compute the identical gate.
HIST = 10
KM_PER_DEG = 111.32
WIND_FROM = True  # readsb `wd` = direction the wind blows FROM (validated: validate_extras.py check 3)
CORE_NAMES = ["dx_km", "dy_km", "gs_ms", "sin_trk", "cos_trk", "vrate_ms", "alt_m", "vrate_missing",
              "dtrack_deg", "dgs_ms", "dalt_m", "dt_min"]
EXTRA_NAMES = ["roll_deg", "track_rate", "hdg_minus_trk", "mach", "tas_ms", "ias_ms", "wind_u",
               "wind_v", "navalt_minus_alt", "navhdg_minus_trk"]
MASK_NAMES = ["m_roll", "m_trate", "m_hdg", "m_mach", "m_tas", "m_ias", "m_wind", "m_navalt",
              "m_navhdg"]
X_NAMES = CORE_NAMES + EXTRA_NAMES + MASK_NAMES  # 31 columns
S_NAMES = ["lat", "lon", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "category_id", "wake_id",
           "is_heli", "is_mil"]
DEST_NAMES = ["dest_dist_norm", "dest_sin", "dest_cos", "m_dest"]
S_NAMES_DEST = S_NAMES + DEST_NAMES  # 14 columns (schema v2 datasets)
DEST_DIST_SCALE_KM = 2000.0  # dest_dist_norm = log1p(km) / log1p(this); long-haul (~9000 km) -> ~1.4


def wrap180(a: np.ndarray) -> np.ndarray:
    """Wrap an angle difference into [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0


def category_id(cat: str) -> int:
    """ADS-B emitter category 'A3' -> 3, 'B2' -> 10, 'C1' -> 17, unknown -> 24."""
    if len(cat) == 2 and cat[0] in "ABC" and cat[1].isdigit():
        return "ABC".index(cat[0]) * 8 + int(cat[1])
    return 24


def window_x(lat, lon, gs, trk_deg, alt, vr_baro, vr_geom, roll, trate, mach, tas, ias, true_hdg,
             wd_deg, ws, navalt, navhdg, dt_min=None) -> np.ndarray:
    """(n,10) arrays -> X (n, 10, 31) float32 (12 core + 10 extra values + 9 masks)."""
    lat_i, lon_i = lat[:, -1:], lon[:, -1:]
    dy = (lat - lat_i) * KM_PER_DEG
    dx = (lon - lon_i) * KM_PER_DEG * np.cos(np.radians(lat_i))
    trk = np.radians(trk_deg)
    vr = np.where(np.isnan(vr_baro), vr_geom, vr_baro)
    dtrk, dgs, dalt = np.zeros_like(gs), np.zeros_like(gs), np.zeros_like(gs)
    dtrk[:, 1:] = wrap180(trk_deg[:, 1:] - trk_deg[:, :-1])  # change per step
    dgs[:, 1:] = gs[:, 1:] - gs[:, :-1]
    dalt[:, 1:] = alt[:, 1:] - alt[:, :-1]
    dt = np.ones_like(gs) if dt_min is None else dt_min  # minutes since the previous step
    core = np.stack([dx, dy, gs, np.sin(trk), np.cos(trk), np.nan_to_num(vr), alt,
                     np.isnan(vr).astype(np.float32), dtrk, dgs, dalt, dt], -1)
    hdg = wrap180(true_hdg - trk_deg)
    wd = np.radians(wd_deg)
    sign = -1.0 if WIND_FROM else 1.0  # 'from' direction -> vector pointing where the wind blows to
    wu, wv = sign * ws * np.sin(wd), sign * ws * np.cos(wd)
    navalt = np.where((navalt < -300) | (navalt > 16000), np.nan, navalt)
    ndalt, ndhdg = navalt - alt, wrap180(navhdg - trk_deg)
    vals = [roll, trate, hdg, mach, tas, ias, wu, wv, ndalt, ndhdg]
    masks = [roll, trate, hdg, mach, tas, ias, wu, ndalt, ndhdg]  # wind mask uses wu (needs wd, ws)
    ext = np.stack([np.nan_to_num(v) for v in vals]
                   + [(~np.isnan(m)).astype(np.float32) for m in masks], -1)
    return np.concatenate([core, ext], -1).astype(np.float32)


def window_s(lat_i, lon_i, ts_last, cat_id, wake_id, is_heli, is_mil) -> np.ndarray:
    """Static features (n, 10) float32. ts_last = epoch seconds (UTC) of the last reading."""
    hour = 2 * np.pi * (ts_last % 86400) / 86400
    dow = ((ts_last // 86400).astype(int) + 3) % 7
    return np.stack([lat_i, lon_i, np.sin(hour), np.cos(hour), np.sin(2 * np.pi * dow / 7),
                     np.cos(2 * np.pi * dow / 7), cat_id, wake_id, is_heli, is_mil], -1).astype(np.float32)


def bearing_distance(lat_i, lon_i, dest_lat, dest_lon) -> tuple[np.ndarray, np.ndarray]:
    """Great-circle distance (km) and initial bearing (deg, 0=north/clockwise) to a destination.

    dest_lat/dest_lon may be NaN (no known destination for that window); the result is NaN there.
    """
    phi1, phi2 = np.radians(lat_i), np.radians(dest_lat)
    dphi, dlmb = np.radians(dest_lat - lat_i), np.radians(dest_lon - lon_i)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlmb / 2) ** 2
    dist_km = 6371.0088 * 2 * np.arctan2(np.sqrt(a), np.sqrt(np.clip(1 - a, 0, None)))
    brg = np.degrees(np.arctan2(np.sin(dlmb) * np.cos(phi2),
                                np.cos(phi1) * np.sin(phi2) - np.sin(phi1) * np.cos(phi2) * np.cos(dlmb)))
    return dist_km, brg % 360.0


TURN_GATE_DEG = 8.0  # same threshold build_windows_v2.py's cand_regime() uses for "turning"
CLIMB_GATE_MS = 2.5  # same threshold cand_regime() uses for "climbing/descending"
TURN_GATE_LOOKBACK = 3  # steps back (= 3 min at 60s spacing) compared against the last step


def not_level_gate(trk_deg, vr_baro, vr_geom) -> np.ndarray:
    """(n,) bool: True if the window's OWN last few readings show a turn or a climb/descent -
    the same "not level cruise" condition as build_windows_v2.py's regime 1/2, computed purely
    from data any caller (training or live serving) already has for window_x(), so training and
    live serving always compute the identical gate from the identical inputs."""
    turned = np.abs(wrap180(trk_deg[:, -1] - trk_deg[:, -1 - TURN_GATE_LOOKBACK])) > TURN_GATE_DEG
    vr = np.where(np.isnan(vr_baro[:, -1]), vr_geom[:, -1], vr_baro[:, -1])
    climbing = np.abs(np.nan_to_num(vr)) > CLIMB_GATE_MS
    return turned | climbing


def window_s_dest(lat_i, lon_i, ts_last, cat_id, wake_id, is_heli, is_mil,
                  dest_lat, dest_lon, trk_deg, vr_baro, vr_geom) -> np.ndarray:
    """Static features (n, 14) float32: window_s() + destination bearing/distance from the
    scheduled route (VRS routes.csv, matched by callsign). dest_lat/dest_lon = NaN when unknown
    (about half of windows: only the aircraft that report a callsign AND have a matching route).

    GATED (v3): the destination columns are also zeroed (treated as unknown, m_dest=0) whenever
    `not_level_gate()` says the aircraft is currently in level cruise, regardless of whether a
    destination was actually found - see FEATURES_VERSION's v3 note for why.
    """
    base = window_s(lat_i, lon_i, ts_last, cat_id, wake_id, is_heli, is_mil)
    dist_km, brg = bearing_distance(lat_i, lon_i, dest_lat, dest_lon)
    known = np.isfinite(dist_km) & not_level_gate(trk_deg, vr_baro, vr_geom)
    dist_norm = np.where(known, np.log1p(np.nan_to_num(dist_km)) / np.log1p(DEST_DIST_SCALE_KM), 0.0)
    rad = np.radians(np.nan_to_num(brg))
    dest = np.stack([dist_norm, np.where(known, np.sin(rad), 0.0), np.where(known, np.cos(rad), 0.0),
                     known.astype(np.float32)], -1).astype(np.float32)
    return np.concatenate([base, dest], -1)
