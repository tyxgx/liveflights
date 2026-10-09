"""Before/after check for the wake/military feature fix, from ONE live history snapshot.

live/history.json holds ~15 min of 60 s readings per aircraft. For each aircraft this predicts
"5 minutes ahead" from a window that ENDS 5 minutes before its newest reading, then compares with
that newest (actual) position. It does so twice with the same model and windows:
  old: wake_id = -1, is_mil = 0 for everyone (what the predict Lambda used to send)
  new: wake_id / is_mil from ml/static_meta.py (what training used)

    aws s3 cp s3://<lake>/live/history.json snap/history.json
    aws s3 cp s3://<lake>/models/trajectory.onnx snap/  (and trajectory_norm.json)
    python scripts/backtest_static_meta.py snap
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "predict"), str(ROOT / "ml")]
os.environ.setdefault("LAKE_BUCKET_NAME", "unused")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import handler as h  # noqa: E402
import onnxruntime as ort  # noqa: E402

KM = 111.32


def _dist_km(dlat: float, dlon: float, at_lat: float) -> float:
    return float(((dlat * KM) ** 2 + (dlon * KM * np.cos(np.radians(at_lat))) ** 2) ** 0.5)


def summarize(name: str, errs: list[float]) -> str:
    a = np.array(errs)
    return (f"{name:<28} n={len(a):>5}  median={np.median(a):6.3f} km  mean={a.mean():6.3f} km  "
            f"p90={np.quantile(a, 0.9):6.3f} km")


def main(snap: str) -> None:
    snap_dir = Path(snap)
    history = json.loads((snap_dir / "history.json").read_text())
    norm = json.loads((snap_dir / "trajectory_norm.json").read_text())
    onnx_path = str(snap_dir / "trajectory.onnx")
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    routes = h._load_routes()
    use_dest = bool(norm.get("use_dest"))

    icaos, windows, route_list, actual = [], [], [], []
    metas_new, metas_old, groups = [], [], []
    for icao, entry in history.items():
        rd = sorted(entry.get("readings") or [], key=lambda r: r[0])
        if len(rd) < h.HIST + 1:
            continue
        newest = rd[-1]
        k = min(range(len(rd) - 1), key=lambda i: abs((newest[0] - rd[i][0]) - 300))
        if abs((newest[0] - rd[k][0]) - 300) > 20:
            continue
        window = h._select_window(rd[: k + 1])
        if window is None:
            continue
        meta_new = h._static_meta(entry.get("category"), entry.get("type_code"), icao)
        meta_old = dict(meta_new, wake_id=-1.0, is_mil=0.0)
        icaos.append(icao)
        windows.append(window)
        route_list.append(routes.get(entry.get("callsign") or ""))
        # the newest reading is ~300 s (+-20) after the window end: move it to exactly +300 s
        actual.append(h._position_at(newest, rd[k][0] + h.HORIZON_S))
        metas_new.append(meta_new)
        metas_old.append(meta_old)
        known = meta_new["wake_id"] >= 0
        if meta_new["is_mil"]:
            groups.append("military")
        else:
            groups.append("wake known" if known else "wake unknown")

    def run(metas: list[dict]) -> list[float]:
        preds = h._predict_batch(icaos, windows, metas, route_list, sess, use_dest)
        out = []
        for icao, (alat, alon) in zip(icaos, actual, strict=True):
            p = preds[icao]
            dlat, dlon = alat - p["pred_lat_5min"], alon - p["pred_lon_5min"]
            out.append(_dist_km(dlat, dlon, alat))
        return out

    old, new = run(metas_old), run(metas_new)

    def straight_line_km(window: np.ndarray, actual_pos: tuple[float, float]) -> float:
        """Naive guess: keep flying at the last ground speed and track for 5 minutes."""
        lat, lon, gs, trk = window[-1, 1], window[-1, 2], window[-1, 4], np.radians(window[-1, 5])
        north_km, east_km = gs * 300 / 1000 * np.cos(trk), gs * 300 / 1000 * np.sin(trk)
        plat, plon = lat + north_km / KM, lon + east_km / (KM * np.cos(np.radians(lat)))
        dlat, dlon = actual_pos[0] - plat, actual_pos[1] - plon
        return _dist_km(dlat, dlon, actual_pos[0])

    base = [straight_line_km(w, a) for w, a in zip(windows, actual, strict=True)]
    ok = [i for i in range(len(old)) if old[i] == old[i] and new[i] == new[i]]
    print(f"aircraft backtested: {len(ok)} (of {len(history)} in snapshot)")
    print(summarize("OLD (wake=-1, mil=0)", [old[i] for i in ok]))
    print(summarize("NEW (VRS wake + military)", [new[i] for i in ok]))
    print(summarize("NAIVE straight line", [base[i] for i in ok if base[i] == base[i]]))
    diff = np.array([new[i] - old[i] for i in ok])
    print(f"per-aircraft: new better for {(diff < 0).mean():.1%}, "
          f"worse for {(diff > 0).mean():.1%}, identical for {(diff == 0).mean():.1%}")
    for g in ("wake known", "wake unknown", "military"):
        idx = [i for i in ok if groups[i] == g]
        if idx:
            print(f"  [{g}] {summarize('old', [old[i] for i in idx])}")
            print(f"  [{g}] {summarize('new', [new[i] for i in idx])}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "snap")
