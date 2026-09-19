"""daily_quality_drift — row-count/null-rate quality checks + schema drift
detection over the `gold` schema (see orchestration/quality_checks.py for
what's actually computed and why Evidently isn't used).

The task fails (non-zero exit from quality_checks.py) if any column check
fails its threshold — a real gate, not just a logged report, so a broken
upstream job shows up as a red task in the Airflow UI the same day.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from common import DEFAULT_ARGS, uv_run

with DAG(
    dag_id="daily_quality_drift",
    description="gold schema data-quality checks + schema drift detection",
    default_args=DEFAULT_ARGS,
    schedule="@daily",
    start_date=datetime(2026, 9, 18),
    catchup=False,
    tags=["liveflights", "quality"],
) as dag:
    quality_and_drift = BashOperator(
        task_id="quality_and_drift_checks",
        bash_command=uv_run("python -m orchestration.quality_checks", group="ml"),
    )
