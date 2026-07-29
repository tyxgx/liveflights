"""Model 4: hourly traffic forecast, next 6 buckets.

Real accumulated history (from stg_traffic_by_hour) is currently only a
couple of hours long — nowhere near enough for lag_24 features. Per
instruction, we generate a MODEST synthetic 7-day hourly history (labeled
"synthetic" everywhere it appears, never presented as real traffic) purely
so the model has enough history to learn a diurnal/weekly pattern from.
Compared against two naive baselines; if the model doesn't beat them, that
is reported plainly, not hidden.
"""

from __future__ import annotations

import logging

import joblib
import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, mean_squared_error

from ml.config import settings

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ml.forecast")

SYNTHETIC_DAYS = 7
FORECAST_HORIZON = 6
FEATURE_COLS = ["lag_1", "lag_2", "lag_3", "lag_24", "hour_sin", "hour_cos", "day_of_week"]


def generate_synthetic_history(days: int = SYNTHETIC_DAYS, seed: int = 7) -> pd.DataFrame:
    """SYNTHETIC hourly flight-count history — not real traffic. Diurnal
    pattern (low overnight, peak midday/evening) + a mild weekday/weekend
    effect + noise, loosely calibrated to the ~150-aircraft simulator fleet.
    """
    rng = np.random.default_rng(seed)
    n_hours = days * 24
    timestamps = pd.date_range(end=pd.Timestamp.utcnow().floor("h"), periods=n_hours, freq="h")

    hour_of_day = timestamps.hour.values
    day_of_week = timestamps.dayofweek.values
    diurnal = 80 + 60 * np.sin((hour_of_day - 6) / 24 * 2 * np.pi - np.pi / 2).clip(min=-0.3)
    weekend_damp = np.where(day_of_week >= 5, 0.85, 1.0)
    noise = rng.normal(0, 8, size=n_hours)
    flight_count = np.clip(diurnal * weekend_damp + noise, 10, None).round().astype(int)

    return pd.DataFrame(
        {"hour_bucket": timestamps, "flight_count": flight_count, "is_synthetic": True}
    )


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("hour_bucket").reset_index(drop=True).copy()
    df["lag_1"] = df["flight_count"].shift(1)
    df["lag_2"] = df["flight_count"].shift(2)
    df["lag_3"] = df["flight_count"].shift(3)
    df["lag_24"] = df["flight_count"].shift(24)
    hour = df["hour_bucket"].dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["day_of_week"] = df["hour_bucket"].dt.dayofweek
    return df.dropna(subset=FEATURE_COLS + ["flight_count"]).reset_index(drop=True)


def _metrics(actual, predicted) -> dict:
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mape": float(mean_absolute_percentage_error(actual, predicted)),
    }


def run() -> dict:
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_forecast_experiment)

    logger.info(
        "Generating %d days of SYNTHETIC hourly traffic history (labeled synthetic — "
        "real accumulated history is far too short for lag_24 features yet).",
        SYNTHETIC_DAYS,
    )
    history = generate_synthetic_history()
    featured = build_features(history)

    split_idx = int(len(featured) * 0.8)
    train, test = featured.iloc[:split_idx], featured.iloc[split_idx:]
    logger.info(
        "Train: %d hours, Test: %d hours (time-based split, strict — no shuffling)",
        len(train),
        len(test),
    )

    model = GradientBoostingRegressor(random_state=42)
    model.fit(train[FEATURE_COLS], train["flight_count"])
    model_pred = model.predict(test[FEATURE_COLS])

    baseline_last_value = test["lag_1"].values
    baseline_same_hour_yesterday = test["lag_24"].values

    results = {
        "model": _metrics(test["flight_count"], model_pred),
        "baseline_last_value": _metrics(test["flight_count"], baseline_last_value),
        "baseline_same_hour_yesterday": _metrics(
            test["flight_count"], baseline_same_hour_yesterday
        ),
    }
    table = pd.DataFrame(results).T
    logger.info(
        "Forecast comparison (synthetic data, 1-step-ahead over the test window):\n%s",
        table.to_string(),
    )

    model_wins = results["model"]["mae"] < min(
        results["baseline_last_value"]["mae"], results["baseline_same_hour_yesterday"]["mae"]
    )
    logger.info("Model %s both naive baselines on MAE.", "beats" if model_wins else "does NOT beat")

    # Recursive 6-step-ahead demo forecast from the end of the (synthetic) series.
    forecasts = []
    history_vals = list(history["flight_count"].values)
    last_ts = history["hour_bucket"].iloc[-1]
    for step in range(1, FORECAST_HORIZON + 1):
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
        pred = float(model.predict(pd.DataFrame([row])[FEATURE_COLS])[0])
        forecasts.append({"hour_bucket": next_ts, "predicted_flight_count": round(pred, 1)})
        history_vals.append(pred)
    forecast_df = pd.DataFrame(forecasts)
    logger.info(
        "Demo 6-hour-ahead recursive forecast (from synthetic series tail):\n%s",
        forecast_df.to_string(index=False),
    )

    with mlflow.start_run(run_name="gbr-traffic-forecast") as run_ctx:
        mlflow.log_param("synthetic_days", SYNTHETIC_DAYS)
        mlflow.log_param("features", FEATURE_COLS)
        mlflow.log_param("train_hours", len(train))
        mlflow.log_param("test_hours", len(test))
        mlflow.log_param("data_source", "SYNTHETIC — see generate_synthetic_history()")
        for name, m in results.items():
            for metric_name, value in m.items():
                mlflow.log_metric(f"{name}_{metric_name}", value)

        importances = pd.Series(model.feature_importances_, index=FEATURE_COLS).sort_values()
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        importances.plot.barh(ax=ax)
        ax.set_title("Traffic forecast — feature importance (SYNTHETIC training data)")
        plot_path = f"{settings.plots_dir}/forecast_feature_importance.png"
        fig.savefig(plot_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(plot_path)

        mlflow.sklearn.log_model(model, "model")
        joblib.dump(model, f"{settings.artifacts_dir}/traffic_forecast_model.pkl")

        client = mlflow.tracking.MlflowClient()
        model_uri = f"runs:/{run_ctx.info.run_id}/model"
        try:
            mv = mlflow.register_model(model_uri, settings.mlflow_forecast_model_name)
            _maybe_promote(
                client, settings.mlflow_forecast_model_name, mv.version, results["model"]["mae"]
            )
        except Exception:
            logger.exception("Model registry step failed (non-fatal for this demo run)")

    return {"metrics_table": table, "model_wins": model_wins, "forecast": forecast_df}


def _maybe_promote(client, model_name: str, version: str, new_mae: float) -> None:
    try:
        current_prod = client.get_latest_versions(model_name, stages=["Production"])
    except Exception:
        current_prod = []
    if not current_prod:
        client.transition_model_version_stage(model_name, version, "Production")
        logger.info(
            "Promoted %s v%s to Production (no prior Production model)", model_name, version
        )
        return
    prod_run = client.get_run(current_prod[0].run_id)
    prod_mae = prod_run.data.metrics.get("model_mae")
    if prod_mae is None or new_mae < prod_mae:
        client.transition_model_version_stage(
            model_name, version, "Production", archive_existing_versions=True
        )
        logger.info(
            "Promoted %s v%s to Production (MAE %.3f < prior %.3f)",
            model_name,
            version,
            new_mae,
            prod_mae or float("inf"),
        )
    else:
        logger.info("Kept existing Production model (MAE %.3f <= new %.3f)", prod_mae, new_mae)


if __name__ == "__main__":
    run()
