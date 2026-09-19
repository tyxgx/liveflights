"""daily_dbt — dbt run, then dbt test, then dbt docs generate.

Test runs strictly after run (not in parallel) so a failing test reflects
that day's actual transformed data, not a stale run; docs generate is last
and allowed to run even if tests failed (`trigger_rule="all_done"`) so the
docs site never goes stale just because one test regressed.

`stg_anomaly_events` reads `gold.anomaly_events`, which only `ml.anomaly`
(daily_ml_retrain.retrain_anomaly) creates, so dbt waits on that task for the
same logical date first — otherwise dbt_run fails on a fresh database.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.sensors.external_task import ExternalTaskSensor
from common import DEFAULT_ARGS, uv_run

DBT_FLAGS = "--project-dir transform --profiles-dir transform"

with DAG(
    dag_id="daily_dbt",
    description="dbt run -> dbt test -> dbt docs generate",
    default_args=DEFAULT_ARGS,
    schedule="@daily",
    start_date=datetime(2026, 9, 18),
    catchup=False,
    tags=["liveflights", "dbt"],
) as dag:
    wait_for_anomaly_events = ExternalTaskSensor(
        task_id="wait_for_anomaly_events",
        external_dag_id="daily_ml_retrain",
        external_task_id="retrain_anomaly",
        mode="reschedule",
        poke_interval=300,
        timeout=6 * 60 * 60,
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=uv_run(f"dbt run {DBT_FLAGS}", group="dbt"),
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=uv_run(f"dbt test {DBT_FLAGS}", group="dbt"),
    )

    dbt_docs_generate = BashOperator(
        task_id="dbt_docs_generate",
        bash_command=uv_run(f"dbt docs generate {DBT_FLAGS}", group="dbt"),
        trigger_rule="all_done",
    )

    wait_for_anomaly_events >> dbt_run >> dbt_test >> dbt_docs_generate
