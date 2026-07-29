"""Stats endpoints' data access — reads dbt's staging views + gold aggregates."""

from __future__ import annotations

from api.deps.db import query_df
from api.services.live_store import store


def overview() -> dict:
    anomaly_count = int(query_df("SELECT count(*) AS n FROM gold.anomaly_events").iloc[0]["n"])
    return {
        "active_flights": store.count(),
        "countries": store.countries(),
        "avg_altitude_ft": store.avg_altitude_ft(),
        "anomaly_count": anomaly_count,
    }


def traffic_by_hour() -> dict:
    df = query_df(
        "SELECT hour_bucket, flight_count, avg_altitude_ft, avg_speed_kmh "
        "FROM staging.stg_traffic_by_hour ORDER BY hour_bucket"
    )
    points = df.to_dict("records")
    for p in points:
        p["is_synthetic"] = False  # every row here is real, per instruction to separate clearly
    return {"points": points}


def by_country(limit: int) -> dict:
    df = query_df(
        "SELECT origin_country, flight_count, avg_altitude_ft, avg_speed_kmh "
        "FROM staging.stg_traffic_by_country ORDER BY flight_count DESC LIMIT :limit",
        {"limit": limit},
    )
    return {"countries": df.to_dict("records")}
