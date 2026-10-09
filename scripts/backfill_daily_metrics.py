"""Rebuild metrics/daily.json from the per-prediction eval_log, with whole-day median and p90.

Before 2026-10-07 the daily median/p90 were computed from the LAST 2000 errors of the day only
(see docs/improvements/10-accuracy-metric-honesty.md). eval_log/<date>.json holds every evaluated
prediction, so the real whole-day numbers can be recomputed for every past day. Errors in eval_log
for past days were NOT time-corrected (that fix is only in new records), so these numbers include
a little timing noise; they are labelled with `basis: "eval_log, not time-corrected"`.

Read-only by default: writes a local file. Upload is a separate, deliberate step.

    aws s3 cp s3://<lake>/eval_log/ snap/eval --recursive
    aws s3 cp s3://<lake>/metrics/daily.json snap/daily.json
    python scripts/backfill_daily_metrics.py snap/eval snap/daily.json snap/daily.new.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HIST_BIN_KM = 0.1
HIST_BINS = 501


def day_entry(day: str, errors: np.ndarray) -> dict:
    hist = np.bincount(np.minimum((errors / HIST_BIN_KM).astype(int), HIST_BINS - 1),
                       minlength=HIST_BINS)
    return {
        "day": day, "n": int(len(errors)), "sum_km": float(errors.sum()),
        "sq_sum_km2": float((errors ** 2).sum()), "mean_km": round(float(errors.mean()), 3),
        "median_km": round(float(np.median(errors)), 3),
        "p90_km": round(float(np.quantile(errors, 0.9)), 3),
        "hist": hist.tolist(), "hist_n": int(len(errors)),
        "basis": "eval_log, not time-corrected",
    }


def main(eval_dir: str, old_path: str, out_path: str) -> None:
    old = {d["day"]: d for d in json.loads(Path(old_path).read_text())["days"]}
    for f in sorted(Path(eval_dir).glob("2026-*.json")):
        day = f.stem
        errors = np.array([r["error_km"] for r in json.loads(f.read_text())
                           if r["error_km"] == r["error_km"]])
        if len(errors) == 0:
            continue
        new = day_entry(day, errors)
        was = old.get(day, {})
        print(f"{day}  n={new['n']:6d}  median {was.get('median_km')} -> {new['median_km']}  "
              f"p90 {was.get('p90_km')} -> {new['p90_km']}")
        old[day] = new
    Path(out_path).write_text(json.dumps({"days": [old[d] for d in sorted(old)]},
                                         separators=(",", ":")))
    print("wrote", out_path)


if __name__ == "__main__":
    main(*sys.argv[1:4])
