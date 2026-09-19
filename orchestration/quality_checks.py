"""Data quality + schema drift checks against the gold schema in Postgres.

Two checks, both genuinely computed (no fabricated metrics):

1. **Quality**: for every column of every table in the `gold` schema,
   row count and null rate. A table with 0 rows, or a column whose null
   rate exceeds `NULL_RATE_THRESHOLD`, is a quality failure.
2. **Schema drift**: the current `(table, column, data_type)` set for
   `gold` is compared against the last snapshot stored in MinIO
   (`quality-reports/schema_baseline.json`). Any added/removed/retyped
   column is reported as drift (not necessarily a failure — logged either
   way, but surfaced).

No Evidently here — it isn't an installed dependency anywhere else in this
repo (PLAN.md's monitoring stack lists it, but P9 never added it), and
bringing in a whole drift-report library for one Airflow task wasn't worth
it. This does the same two things (quality thresholds + schema drift) with
plain SQL and a JSON diff instead. Intended to run daily via Airflow's
`daily_quality_drift` DAG (P8).

Report JSON is written to MinIO only — the real-AWS-S3 sync PLAN.md
mentions for this DAG was dropped (see docs/architecture.md); this project
had a real cost incident from an always-on AWS resource before (see
PROGRESS.md), so nothing here touches real AWS by default.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import boto3
from sqlalchemy import create_engine, text

from streaming.config import settings

logger = logging.getLogger("orchestration.quality_checks")

NULL_RATE_THRESHOLD = 0.5
SCHEMA_NAME = "gold"
REPORT_PREFIX = "quality-reports"
BASELINE_KEY = f"{REPORT_PREFIX}/schema_baseline.json"


@dataclass
class ColumnCheck:
    table: str
    column: str
    data_type: str
    row_count: int
    null_count: int
    null_rate: float
    passed: bool


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{settings.minio_endpoint}",
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
    )


def _list_columns(engine) -> list[tuple[str, str, str]]:
    """Returns (table_name, column_name, data_type) for every column in
    SCHEMA_NAME, via information_schema — no hardcoded table list, so a
    table added later is picked up automatically."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema
                ORDER BY table_name, ordinal_position
                """
            ),
            {"schema": SCHEMA_NAME},
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _run_quality_checks(engine, columns: list[tuple[str, str, str]]) -> list[ColumnCheck]:
    checks: list[ColumnCheck] = []
    tables = sorted({t for t, _, _ in columns})
    with engine.connect() as conn:
        row_counts = {
            t: conn.execute(text(f'SELECT COUNT(*) FROM "{SCHEMA_NAME}"."{t}"')).scalar()
            for t in tables
        }
        for table, column, data_type in columns:
            row_count = row_counts[table]
            if row_count == 0:
                null_count, null_rate = 0, 0.0
            else:
                null_count = conn.execute(
                    text(
                        f'SELECT COUNT(*) FROM "{SCHEMA_NAME}"."{table}" '
                        f'WHERE "{column}" IS NULL'
                    )
                ).scalar()
                null_rate = null_count / row_count
            passed = row_count > 0 and null_rate <= NULL_RATE_THRESHOLD
            checks.append(
                ColumnCheck(
                    table=table,
                    column=column,
                    data_type=data_type,
                    row_count=row_count,
                    null_count=null_count,
                    null_rate=round(null_rate, 4),
                    passed=passed,
                )
            )
    return checks


def _schema_drift(s3, columns: list[tuple[str, str, str]]) -> dict:
    current = sorted(f"{t}.{c}:{d}" for t, c, d in columns)
    try:
        obj = s3.get_object(Bucket=settings.minio_bucket, Key=BASELINE_KEY)
        baseline = set(json.loads(obj["Body"].read())["columns"])
    except s3.exceptions.NoSuchKey:
        baseline = None
    except Exception:
        logger.warning("no prior schema baseline found (%s) — first run", BASELINE_KEY)
        baseline = None

    if baseline is None:
        added, removed = [], []
    else:
        current_set = set(current)
        added = sorted(current_set - baseline)
        removed = sorted(baseline - current_set)

    s3.put_object(
        Bucket=settings.minio_bucket,
        Key=BASELINE_KEY,
        Body=json.dumps({"columns": current, "updated_at": datetime.now(UTC).isoformat()}),
        ContentType="application/json",
    )
    return {"added": added, "removed": removed, "is_first_run": baseline is None}


def run() -> bool:
    """Returns True if every quality check passed."""
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = create_engine(settings.database_url)
    s3 = _s3_client()

    columns = _list_columns(engine)
    if not columns:
        logger.error("gold schema has no tables yet — nothing to check")
        return False

    checks = _run_quality_checks(engine, columns)
    drift = _schema_drift(s3, columns)

    failures = [c for c in checks if not c.passed]
    for c in failures:
        logger.warning(
            "QUALITY FAIL: %s.%s null_rate=%.2f%% (threshold %.0f%%) row_count=%d",
            c.table,
            c.column,
            c.null_rate * 100,
            NULL_RATE_THRESHOLD * 100,
            c.row_count,
        )
    if drift["added"] or drift["removed"]:
        logger.warning("SCHEMA DRIFT: added=%s removed=%s", drift["added"], drift["removed"])
    else:
        logger.info("no schema drift detected")

    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "checks": [asdict(c) for c in checks],
        "failure_count": len(failures),
        "schema_drift": drift,
    }
    report_key = f"{REPORT_PREFIX}/{datetime.now(UTC):%Y-%m-%d}/quality_report.json"
    s3.put_object(
        Bucket=settings.minio_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2),
        ContentType="application/json",
    )
    logger.info(
        "quality report written to s3://%s/%s (%d/%d checks passed)",
        settings.minio_bucket,
        report_key,
        len(checks) - len(failures),
        len(checks),
    )
    return len(failures) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="liveflights gold data-quality + schema drift")
    parser.parse_args()
    ok = run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
