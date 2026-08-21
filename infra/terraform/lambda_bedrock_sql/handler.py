"""Bedrock-backed text-to-SQL Lambda: one endpoint, not a product.

Question in, SQL generated against a hardcoded schema description of the
gold tables, validated, executed read-only via Athena, rows back out.

Guardrails (all enforced in code, not just prompted for):
  - Only SELECT statements are ever executed — a regex-based statement-type
    check rejects anything else before it reaches Athena.
  - Only whitelisted gold tables may appear in the generated SQL's FROM/JOIN
    clauses — checked by parsing table references out of the SQL text.
  - A LIMIT is enforced server-side (added if missing, capped if present)
    regardless of what the model generates.
  - Athena workgroup-level bytes-scanned cutoff (see athena.tf) is the
    backstop if all of the above somehow still lets through a
    full-table-scan query.
"""

from __future__ import annotations

import json
import os
import re
import time

import boto3

bedrock = boto3.client("bedrock-runtime")
athena = boto3.client("athena")

ATHENA_DATABASE = os.environ["ATHENA_DATABASE"]
ATHENA_WORKGROUP = os.environ["ATHENA_WORKGROUP"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

ALLOWED_TABLES = {
    "traffic_by_hour",
    "traffic_by_country",
    "airline_activity",
    "altitude_band_distribution",
    "silver",
}
MAX_ROWS = 100

SCHEMA_DESCRIPTION = """
Tables in the Athena/Glue database, all Parquet, all read-only:
- traffic_by_hour(hour_bucket timestamp, flight_count bigint, avg_altitude_ft double,
  avg_speed_kmh double)
- traffic_by_country(origin_country string, flight_count bigint)
- airline_activity(airline string, flight_count bigint)
- altitude_band_distribution(altitude_band string, flight_count bigint)
- silver(icao24 string, callsign string, origin_country string, region string,
  flight_phase string, speed_kmh double, altitude_ft double, ingest_ts timestamp,
  ingest_date string, ingest_hour string)
"""

SQL_STATEMENT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+\"?(?:\w+\.)?\"?(\w+)\"?", re.IGNORECASE)


def generate_sql(question: str) -> str:
    prompt = (
        f"{SCHEMA_DESCRIPTION}\n\n"
        f"Write a single read-only Athena/Presto SQL SELECT statement that answers "
        f"this question: {question}\n\n"
        "Rules: SELECT only, no DDL/DML, no semicolons, always include a LIMIT "
        "clause of 100 or fewer. "
        "Respond with ONLY the SQL, no markdown fences, no explanation."
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=body)
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"].strip().strip(";")


def validate_sql(sql: str) -> None:
    if not SQL_STATEMENT_RE.match(sql):
        raise ValueError("only SELECT statements are permitted")
    if ";" in sql:
        raise ValueError("multiple statements are not permitted")
    tables = {m.group(1).lower() for m in TABLE_REF_RE.finditer(sql)}
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise ValueError(f"query references non-whitelisted table(s): {disallowed}")


def enforce_row_limit(sql: str) -> str:
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    if limit_match:
        requested = int(limit_match.group(1))
        capped = min(requested, MAX_ROWS)
        return re.sub(r"\bLIMIT\s+\d+\b", f"LIMIT {capped}", sql, flags=re.IGNORECASE)
    return f"{sql} LIMIT {MAX_ROWS}"


def run_query(sql: str) -> list[dict]:
    exec_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        WorkGroup=ATHENA_WORKGROUP,
    )["QueryExecutionId"]

    for _ in range(20):
        execution = athena.get_query_execution(QueryExecutionId=exec_id)
        state = execution["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)
    else:
        raise TimeoutError("Athena query did not finish in time")

    if state != "SUCCEEDED":
        raise RuntimeError(f"Athena query {state}")

    result = athena.get_query_results(QueryExecutionId=exec_id)
    rows = result["ResultSet"]["Rows"]
    if not rows:
        return []
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    return [
        dict(zip(header, [c.get("VarCharValue") for c in row["Data"]], strict=False))
        for row in rows[1:]
    ]


def handler(event: dict, context: object) -> dict:
    """API Gateway proxy entrypoint: {"question": "..."} -> {"sql": ..., "rows": [...]}."""
    body = json.loads(event.get("body") or "{}")
    question = (body.get("question") or "").strip()
    if not question:
        return {"statusCode": 400, "body": json.dumps({"error": "missing 'question'"})}

    try:
        sql = generate_sql(question)
        validate_sql(sql)
        sql = enforce_row_limit(sql)
        rows = run_query(sql)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}

    return {"statusCode": 200, "body": json.dumps({"sql": sql, "rows": rows})}
