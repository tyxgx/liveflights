"""Air corridor response models."""

from __future__ import annotations

from pydantic import BaseModel


class Corridor(BaseModel):
    corridor_id: int
    centroid_lat: float
    centroid_lon: float
    modal_heading_deg: float
    altitude_p10_ft: float
    altitude_p50_ft: float
    altitude_p90_ft: float
    member_count: int
    polyline: list[list[float]]


class CorridorsResponse(BaseModel):
    total_corridors: int
    returned: int
    corridors: list[Corridor]
