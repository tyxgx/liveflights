"""Static per-aircraft features (wake class, military flag) for LIVE serving.

Training (ml/scratch/build_windows_v2.py `static_tables`) takes these from the VRS standing data:
wake class from the aircraft type code, military from the ICAO address ranges. The predict Lambda
used to hardcode wake_id=-1 and is_mil=0 for every aircraft, so the model saw different inputs live
than it was trained on (train/serve skew). This module gives serving the SAME mapping from a small
JSON (predict/static_meta.json, built by scripts/build_static_meta.py), so the two cannot drift.

Pure stdlib on purpose: it is vendored into the predict Lambda image next to ml/features.py.
"""

from __future__ import annotations

import json
from pathlib import Path

WAKE_MAP = {"L": 0, "M": 1, "H": 2, "J": 3}  # same as build_windows_v2.static_tables
UNKNOWN_WAKE = -1


class StaticMeta:
    """type code -> wake_id and ICAO hex -> is_military, loaded from the compact JSON."""

    def __init__(self, wake_by_type: dict[str, str], mil_ranges: list[tuple[int, int]]):
        self.wake_by_type = wake_by_type
        self.mil_ranges = sorted(mil_ranges)

    def wake_id(self, type_code: str | None) -> int:
        return WAKE_MAP.get(self.wake_by_type.get(type_code or "", ""), UNKNOWN_WAKE)

    def is_military(self, icao_hex: str | None) -> int:
        try:
            v = int(icao_hex or "", 16)
        except ValueError:
            return 0
        return int(any(s <= v <= e for s, e in self.mil_ranges))


def load(path: str | Path) -> StaticMeta:
    data = json.loads(Path(path).read_text())
    return StaticMeta(data["wake_by_type"], [(s, e) for s, e in data["mil_ranges"]])
