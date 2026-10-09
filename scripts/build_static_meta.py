"""Build predict/static_meta.json from the VRS standing data (data/vrs/standing-data).

Same rules as `load_vrs()` in ml/scratch/build_windows_v2.py (the training pipeline): wake class
from model-type/*.csv, military ICAO ranges from code-blocks.csv rows with IsMilitary == 1. Stdlib
only, so it runs without the heavy ML dependencies. tests/test_static_meta.py checks the output
against the training loader whenever those dependencies are installed.

    python scripts/build_static_meta.py
"""

from __future__ import annotations

import csv
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VRS_DIR = ROOT / "data" / "vrs" / "standing-data"
OUT = ROOT / "predict" / "static_meta.json"


def build() -> dict:
    wake: dict[str, str] = {}
    for f in sorted(glob.glob(str(VRS_DIR / "model-type" / "schema-01" / "*.csv"))):
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["ICAO"] and row["WakeTurbulenceCode"] in ("L", "M", "H", "J"):
                    wake[row["ICAO"]] = row["WakeTurbulenceCode"]
    mil: list[list[int]] = []
    with open(VRS_DIR / "code-blocks" / "schema-01" / "code-blocks.csv", encoding="utf-8-sig",
              newline="") as fh:
        for row in csv.DictReader(fh):
            if row["IsMilitary"] == "1":
                mil.append([int(row["Start"], 16), int(row["Finish"], 16)])
    return {"wake_by_type": wake, "mil_ranges": mil}


def main() -> None:
    data = build()
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes): "
          f"{len(data['wake_by_type'])} types, {len(data['mil_ranges'])} military ranges")


if __name__ == "__main__":
    main()
