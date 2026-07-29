"""Anomaly listing + on-demand scoring (rules + corridor-based ML context)."""

from __future__ import annotations

import numpy as np

from api.deps.db import query_df
from streaming.utils.enrich import data_quality_flags

EARTH_RADIUS_KM = 6371.0
LATERAL_DISTANCE_SCALE_KM = 50.0
HEADING_DEVIATION_SCALE_DEG = 45.0
ALTITUDE_Z_SCALE = 3.0
ANOMALY_SCORE_THRESHOLD = 0.65


def list_anomalies(page: int, page_size: int) -> dict:
    total = int(query_df("SELECT count(*) AS n FROM gold.anomaly_events").iloc[0]["n"])
    offset = (page - 1) * page_size
    df = query_df(
        "SELECT * FROM gold.anomaly_events ORDER BY ingest_ts DESC LIMIT :limit OFFSET :offset",
        {"limit": page_size, "offset": offset},
    )
    return {"total": total, "page": page, "page_size": page_size, "events": df.to_dict("records")}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)))


def score_flight_state(state: dict) -> dict:
    """Score an arbitrary flight state: rule flags (same logic as silver's
    enrichment) + ML contextual score against the corridors Model 1
    discovered (loaded fresh from gold.flight_corridors each call — a small
    table, cheap to query per request).
    """
    rule_flags = data_quality_flags(
        state.get("latitude"),
        state.get("longitude"),
        state.get("time_position"),
        state.get("last_contact"),
        state.get("velocity"),
        state.get("baro_altitude"),
        state.get("vertical_rate"),
        state.get("squawk"),
    )

    corridors = query_df(
        "SELECT corridor_id, centroid_lat, centroid_lon, modal_heading_deg, "
        "altitude_p50_ft FROM gold.flight_corridors"
    )
    ml_reasons: list[str] = []
    nearest_corridor_id = None
    lateral_distance_km = None
    heading_deviation_deg = None
    altitude_z = None
    ml_component = 0.0

    lat, lon = state.get("latitude"), state.get("longitude")
    if lat is not None and lon is not None and not corridors.empty:
        dists = corridors.apply(
            lambda r: _haversine_km(lat, lon, r["centroid_lat"], r["centroid_lon"]), axis=1
        )
        idx = dists.idxmin()
        nearest = corridors.loc[idx]
        nearest_corridor_id = int(nearest["corridor_id"])
        lateral_distance_km = float(dists.loc[idx])

        true_track = state.get("true_track")
        if true_track is not None:
            diff = (true_track - nearest["modal_heading_deg"] + 180) % 360 - 180
            heading_deviation_deg = float(abs(diff))

        baro_altitude = state.get("baro_altitude")
        if baro_altitude is not None:
            altitude_ft = baro_altitude * 3.28084
            # No per-corridor std available from this lightweight query;
            # approximate spread as 10% of the corridor's median altitude.
            approx_std = max(nearest["altitude_p50_ft"] * 0.1, 1.0)
            altitude_z = float((altitude_ft - nearest["altitude_p50_ft"]) / approx_std)

        lateral_component = min(lateral_distance_km / LATERAL_DISTANCE_SCALE_KM, 1.0)
        heading_component = (
            min(heading_deviation_deg / HEADING_DEVIATION_SCALE_DEG, 1.0)
            if heading_deviation_deg is not None
            else 0.0
        )
        altitude_component = (
            min(abs(altitude_z) / ALTITUDE_Z_SCALE, 1.0) if altitude_z is not None else 0.0
        )
        ml_component = (lateral_component + heading_component + altitude_component) / 3

        if lateral_component > 0.5:
            ml_reasons.append("far_from_corridor")
        if heading_component > 0.5:
            ml_reasons.append("heading_deviation")
        if altitude_component > 0.5:
            ml_reasons.append("altitude_outlier")

    rule_component = min(len(rule_flags) / 3, 1.0)
    anomaly_score = round(max(rule_component, ml_component), 4)

    return {
        "is_anomaly": anomaly_score > ANOMALY_SCORE_THRESHOLD or len(rule_flags) > 0,
        "anomaly_score": anomaly_score,
        "rule_flags": rule_flags,
        "ml_reasons": ml_reasons,
        "nearest_corridor_id": nearest_corridor_id,
        "lateral_distance_km": lateral_distance_km,
        "heading_deviation_deg": heading_deviation_deg,
        "altitude_z": altitude_z,
    }
