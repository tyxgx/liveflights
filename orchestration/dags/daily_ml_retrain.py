"""daily_ml_retrain — retrain the anomaly and forecast models against the
current silver data, log to MLflow, and (forecast only) conditionally
promote to the Production stage if the new run beats the current
Production model's MAE (see ml/forecast.py's `_maybe_promote`).

Runs anomaly and forecast in parallel — they're independent (anomaly reads
silver via corridors.discover, forecast reads the external DGCA CSV) and
each is already a single bounded Spark/pandas job, so there's no shared
state to serialize on.

Scope note: this does NOT retrain flight corridors from the full multi-week
historical archive (`ml/scratch/train_all.py`) — that script's RAM profile
(~845MB per 5,460 files on this machine) makes it unsafe to run
unattended on a schedule; corridor retraining on the full history stays a
manual, occasional operation. `ml.anomaly` still re-fits corridors, but
only against the current silver window (same data gold_batch reads), which
is what already ran manually before this DAG existed.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from common import DEFAULT_ARGS, uv_run

with DAG(
    dag_id="daily_ml_retrain",
    description="Retrain anomaly + forecast models, log to MLflow, conditionally promote",
    default_args=DEFAULT_ARGS,
    schedule="@daily",
    start_date=datetime(2026, 9, 18),
    catchup=False,
    tags=["liveflights", "ml"],
) as dag:
    retrain_anomaly = BashOperator(
        task_id="retrain_anomaly",
        bash_command=uv_run("python -m ml.anomaly", group="ml"),
    )

    retrain_forecast = BashOperator(
        task_id="retrain_forecast",
        bash_command=uv_run("python -m ml.forecast", group="ml"),
    )
