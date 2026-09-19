"""Shared DAG defaults for the 4 liveflights DAGs.

All four run every task via `uv run` against the same repo checked out into
the Airflow image at PROJECT_DIR (see orchestration/Dockerfile) — the exact
same commands `make dbt-run` / `make seed` / etc. run on a dev machine, just
scheduled instead of manual.
"""

from __future__ import annotations

from datetime import timedelta

PROJECT_DIR = "/opt/liveflights"

DEFAULT_ARGS = {
    "owner": "liveflights",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def uv_run(command: str, group: str | None = None) -> str:
    """Build a `cd <project> && uv run [...] <command>` bash string."""
    group_flag = f"--group {group} " if group else ""
    return f"cd {PROJECT_DIR} && uv run {group_flag}{command}"
