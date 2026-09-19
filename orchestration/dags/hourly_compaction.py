"""hourly_compaction — Delta OPTIMIZE/VACUUM on silver + gold, then rebuild
the gold aggregates and their Postgres mirror.

compact.py compacts whatever silver + gold Delta tables already exist, in
that order, before refresh_gold overwrites gold with this hour's
aggregates — so this run's silver read is fast, and this run's gold output
(freshly overwritten, uncompacted) gets picked up by *next* hour's compact
step. gold_batch.py fully overwrites each gold table every run regardless
(see that file), so there's no data-loss risk from compacting gold one
cycle behind.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from common import DEFAULT_ARGS, uv_run

with DAG(
    dag_id="hourly_compaction",
    description="Delta OPTIMIZE/VACUUM on silver+gold, then refresh gold aggregates",
    default_args=DEFAULT_ARGS,
    schedule="@hourly",
    start_date=datetime(2026, 9, 18),
    catchup=False,
    tags=["liveflights", "maintenance"],
) as dag:
    compact_silver = BashOperator(
        task_id="compact_silver",
        bash_command=uv_run("python -m streaming.jobs.compact", group="streaming"),
    )

    refresh_gold = BashOperator(
        task_id="refresh_gold",
        bash_command=uv_run("python -m streaming.jobs.gold_batch", group="streaming"),
    )

    compact_silver >> refresh_gold
