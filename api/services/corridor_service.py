"""Corridor listing, sorted by member count, capped by `limit`."""

from __future__ import annotations

import json

from api.deps.db import query_df


def list_corridors(limit: int) -> dict:
    total = int(query_df("SELECT count(*) AS n FROM gold.flight_corridors").iloc[0]["n"])
    df = query_df(
        "SELECT * FROM gold.flight_corridors ORDER BY member_count DESC LIMIT :limit",
        {"limit": limit},
    )
    records = df.to_dict("records")
    for r in records:
        if isinstance(r.get("polyline"), str):
            r["polyline"] = json.loads(r["polyline"])
    return {"total_corridors": total, "returned": len(records), "corridors": records}
