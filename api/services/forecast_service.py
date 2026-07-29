"""Next-6-hour traffic forecast, using the model trained in ml/forecast.py.

Real accumulated hourly history is still far too short for a lag_24
feature (see PROGRESS.md P5), so — exactly as at training time — the
synthetic history generator seeds the lag features. Every response is
flagged `trained_on_synthetic_history: true`; this is not presented as a
real traffic prediction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from api.services.models_loader import bundle
from ml.forecast import FEATURE_COLS, generate_synthetic_history

# Rough uncertainty band: +/- this fraction of the point estimate. Not a
# statistically fitted prediction interval — flagged as approximate.
CONFIDENCE_BAND_FRACTION = 0.20


def get_forecast(horizon_hours: int = 6) -> dict:
    if not bundle.forecast_ready:
        return {"trained_on_synthetic_history": True, "points": []}

    history = generate_synthetic_history()
    history_vals = list(history["flight_count"].values)
    last_ts = history["hour_bucket"].iloc[-1]

    points = []
    for step in range(1, horizon_hours + 1):
        next_ts = last_ts + pd.Timedelta(hours=int(step))
        row = {
            "lag_1": history_vals[-1],
            "lag_2": history_vals[-2],
            "lag_3": history_vals[-3],
            "lag_24": history_vals[-24],
            "hour_sin": np.sin(2 * np.pi * next_ts.hour / 24),
            "hour_cos": np.cos(2 * np.pi * next_ts.hour / 24),
            "day_of_week": next_ts.dayofweek,
        }
        pred = float(bundle.forecast.predict(pd.DataFrame([row])[FEATURE_COLS])[0])
        band = pred * CONFIDENCE_BAND_FRACTION
        points.append(
            {
                "hour_bucket": next_ts,
                "predicted_flight_count": round(pred, 1),
                "lower_bound": round(max(pred - band, 0), 1),
                "upper_bound": round(pred + band, 1),
            }
        )
        history_vals.append(pred)

    return {"trained_on_synthetic_history": True, "points": points}
