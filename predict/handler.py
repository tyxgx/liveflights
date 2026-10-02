"""EventBridge-triggered Lambda: build a GRU window per aircraft from the rolling live history,
predict the next 5 minutes, and evaluate predictions made ~5-6 minutes ago against what actually
happened. Runs every minute, right after the ingest Lambda (see infra/terraform/lambda_predict.tf,
not yet written — this handler exists so the code can be reviewed/tested before that wiring).

Reads:  live/history.json      (written by lambda_ingest/handler.py's _update_history)
Writes: live/predictions.json  (current prediction per aircraft, for the dashboard)
        live/pending.json      (predictions not yet due for comparison, <= ~6 min old)
        metrics/daily.json     (permanent, tiny: rolling per-day accuracy AGGREGATE)
        eval_log/<date>.json   (kept ~60 days: every evaluated prediction's full record - icao,
                                predicted vs actual position, error - the raw material to actually
                                improve the model later, not just the same-day aggregate above)
Reads models/trajectory.onnx + models/trajectory_norm.json from S3 once per cold start (same
"models/ in the lake bucket, cached module-level" pattern as api/cloud/app.py's forecast model).

Everything here is the SAME small-combined-S3-object pattern the ingest Lambda already uses
(one GET + one PUT per poll, not one write per aircraft) — see that handler's module docstring for
the cost reasoning this follows.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3
import numpy as np
import onnxruntime as ort
from botocore.exceptions import ClientError
from features import HIST, window_s, window_s_dest, window_x  # vendored: COPY ml/features.py
from route_lookup import RouteTable, load_route_table  # vendored: COPY ml/route_lookup.py

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

LAKE_BUCKET_NAME = os.environ["LAKE_BUCKET_NAME"]
HISTORY_KEY = "live/history.json"
PREDICTIONS_KEY = "live/predictions.json"
PENDING_KEY = "live/pending.json"
METRICS_KEY = "metrics/daily.json"
# + "<date>.json" per day, full per-prediction records (see _append_eval_log)
EVAL_LOG_PREFIX = "eval_log/"
MODEL_KEY = os.environ.get("MODEL_KEY", "models/trajectory.onnx")
NORM_KEY = os.environ.get("NORM_KEY", "models/trajectory_norm.json")
# small (one row/day), keep well over a year; not the 7-day raw archive
METRICS_RETENTION_DAYS = 400

# must match ml/scratch/build_windows_v2.py's HISTORY_FIELD_ORDER / HIST/STEP exactly, or the
# window fed to the model does not match what it was trained on (see ml/features.py's module
# docstring on train/serve skew) -- kept in sync with lambda_ingest/handler.py's HISTORY_FIELD_ORDER
FIELD_ORDER = [
    "lat", "lon", "alt_baro_m", "gs_ms", "track_deg", "vrate_baro_ms", "vrate_geom_ms", "roll_deg",
    "track_rate", "true_heading", "mach", "tas_ms", "ias_ms", "wd_deg", "ws_ms", "nav_alt_mcp_m",
    "nav_heading",
]
STEP_S = 60.0  # window rows must be this far apart (matches training's 60 s spacing)
STEP_TOL_S = 15.0  # a live poll is not exactly 60.000s apart; accept this much jitter per gap
HORIZON_S = 300.0  # +5 minutes
EVAL_TOL_S = 45.0  # accept an actual reading within this many seconds of the target time
MAX_PLAUSIBLE_SPEED_KMH = 1200.0  # generous ceiling for any aircraft in this feed (fastest
# civilian cruise is ~950 km/h) - used to reject eval matches that imply faster travel than any
# real aircraft here could do; see _eval_pending's plausibility check

_model: ort.InferenceSession | None = None
_norm: dict[str, Any] | None = None
_routes: RouteTable | None = None


def _load_model() -> tuple[ort.InferenceSession, dict[str, Any]]:
    """Cold-start load, cached at module level so a warm invocation reuses it."""
    global _model, _norm
    if _model is None:
        obj = s3.get_object(Bucket=LAKE_BUCKET_NAME, Key=MODEL_KEY)
        _model = ort.InferenceSession(obj["Body"].read(), providers=["CPUExecutionProvider"])
        norm_obj = s3.get_object(Bucket=LAKE_BUCKET_NAME, Key=NORM_KEY)
        _norm = json.loads(norm_obj["Body"].read())
        logger.info("Loaded model %s (use_extra=%s, use_dest=%s)", MODEL_KEY,
                   _norm.get("use_extra"), _norm.get("use_dest"))
    return _model, _norm


# same margin box extract_globe_day.py extracts (lat 32-67, lon -16..36 = the 35-64/-13..33 serving
# area + 3 deg) - filters the route table to ~this region so it fits Lambda memory (see
# route_lookup.py's docstring: the unfiltered table alone used ~200-450 MB, more than 512 MB total)
ROUTE_REGION_BOX = (32.0, 67.0, -16.0, 36.0)


def _load_routes() -> RouteTable:
    """Cold-start load of the callsign -> {origin, destination} lookup (ml/route_lookup.py),
    bundled into the container image (COPY data/vrs in the Dockerfile) - not the model, so a
    failure here must never block prediction; the caller degrades to no route info."""
    global _routes
    if _routes is None:
        _routes = load_route_table(region_box=ROUTE_REGION_BOX)
        logger.info("Loaded route table: %d callsigns (region-filtered, lazy expand)", len(_routes))
    return _routes


def _get_json(key: str, default: Any) -> Any:
    try:
        return json.loads(s3.get_object(Bucket=LAKE_BUCKET_NAME, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchKey":
            raise
        return default


def _put_json(key: str, data: Any) -> None:
    s3.put_object(
        Bucket=LAKE_BUCKET_NAME, Key=key,
        Body=json.dumps(data, separators=(",", ":")).encode(), ContentType="application/json",
    )


def _select_window(readings: list[list[float]]) -> np.ndarray | None:
    """The last HIST readings, each ~STEP_S apart (within STEP_TOL_S), oldest first.

    Picks by walking backwards from the newest reading and only keeping one whenever it is close
    enough to STEP_S before the previously kept one — the same "last 10, 60s apart" shape the
    model was trained on (ml/scratch/build_windows_v2.py H_OFF), built from whatever irregular
    spacing the live polls actually produced (adsb.lol 429s, a cold start, a missed minute).
    Returns None if fewer than HIST readings of the right spacing are available yet.
    """
    if len(readings) < HIST:
        return None
    picked = [readings[-1]]
    for row in reversed(readings[:-1]):
        gap = picked[-1][0] - row[0]
        if abs(gap - STEP_S) <= STEP_TOL_S:
            picked.append(row)
            if len(picked) == HIST:
                break
        elif gap > STEP_S + STEP_TOL_S:
            break  # gap already too big to ever land back on a ~60s cadence: give up
    if len(picked) < HIST:
        return None
    window = np.array(list(reversed(picked)), dtype=np.float64)  # (HIST, 1+len(FIELD_ORDER))
    # gs_ms (col 4) and track_deg (col 5) are the only two core fields ml/features.py's window_x()
    # does NOT nan-safe internally (unlike the optional extras, which are masked) - a stray NaN
    # here would otherwise silently turn the whole predicted path into NaN. adsb_lol_extras_mapping
    # already refuses to store a reading missing either one, so this is defense in depth, not the
    # primary fix (see that module's docstring + the 2026-09-28 journal entry on this bug).
    if np.isnan(window[:, 4]).any() or np.isnan(window[:, 5]).any():
        return None
    return window


def _predict_batch(icaos: list[str], rows_list: list[np.ndarray], metas: list[dict[str, Any]],
                   routes: list[dict | None], sess: ort.InferenceSession, use_dest: bool,
                   chunk_size: int = 2000) -> dict[str, dict]:
    """ALL aircraft in ONE (or a few chunked) ONNX call, not one call per aircraft.

    The first deployed version called `sess.run()` once per aircraft in a Python loop - correct,
    but each ONNX Runtime call has real fixed overhead, and Lambda's CPU (weaker than the dev
    Mac this was built/tested on) could not absorb ~2,500 of those a minute: the very first real
    invocations on AWS hit the 60s timeout, then Runtime.OutOfMemory (see the 2026-09-28 journal
    entry "REAL BUGS found in the first live Lambda invocations"). `window_x()`/`window_s()`/
    `window_s_dest()` already accept (n,HIST) batched inputs - this function is what should have
    called them with n=len(icaos) from the start, mirroring exactly how the offline dataset
    builder and the batch-size parity check (09-26 ONNX export entry: 2,500 rows in one call took
    86 ms on the Mac) already do it. `chunk_size` bounds a single sess.run() call's size, in case
    an unusually busy minute (thousands of aircraft) makes one giant batch a memory/latency risk;
    2000 keeps each chunk well under what was already verified fast.
    """
    if not icaos:
        return {}
    rows = np.stack(rows_list)  # (n, HIST, 1+len(FIELD_ORDER))
    f = {name: rows[:, :, 1 + i].astype(np.float32) for i, name in enumerate(FIELD_ORDER)}
    now_ts = rows[:, -1, 0]
    cat_id = np.array([m["cat_id"] for m in metas])
    wake_id = np.array([m["wake_id"] for m in metas])
    is_heli = np.array([m["is_heli"] for m in metas])
    is_mil = np.array([m["is_mil"] for m in metas])
    lat_last, lon_last = f["lat"][:, -1], f["lon"][:, -1]

    out: dict[str, dict] = {}
    for c0 in range(0, len(icaos), chunk_size):
        c1 = min(c0 + chunk_size, len(icaos))
        fc = {k: v[c0:c1] for k, v in f.items()}
        x = window_x(fc["lat"], fc["lon"], fc["gs_ms"], fc["track_deg"], fc["alt_baro_m"],
                    fc["vrate_baro_ms"], fc["vrate_geom_ms"], fc["roll_deg"], fc["track_rate"],
                    fc["mach"], fc["tas_ms"], fc["ias_ms"], fc["true_heading"], fc["wd_deg"],
                    fc["ws_ms"], fc["nav_alt_mcp_m"], fc["nav_heading"])
        if use_dest:
            dlat = np.array(
                [(routes[i]["destination"]["lat"] if routes[i] else np.nan) for i in range(c0, c1)]
            )
            dlon = np.array(
                [(routes[i]["destination"]["lon"] if routes[i] else np.nan) for i in range(c0, c1)]
            )
            s = window_s_dest(lat_last[c0:c1], lon_last[c0:c1], now_ts[c0:c1], cat_id[c0:c1],
                              wake_id[c0:c1], is_heli[c0:c1], is_mil[c0:c1], dlat, dlon,
                              fc["track_deg"], fc["vrate_baro_ms"], fc["vrate_geom_ms"])
        else:
            s = window_s(lat_last[c0:c1], lon_last[c0:c1], now_ts[c0:c1], cat_id[c0:c1],
                        wake_id[c0:c1], is_heli[c0:c1], is_mil[c0:c1])
        pos, hdg, spd = sess.run(None, {"X": x, "S": s})
        coslat = np.cos(np.radians(lat_last[c0:c1]))
        km_per_deg = 111.32
        pred_lat = lat_last[c0:c1, None] + pos[:, :, 1] / km_per_deg
        pred_lon = lon_last[c0:c1, None] + pos[:, :, 0] / (km_per_deg * coslat[:, None])
        for j, i in enumerate(range(c0, c1)):
            out[icaos[i]] = {
                "made_at": float(now_ts[i]),
                "target_ts": float(now_ts[i]) + HORIZON_S,
                "path": [[round(float(la), 5), round(float(lo), 5)]
                        for la, lo in zip(pred_lat[j], pred_lon[j], strict=True)],
                "pred_lat_5min": round(float(pred_lat[j, -1]), 5),
                "pred_lon_5min": round(float(pred_lon[j, -1]), 5),
                "start_lat": round(float(lat_last[i]), 5),
                "start_lon": round(float(lon_last[i]), 5),
                "route": routes[i],
            }
    return out


def _eval_pending(pending: dict[str, dict], history: dict[str, dict]) -> tuple[dict, list[dict]]:
    """Compare due predictions (target_ts already passed) against the closest actual reading.

    Returns the still-pending subset (predictions not due yet, or an aircraft with no reading
    near its target time yet - kept one more cycle in case it arrives late) and one full RECORD
    per prediction evaluated this run (not just the error number) - icao, both positions, the
    error, and whether a route was known - so the raw material for improving the model later is
    actually kept, not just a same-day aggregate (see _append_eval_log's docstring).

    Matches are keyed purely by icao24, which real ADS-B feeds occasionally reuse/collide between
    two unrelated aircraft within minutes - found 2026-09-29 via a handful of eval_log records
    implying 3,000+ km/h travel (338km "error" in 5 minutes). A match like that isn't the model
    being wrong, it's `best` belonging to a different physical aircraft than the one that was
    actually predicted - see the plausibility check below, which drops those instead of logging
    them as (fake, huge) model error.
    """
    now = time.time()
    still_pending: dict[str, dict] = {}
    records: list[dict] = []
    km_per_deg = 111.32
    for icao, pred in pending.items():
        if pred["target_ts"] > now:
            still_pending[icao] = pred
            continue
        readings = (history.get(icao) or {}).get("readings") or []
        best = min(readings, key=lambda r: abs(r[0] - pred["target_ts"]), default=None)
        if best is None or abs(best[0] - pred["target_ts"]) > EVAL_TOL_S:
            if now - pred["target_ts"] < 5 * 60:  # give a late reading more time, then give up
                still_pending[icao] = pred
            continue
        # Plausibility check: derive the implied speed from the aircraft's OWN last known position
        # (start_lat/start_lon, recorded when this prediction was made) to `best` - real aircraft
        # can't exceed MAX_PLAUSIBLE_SPEED_KMH, so a faster implied speed means `best` almost
        # certainly isn't a continuation of the same physical aircraft (icao24 reuse/collision).
        elapsed_h = (best[0] - pred["made_at"]) / 3600.0
        if elapsed_h > 0:
            sdlat, sdlon = best[1] - pred["start_lat"], best[2] - pred["start_lon"]
            travel_km = (
                (sdlat * km_per_deg) ** 2 + (sdlon * km_per_deg * np.cos(np.radians(best[1]))) ** 2
            ) ** 0.5
            if travel_km / elapsed_h > MAX_PLAUSIBLE_SPEED_KMH:
                continue  # not this aircraft's real motion - drop the match, not a model error

        dlat, dlon = best[1] - pred["pred_lat_5min"], best[2] - pred["pred_lon_5min"]
        err = (
            (dlat * km_per_deg) ** 2 + (dlon * km_per_deg * np.cos(np.radians(best[1]))) ** 2
        ) ** 0.5
        records.append({
            "icao": icao, "made_at": pred["made_at"], "target_ts": pred["target_ts"],
            "pred_lat": pred["pred_lat_5min"], "pred_lon": pred["pred_lon_5min"],
            "actual_lat": round(best[1], 5), "actual_lon": round(best[2], 5),
            "actual_ts": best[0], "error_km": round(float(err), 3),
            "had_route": pred.get("route") is not None,
        })
    return still_pending, records


# "kuch dino ke liye" (owner, 2026-09-28) - full per-prediction records for improving the model
# later, not just same-day aggregates. Revisit/extend once the model is actually being retrained
# from this - see _append_eval_log's docstring.
EVAL_LOG_RETENTION_DAYS = 60


def _append_eval_log(records: list[dict]) -> None:
    """Permanent-ish, one small object PER DAY (`eval_log/<date>.json`, read-modify-write like
    metrics/daily.json - append-only within a day, not per-prediction S3 writes): the actual
    material to improve the model on later - every evaluated prediction's icao, both timestamps,
    predicted vs actual position and the error, not just the same-day aggregate metrics/daily.json
    already kept. Requested by the owner (2026-09-28) specifically so real live accuracy data
    accumulates from day one, before the dashboard/any retraining work is ready to use it.
    Kept EVAL_LOG_RETENTION_DAYS (60) worth of daily files; the caller is expected to delete the
    oldest ones periodically (not automated here - S3 lifecycle rules are simpler and cheaper than
    Lambda-side cleanup, and are a deploy-time/terraform decision, not this function's job).
    """
    if not records:
        return
    day_key = time.strftime("%Y-%m-%d", time.gmtime())
    key = f"{EVAL_LOG_PREFIX}{day_key}.json"
    existing = _get_json(key, [])
    existing.extend(records)
    _put_json(key, existing)


def _update_metrics(errors_km: list[float]) -> None:
    """Permanent, tiny: one row per day (count, mean/median/p90 of +5min error), like the ingest
    Lambda's hourly-stats object but never trimmed to a short window - this is the "how accurate
    are we" number the dashboard shows, and it must survive past the 7-day raw archive.

    Filters out NaN before aggregating: one NaN error (e.g. a live-only edge case not fully
    cleaned upstream) must never poison the whole day's running sum forever - found the hard way
    on 2026-09-28's local_live_test.py run, where 23 of 346 evaluated predictions were NaN
    (upstream cause fixed in adsb_lol_extras_mapping.py) and turned that day's mean/p90 into NaN.
    """
    errors_km = [e for e in errors_km if e == e]  # drop NaN (e != e is only true for NaN)
    if not errors_km:
        return
    data = _get_json(METRICS_KEY, {"days": []})
    day_key = time.strftime("%Y-%m-%d", time.gmtime())
    days = data.get("days", [])
    arr = np.array(errors_km)
    today = days[-1] if days and days[-1]["day"] == day_key else None
    if today is None:
        today = {"day": day_key, "n": 0, "sum_km": 0.0, "sq_sum_km2": 0.0, "sample_p90_km": []}
        days.append(today)
    today["n"] += len(errors_km)
    today["sum_km"] += float(arr.sum())
    today["sq_sum_km2"] += float((arr ** 2).sum())
    today["sample_p90_km"] = (today["sample_p90_km"] + errors_km)[-2000:]  # bounded p90 reservoir
    today["mean_km"] = round(today["sum_km"] / today["n"], 3)
    today["p90_km"] = round(float(np.quantile(today["sample_p90_km"], 0.9)), 3)
    # median_km, not mean_km, is the honest "how good is a typical prediction" number - the
    # docstring above always said "mean/median/p90" but median was never actually computed until
    # now. A single very-wrong prediction (a turning aircraft, or the icao24-reuse eval-matching
    # bug fixed 2026-09-29) drags the mean up hard; the median barely moves. Found via a real
    # live check: 09-28's mean was ~4.9km but its median was ~1.3km - the dashboard showing mean
    # alone was quietly overstating how bad the model's typical prediction actually is.
    today["median_km"] = round(float(np.quantile(today["sample_p90_km"], 0.5)), 3)
    days = days[-METRICS_RETENTION_DAYS:]
    _put_json(METRICS_KEY, {"days": days})


def _static_meta(category: str) -> dict[str, float]:
    """Placeholder static features until the VRS wake/military lookup is vendored in here too
    (category_id can be read live; wake/military need the same tables
    ml/scratch/build_windows_v2.py loads from data/vrs/ - not yet wired into this Lambda, see the
    journal entry for this file)."""
    cat = category or ""
    valid = len(cat) == 2 and cat[0] in "ABC" and cat[1].isdigit()
    cat_id = ("ABC".index(cat[0]) * 8 + int(cat[1])) if valid else 24
    return {
        "cat_id": float(cat_id), "wake_id": -1.0,
        "is_heli": 1.0 if cat == "A7" else 0.0, "is_mil": 0.0,
    }


def handler(event: dict, context: object) -> dict:
    """EventBridge entrypoint: predict + evaluate, every minute."""
    sess, norm = _load_model()
    routes = _load_routes()
    history: dict[str, dict] = _get_json(HISTORY_KEY, {})
    if not history:
        return {"statusCode": 200, "predicted": 0, "evaluated": 0}

    icaos: list[str] = []
    windows: list[np.ndarray] = []
    metas: list[dict[str, Any]] = []
    route_list: list[dict | None] = []
    skipped_not_enough_history = 0
    for icao, entry in history.items():
        window = _select_window(entry.get("readings") or [])
        if window is None:
            skipped_not_enough_history += 1
            continue
        icaos.append(icao)
        windows.append(window)
        metas.append(_static_meta(entry.get("category")))
        route_list.append(routes.get(entry.get("callsign") or ""))

    use_dest = bool(norm.get("use_dest"))
    try:
        predictions = _predict_batch(icaos, windows, metas, route_list, sess, use_dest)
    except Exception:  # noqa: BLE001 - a bad batch (e.g. one aircraft's odd data) must not sink
        # the whole poll; fall back to one-by-one so the other aircraft this minute still predict
        logger.exception("Batched prediction failed, falling back to per-aircraft")
        predictions = {}
        for icao, window, meta, route in zip(icaos, windows, metas, route_list, strict=True):
            try:
                predictions.update(
                    _predict_batch([icao], [window], [meta], [route], sess, use_dest)
                )
            except Exception:  # noqa: BLE001
                logger.exception("Prediction failed for %s", icao)

    _put_json(PREDICTIONS_KEY, predictions)

    pending = _get_json(PENDING_KEY, {})
    pending.update({icao: p for icao, p in predictions.items()})  # newest prediction wins
    still_pending, records = _eval_pending(pending, history)
    _put_json(PENDING_KEY, still_pending)
    errors_km = [r["error_km"] for r in records]
    _update_metrics(errors_km)
    try:
        _append_eval_log(records)
    except Exception:  # noqa: BLE001 - the eval log is additive; never let it sink core prediction
        logger.exception("Eval log append failed, continuing without it this poll")

    logger.info("predicted=%d skipped(no history)=%d evaluated=%d still_pending=%d",
               len(predictions), skipped_not_enough_history, len(errors_km), len(still_pending))
    return {"statusCode": 200, "predicted": len(predictions), "evaluated": len(errors_km),
           "still_pending": len(still_pending)}
