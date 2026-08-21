"""Model 4: daily domestic-traffic forecast, next 7 days.

Trains on real data: DGCA / Ministry of Civil Aviation daily domestic
departure-flight counts, sourced from the `Vonter/india-aviation-traffic`
GitHub repo's `aggregated/daily.csv` (no auth needed, verified during
research this file spans 2020-07-07 through 2026-07-05, 1276 rows). This
replaces an earlier synthetic-data version of this model — our own
accumulated real traffic history was too short for lag features, and this
DGCA series is real, dense, and long enough on its own.

Daily granularity, not hourly — DGCA reports don't have hourly counts, and
daily numbers are what "next 7 days" forecasting actually needs (weekday vs
weekend, not time-of-day). Still compared against two naive baselines
(yesterday's count, same day last week); if the model doesn't beat them,
that's reported plainly, not hidden.
"""

from __future__ import annotations

import io
import logging
import subprocess

import joblib
import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, mean_squared_error

from ml.config import settings

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ml.forecast")

DGCA_DAILY_URL = (
    "https://raw.githubusercontent.com/Vonter/india-aviation-traffic/main/aggregated/daily.csv"
)
DGCA_FLIGHT_COL = "Domestic (Departure Flights)"
FORECAST_HORIZON = 7  # days ahead
FEATURE_COLS = ["lag_1", "lag_2", "lag_3", "lag_7", "day_of_week"]


def load_dgca_daily_history() -> pd.DataFrame:
    """Download DGCA's real daily domestic-flight counts and return the
    longest *contiguous* run of consecutive days — the early (2020-2021)
    data has gaps of days/weeks between reports, which would poison lag
    features computed across a gap. curl, not requests/urllib — matches
    the rest of this codebase's workaround for this machine's Python SSL
    trust-store issue with GitHub's cert chain.
    """
    result = subprocess.run(
        ["curl", "-sf", DGCA_DAILY_URL], capture_output=True, text=True, check=True
    )
    df = pd.read_csv(io.StringIO(result.stdout), usecols=["Date", DGCA_FLIGHT_COL])
    df = df.rename(columns={DGCA_FLIGHT_COL: "flight_count"}).dropna()
    df["date_bucket"] = pd.to_datetime(df["Date"])
    df = df.sort_values("date_bucket").drop_duplicates("date_bucket").reset_index(drop=True)

    gap = df["date_bucket"].diff().dt.days.fillna(1)
    run_id = (gap != 1).cumsum()
    longest_run = run_id.value_counts().idxmax()
    contiguous = df[run_id == longest_run].reset_index(drop=True)

    logger.info(
        "DGCA daily.csv: %d total rows, longest contiguous run is %d days (%s -> %s)",
        len(df),
        len(contiguous),
        contiguous["date_bucket"].iloc[0].date(),
        contiguous["date_bucket"].iloc[-1].date(),
    )
    return contiguous[["date_bucket", "flight_count"]]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("date_bucket").reset_index(drop=True).copy()
    df["lag_1"] = df["flight_count"].shift(1)
    df["lag_2"] = df["flight_count"].shift(2)
    df["lag_3"] = df["flight_count"].shift(3)
    df["lag_7"] = df["flight_count"].shift(7)
    df["day_of_week"] = df["date_bucket"].dt.dayofweek
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

    history = load_dgca_daily_history()
    featured = build_features(history)

    split_idx = int(len(featured) * 0.8)
    train, test = featured.iloc[:split_idx], featured.iloc[split_idx:]
    logger.info(
        "Train: %d days, Test: %d days (time-based split, strict — no shuffling)",
        len(train),
        len(test),
    )

    model = GradientBoostingRegressor(random_state=42)
    model.fit(train[FEATURE_COLS], train["flight_count"])
    model_pred = model.predict(test[FEATURE_COLS])

    baseline_yesterday = test["lag_1"].values
    baseline_same_day_last_week = test["lag_7"].values

    results = {
        "model": _metrics(test["flight_count"], model_pred),
        "baseline_yesterday": _metrics(test["flight_count"], baseline_yesterday),
        "baseline_same_day_last_week": _metrics(
            test["flight_count"], baseline_same_day_last_week
        ),
    }
    table = pd.DataFrame(results).T
    logger.info(
        "Forecast comparison (real DGCA data, 1-day-ahead over the test window):\n%s",
        table.to_string(),
    )

    model_wins = results["model"]["mae"] < min(
        results["baseline_yesterday"]["mae"], results["baseline_same_day_last_week"]["mae"]
    )
    logger.info("Model %s both naive baselines on MAE.", "beats" if model_wins else "does NOT beat")

    # Recursive 7-day-ahead demo forecast from the end of the real series.
    forecasts = []
    history_vals = list(history["flight_count"].values)
    last_ts = history["date_bucket"].iloc[-1]
    for step in range(1, FORECAST_HORIZON + 1):
        next_ts = last_ts + pd.Timedelta(days=int(step))
        row = {
            "lag_1": history_vals[-1],
            "lag_2": history_vals[-2],
            "lag_3": history_vals[-3],
            "lag_7": history_vals[-7],
            "day_of_week": next_ts.dayofweek,
        }
        pred = float(model.predict(pd.DataFrame([row])[FEATURE_COLS])[0])
        forecasts.append({"date_bucket": next_ts, "predicted_flight_count": round(pred, 1)})
        history_vals.append(pred)
    forecast_df = pd.DataFrame(forecasts)
    logger.info(
        "Demo 7-day-ahead recursive forecast (from real DGCA series tail):\n%s",
        forecast_df.to_string(index=False),
    )

    with mlflow.start_run(run_name="gbr-traffic-forecast") as run_ctx:
        mlflow.log_param("features", FEATURE_COLS)
        mlflow.log_param("train_days", len(train))
        mlflow.log_param("test_days", len(test))
        mlflow.log_param(
            "data_source",
            "DGCA daily.csv (real, Ministry of Civil Aviation, via Vonter/india-aviation-traffic)",
        )
        for name, m in results.items():
            for metric_name, value in m.items():
                mlflow.log_metric(f"{name}_{metric_name}", value)

        importances = pd.Series(model.feature_importances_, index=FEATURE_COLS).sort_values()
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        importances.plot.barh(ax=ax)
        ax.set_title("Traffic forecast — feature importance (real DGCA training data)")
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
