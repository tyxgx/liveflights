"""Per-aircraft recent track + 5-minute-ahead ghost-trail prediction.

Reuses the exact feature engineering ml/trajectory.py trains on (turn
rate via the same atan2-of-rotation formula, acceleration, climb trend),
computed from the live store's short in-memory history for that aircraft
— at least 2 observations are needed to compute a rate of change.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from api.services.live_store import store
from api.services.models_loader import bundle

FEATURE_COLS = [
    "latitude",
    "longitude",
    "velocity",
    "track_sin",
    "track_cos",
    "vertical_rate",
    "baro_altitude",
    "turn_rate",
    "acceleration",
    "climb_trend",
]


def _build_features(prev: dict, curr: dict) -> dict | None:
    dt = (curr.get("time_position") or 0) - (prev.get("time_position") or 0)
    if dt <= 0:
        return None
    required = ["latitude", "longitude", "velocity", "true_track", "vertical_rate", "baro_altitude"]
    if any(curr.get(f) is None for f in required) or any(prev.get(f) is None for f in required):
        return None

    track_sin = np.sin(np.radians(curr["true_track"]))
    track_cos = np.cos(np.radians(curr["true_track"]))
    prev_sin = np.sin(np.radians(prev["true_track"]))
    prev_cos = np.cos(np.radians(prev["true_track"]))
    cross = track_sin * prev_cos - track_cos * prev_sin
    dot = track_sin * prev_sin + track_cos * prev_cos
    turn_rate = np.degrees(np.arctan2(cross, dot)) / dt

    return {
        "latitude": curr["latitude"],
        "longitude": curr["longitude"],
        "velocity": curr["velocity"],
        "track_sin": track_sin,
        "track_cos": track_cos,
        "vertical_rate": curr["vertical_rate"],
        "baro_altitude": curr["baro_altitude"],
        "turn_rate": turn_rate,
        "acceleration": (curr["velocity"] - prev["velocity"]) / dt,
        "climb_trend": (curr["vertical_rate"] - prev["vertical_rate"]) / dt,
    }


def get_trajectory(icao24: str) -> dict:
    history = store.get_history(icao24)
    track = [
        {
            "time_position": h.get("time_position"),
            "latitude": h.get("latitude"),
            "longitude": h.get("longitude"),
        }
        for h in history
    ]

    predicted = None
    if len(history) >= 2 and bundle.trajectory_ready:
        features = _build_features(history[-2], history[-1])
        if features is not None:
            X = pd.DataFrame([features])[FEATURE_COLS]
            try:
                dlat = float(bundle.trajectory_lat.predict(X)[0])
                dlon = float(bundle.trajectory_lon.predict(X)[0])
                predicted = {
                    "predicted_latitude": history[-1]["latitude"] + dlat,
                    "predicted_longitude": history[-1]["longitude"] + dlon,
                    "horizon_seconds": 300,
                }
            except Exception:
                predicted = None

    return {"icao24": icao24, "recent_track": track, "predicted": predicted}
