"""EventBridge-triggered Lambda: fetch live adsb.lol states, write to S3.

OpenSky's API is unreachable from this Lambda's AWS egress IP (connections to
both `auth.opensky-network.org` and `opensky-network.org` time out at the TCP
level, while general internet egress from the same Lambda works fine) — see
docs/aws-architecture.md for the diagnosis. adsb.lol (a community ADS-B
aggregator) IS reachable from AWS egress — confirmed via the same diagnostic
approach — so it's the primary source here, reusing
`ingestion.schemas.adsb_lol_mapping.map_to_flight_state_dict`, the exact
mapping used by the local producer's `adsb_lol` adapter.

If the live fetch fails for any reason (the aggregator is down, rate-limits
this IP, changes its response shape, etc.), this Lambda falls back to
`ingestion.simulator.FlightSimulator` — the same generator behind local
`--mode simulate` runs — labeled `source="simulate_cloud"`, so the pipeline
never goes dark and downstream consumers can always tell which path produced
a given record.

NOTE (Aug 2026): this stack is a live-data-only MVP — ML (corridors/
anomalies/forecast, and the departure/predicted-destination/ETA trajectory-
tracking that briefly lived here) is paused. DynamoDB is gone entirely: the
live snapshot is one small S3 object (live/latest.json), overwritten every
poll, instead of a full-table item-by-item DynamoDB rewrite — that rewrite
(~4,600 items x 60 writes/hour once Europe coverage went multi-point) was a
real, measured ~$155/mo problem, not a hypothetical one. A tiny rolling
stats/hourly.json gives the dashboard a traffic-over-time chart without
needing Athena/Glue/Step Functions/a transform Lambda at all — every stat
the API serves is computed from these two small files, on the fly.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ingestion.schemas.adsb_lol_extras_mapping import map_to_ml_fields
from ingestion.schemas.adsb_lol_mapping import map_to_flight_state_dict
from ingestion.simulator import FlightSimulator

logger = logging.getLogger()
logger.setLevel(logging.INFO)

firehose = boto3.client("firehose")
s3 = boto3.client("s3")

FIREHOSE_STREAM_NAME = os.environ["FIREHOSE_STREAM_NAME"]
LAKE_BUCKET_NAME = os.environ["LAKE_BUCKET_NAME"]
# Public site bucket: the browser reads a small pre-gzipped map snapshot straight from S3 instead
# of pulling 1.6 MB through API Gateway + Lambda on every poll. Optional: unset = feature off
# (the API path still works).
SITE_BUCKET_NAME = os.environ.get("SITE_BUCKET_NAME", "")
MAP_SNAPSHOT_KEY = "live/map.json"
# fields the web app never reads; dropped from the map snapshot only (live/latest.json and bronze
# keep all 16)
MAP_DROP_FIELDS = frozenset(
    {"time_position", "last_contact", "geo_altitude", "spi", "position_source"}
)
SIMULATOR_REGION = os.environ.get("SIMULATOR_REGION", "india")
SIMULATOR_AIRCRAFT_COUNT = int(os.environ.get("SIMULATOR_AIRCRAFT_COUNT", "40"))
SIMULATOR_ANOMALY_RATE = float(os.environ.get("SIMULATOR_ANOMALY_RATE", "0.02"))
LIVE_SNAPSHOT_KEY = "live/latest.json"
HOURLY_STATS_KEY = "stats/hourly.json"
HOURLY_STATS_RETENTION = 48  # keep the last 48 hourly entries, drop older ones
HISTORY_KEY = "live/history.json"
# ml/scratch/build_windows_v2.py's window is 10 readings spaced 60 s apart (H_OFF = -54..0 rows on
# a 10 s grid = 9 minutes back from "now"); keep a few extra minutes of slack for a missed poll
# (adsb.lol 429s, a cold start) without losing the ability to build a window once polling resumes.
HISTORY_WINDOW_MINUTES = 15
# an aircraft not seen for this long is dropped from history entirely, so the object does not grow
# forever with aircraft that left coverage (landed, went below the horizon, flew out of the boxes)
HISTORY_STALE_MINUTES = 20
# compact form per reading: [ts, lat, lon, alt_baro_m, gs_ms, track_deg, vrate_baro_ms,
# vrate_geom_ms, roll_deg, track_rate, true_heading, mach, tas_ms, ias_ms, wd_deg, ws_ms,
# nav_alt_mcp_m, nav_heading] -- a list, not a dict, so the field names are not repeated on every
# one of the thousands of readings in the combined object (keeps live/history.json much smaller).
HISTORY_FIELD_ORDER = [
    "lat", "lon", "alt_baro_m", "gs_ms", "track_deg", "vrate_baro_ms", "vrate_geom_ms", "roll_deg",
    "track_rate", "true_heading", "mach", "tas_ms", "ias_ms", "wd_deg", "ws_ms", "nav_alt_mcp_m",
    "nav_heading",
]

# adsb.lol's /v2/lat/{lat}/lon/{lon}/dist/{nm} endpoint is a single point +
# radius query, capped at 250nm by the API itself — one call can never cover
# a continent. To get Europe-wide coverage instead of one small circle, this
# Lambda now fans out to a curated set of hub-centered points and merges the
# results, deduped by icao24 (adjacent circles overlap at the edges).
#
# Not a mathematically exact tiling of the whole Europe bbox (that would need
# dozens of 250nm circles) — 8 points chosen to sit near real air-traffic
# density centers (major hub regions), which covers the routes a viewer
# actually expects to see on a "Europe" map far better than one circle would.
DEFAULT_EUROPE_POINTS: list[dict[str, float]] = [
    {"lat": 53.0, "lon": -2.0, "dist": 250},  # British Isles
    {"lat": 50.0, "lon": 2.5, "dist": 250},  # France / Benelux
    {"lat": 50.5, "lon": 10.0, "dist": 250},  # Germany / Central Europe (old single-point default)
    {"lat": 59.0, "lon": 15.0, "dist": 250},  # Scandinavia
    {"lat": 40.0, "lon": -3.5, "dist": 250},  # Iberia
    {"lat": 42.0, "lon": 12.5, "dist": 250},  # Italy
    {"lat": 50.5, "lon": 22.0, "dist": 250},  # Poland / Eastern Europe
    {"lat": 40.0, "lon": 22.0, "dist": 250},  # Balkans / Greece
]


def _load_points() -> list[dict[str, float]]:
    """Resolve the list of {lat, lon, dist} points to poll.

    Priority: ADSB_LOL_POINTS (JSON list, the new multi-point config) >
    the old single-point ADSB_LOL_LAT/LON/DIST_NM vars (back-compat, so an
    un-migrated deploy still works) > the curated default above.
    """
    raw_points = os.environ.get("ADSB_LOL_POINTS")
    if raw_points:
        return json.loads(raw_points)

    if "ADSB_LOL_LAT" in os.environ:
        return [
            {
                "lat": float(os.environ["ADSB_LOL_LAT"]),
                "lon": float(os.environ["ADSB_LOL_LON"]),
                "dist": float(os.environ.get("ADSB_LOL_DIST_NM", "250")),
            }
        ]

    return DEFAULT_EUROPE_POINTS


ADSB_LOL_POINTS = _load_points()
# Firing all 8 points fully concurrently got adsb.lol rate-limiting several
# of them (HTTP 420/429) every single run — 2-3 of 8 points failing per
# invocation, in practice. Lower default concurrency + a small stagger below
# fixes that; override via env if adsb.lol's actual limit turns out looser.
ADSB_LOL_MAX_WORKERS = int(os.environ.get("ADSB_LOL_MAX_WORKERS", "3"))
ADSB_LOL_STAGGER_SECONDS = float(os.environ.get("ADSB_LOL_STAGGER_SECONDS", "0.35"))
ADSB_LOL_RETRY_ATTEMPTS = int(os.environ.get("ADSB_LOL_RETRY_ATTEMPTS", "2"))

# Module-level so the simulator's aircraft pool persists across warm
# invocations (used only as a fallback) instead of respawning every 5 minutes.
_simulator = FlightSimulator(
    aircraft_count=SIMULATOR_AIRCRAFT_COUNT,
    anomaly_rate=SIMULATOR_ANOMALY_RATE,
    region=SIMULATOR_REGION,
)


def _fetch_one_point(point: dict[str, float], *, start_delay: float = 0.0) -> list[dict[str, Any]]:
    """Fetch one point+radius circle, staggered by `start_delay` seconds so
    N points submitted together don't all hit adsb.lol in the same instant.
    Retries once (by default) on 429/420 — adsb.lol's rate-limit responses —
    with a short backoff; any other failure (timeout, 5xx, malformed JSON)
    raises immediately, since retrying those isn't likely to help within a
    single 1-minute poll window.
    """
    if start_delay:
        time.sleep(start_delay)

    url = f"https://api.adsb.lol/v2/lat/{point['lat']}/lon/{point['lon']}/dist/{point['dist']}"
    req = urllib.request.Request(url, headers={"User-Agent": "liveflights-cloud/1.0"})

    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed adsb.lol host
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (420, 429) and attempt <= ADSB_LOL_RETRY_ATTEMPTS:
                backoff = 1.5 * attempt
                logger.warning(
                    "adsb.lol point %s rate-limited (HTTP %d), retry %d/%d in %.1fs",
                    point, exc.code, attempt, ADSB_LOL_RETRY_ATTEMPTS, backoff,
                )
                time.sleep(backoff)
                continue
            raise

    now_ms = payload.get("now")
    now = (now_ms / 1000) if now_ms else time.time()

    states = []
    for row in payload.get("ac") or []:
        try:
            state = map_to_flight_state_dict(row, now=now, source="adsb_lol")
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Dropping malformed adsb.lol row: %s", exc)
            continue
        # extras is None for rows the trajectory model's training pipeline would also have
        # dropped (on the ground, no position, non-ICAO address) -- never fatal, just skipped
        # from the ML history; the canonical `state` is unaffected either way.
        state["_ml_extras"] = map_to_ml_fields(row, now=now)
        states.append(state)
    return states


def _fetch_adsb_lol() -> list[dict[str, Any]]:
    """Fan out to every configured point (I/O-bound HTTP calls, so threads —
    not asyncio — are the boring, sufficient choice here), staggered and
    concurrency-capped to stay under adsb.lol's per-IP rate limit, and merge
    the results, deduped by icao24. Overlapping circles mean the same
    aircraft can come back from two points; the later-fetched observation
    wins (arbitrary but harmless — both are the same ~1-minute poll).

    A single point's failure (rate-limited past the retry budget, timeout,
    5xx) is logged and skipped, not fatal — the other points' data still
    ships. Only an all-points failure falls through to the caller's
    simulator fallback.
    """
    merged: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=ADSB_LOL_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_point, p, start_delay=i * ADSB_LOL_STAGGER_SECONDS): p
            for i, p in enumerate(ADSB_LOL_POINTS)
        }
        for future in as_completed(futures):
            point = futures[future]
            try:
                for state in future.result():
                    merged[state["icao24"]] = state
            except Exception as exc:  # noqa: BLE001 - one point's failure shouldn't sink the rest
                logger.warning("adsb.lol point %s failed: %s", point, exc)

    if not merged:
        raise RuntimeError("adsb.lol returned zero aircraft across all points")

    return list(merged.values())


FIREHOSE_MAX_RECORD_BYTES = 900_000  # stay under the 1000 KiB hard limit with headroom


def _put_ndjson_chunks(states: list[dict[str, Any]]) -> None:
    """Split states into NDJSON chunks under Firehose's per-record size cap
    and PutRecord each one. Chunking by line count would risk a single
    oversized line-run tipping past the limit; this chunks by accumulated
    byte size instead, so it's correct regardless of how big the fleet gets.
    """
    chunk_lines: list[str] = []
    chunk_bytes = 0

    def _flush() -> None:
        if not chunk_lines:
            return
        firehose.put_record(
            DeliveryStreamName=FIREHOSE_STREAM_NAME,
            Record={"Data": ("\n".join(chunk_lines) + "\n").encode()},
        )

    for state in states:
        line = json.dumps(state)
        line_bytes = len(line.encode()) + 1  # +1 for the newline
        if chunk_lines and chunk_bytes + line_bytes > FIREHOSE_MAX_RECORD_BYTES:
            _flush()
            chunk_lines, chunk_bytes = [], 0
        chunk_lines.append(line)
        chunk_bytes += line_bytes

    _flush()


def _write_live_snapshot(states: list[dict[str, Any]], ingest_ts: str) -> None:
    """One S3 object, fully overwritten every poll — this is the entire
    'live state store' now. One PUT/minute (~$0.0002/mo at that rate) vs.
    DynamoDB's ~4,600 individual item writes/poll (~$155/mo measured, see
    module docstring) for exactly the same 'what's flying right now' answer.
    """
    payload = {
        "updated_at": ingest_ts,
        "count": len(states),
        "flights": states,
    }
    s3.put_object(
        Bucket=LAKE_BUCKET_NAME,
        Key=LIVE_SNAPSHOT_KEY,
        Body=json.dumps(payload).encode(),
        ContentType="application/json",
    )


def _slim_for_map(state: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in state.items() if k not in MAP_DROP_FIELDS}
    for key in ("latitude", "longitude"):
        if out.get(key) is not None:
            out[key] = round(out[key], 5)  # ~1 m
    for key in ("baro_altitude", "velocity", "true_track", "vertical_rate"):
        if out.get(key) is not None:
            out[key] = round(out[key], 1)
    return out


def _write_map_snapshot(states: list[dict[str, Any]], ingest_ts: str) -> None:
    """Same shape as GET /api/flights/live, minus unused fields, rounded, gzipped, in the PUBLIC
    site bucket (about 137 KB instead of 1.6 MB). 10 s max-age: the map polls every 15 s and the
    data changes once a minute."""
    body = json.dumps(
        {"count": len(states), "updated_at": ingest_ts,
         "flights": [_slim_for_map(s) for s in states]},
        separators=(",", ":"),
    ).encode()
    s3.put_object(
        Bucket=SITE_BUCKET_NAME,
        Key=MAP_SNAPSHOT_KEY,
        Body=gzip.compress(body, compresslevel=6),
        ContentType="application/json",
        ContentEncoding="gzip",
        CacheControl="public, max-age=10",
    )


def _update_hourly_stats(states: list[dict[str, Any]]) -> None:
    """Rolling last-48-hours aggregate, read-modify-write on ONE small S3
    object (a few KB at most) — gives the dashboard a traffic-over-time
    chart without Athena/Glue/a transform Lambda. Each poll overwrites the
    CURRENT hour's entry (so it reflects the latest count seen this hour,
    not a sum across polls — deliberately simple, no double-counting risk).
    """
    hour_key = datetime.now(UTC).strftime("%Y-%m-%dT%H:00:00Z")
    altitudes = [s["baro_altitude"] for s in states if s.get("baro_altitude") is not None]
    countries = {s["origin_country"] for s in states if s.get("origin_country")}
    avg_altitude_ft = (sum(altitudes) / len(altitudes) * 3.28084) if altitudes else None

    try:
        obj = s3.get_object(Bucket=LAKE_BUCKET_NAME, Key=HOURLY_STATS_KEY)
        data = json.loads(obj["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchKey":
            raise
        data = {"hours": []}

    hours = data.get("hours", [])
    entry = {
        "hour": hour_key,
        "flight_count": len(states),
        "countries": len(countries),
        "avg_altitude_ft": round(avg_altitude_ft, 1) if avg_altitude_ft else None,
    }
    if hours and hours[-1]["hour"] == hour_key:
        hours[-1] = entry
    else:
        hours.append(entry)
    hours = hours[-HOURLY_STATS_RETENTION:]

    s3.put_object(
        Bucket=LAKE_BUCKET_NAME,
        Key=HOURLY_STATS_KEY,
        Body=json.dumps({"hours": hours}).encode(),
        ContentType="application/json",
    )


def _update_history(extras: list[dict[str, Any]]) -> None:
    """Rolling last-HISTORY_WINDOW_MINUTES-of-readings per aircraft, read-modify-write on ONE
    combined S3 object — the same cost-driven pattern as `_write_live_snapshot`/
    `_update_hourly_stats` above (one GET + one PUT per poll, not one write per aircraft).

    This is what the predict Lambda reads to build a GRU window: `ml/features.py`'s `window_x()`/
    `window_s()` need the last 10 readings spaced 60 s apart, in the same units this dict already
    uses. Readings closer together than about 40 s to the previous kept one are skipped (the
    window spacing is 60 s; keeping every ~60 s poll, not every retry/duplicate, keeps the object
    from filling up with near-duplicate rows if the schedule ever fires faster than once a minute).
    """
    try:
        obj = s3.get_object(Bucket=LAKE_BUCKET_NAME, Key=HISTORY_KEY)
        history: dict[str, dict[str, Any]] = json.loads(obj["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchKey":
            raise
        history = {}

    now = time.time()
    seen_now: set[str] = set()
    for row in extras:
        if row is None:
            continue
        icao, ts = row["icao24"], row["ts"]
        seen_now.add(icao)
        # nested per-aircraft: identity fields (updated to the latest seen each poll - a callsign
        # can change leg to leg, though rarely mid-poll) + the numeric reading list predict Lambda
        # windows over. identity fields are NOT part of HISTORY_FIELD_ORDER (they are strings, not
        # numbers the model consumes) - kept alongside so the predict Lambda / a future dashboard
        # can label an aircraft (route lookup, category) without a second S3 round trip.
        entry = history.setdefault(icao, {"readings": []})
        entry["callsign"] = row.get("callsign") or entry.get("callsign")
        entry["category"] = row.get("category") or entry.get("category")
        entry["type_code"] = row.get("type_code") or entry.get("type_code")
        readings = entry["readings"]
        if readings and ts - readings[-1][0] < 40:
            continue  # too close to the previous kept reading, skip (not a new ~60s poll)
        readings.append([ts, *(row[f] for f in HISTORY_FIELD_ORDER)])
        cutoff = ts - HISTORY_WINDOW_MINUTES * 60
        while readings and readings[0][0] < cutoff:
            readings.pop(0)

    stale_cutoff = now - HISTORY_STALE_MINUTES * 60
    for icao in list(history):
        readings = history[icao]["readings"]
        if icao not in seen_now and (not readings or readings[-1][0] < stale_cutoff):
            del history[icao]  # left coverage a while ago: drop, don't grow the object forever

    s3.put_object(
        Bucket=LAKE_BUCKET_NAME,
        Key=HISTORY_KEY,
        Body=json.dumps(history, separators=(",", ":")).encode(),
        ContentType="application/json",
    )


def handler(event: dict, context: object) -> dict:
    """EventBridge entrypoint: fetch live states (or simulate), write to S3."""
    ingest_ts = datetime.now(UTC).isoformat()
    t0 = time.monotonic()
    timings: dict[str, float] = {}

    try:
        states = _fetch_adsb_lol()
        if not states:
            raise RuntimeError("adsb.lol returned zero aircraft")
        logger.info("Fetched %d live states from adsb.lol", len(states))
    except Exception as exc:  # noqa: BLE001 - any live-fetch failure falls back to the simulator
        logger.warning("adsb.lol fetch failed (%s), falling back to simulator", exc)
        states = _simulator.tick()
        for state in states:
            state["source"] = "simulate_cloud"
            state["_ml_extras"] = None  # the simulator has no roll/wind/etc. to offer
        logger.info("Generated %d simulated states (region=%s)", len(states), SIMULATOR_REGION)

    if not states:
        return {"statusCode": 200, "fetched": 0}

    t_fetched = time.monotonic()
    for state in states:
        state["ingest_ts"] = ingest_ts

    # pulled out BEFORE the canonical writes below, so live/latest.json, Firehose/bronze and the
    # hourly stats all see exactly the same 16-field shape as before this feature existed — the ML
    # extras never enter the canonical contract (repo CLAUDE.md "Data contract")
    extras = [state.pop("_ml_extras", None) for state in states]

    # Newline-delimited JSON, one line per aircraft, matching the shape
    # bronze_stream.py already expects locally. Firehose caps a single
    # PutRecord at 1000 KiB — the old single-circle fetch never got close,
    # but merging 8 Europe hub-points in one invocation can (thousands of
    # aircraft in a busy poll), so this chunks into multiple records instead
    # of assuming one record always fits.
    timings["fetch"] = round(t_fetched - t0, 2)
    t = time.monotonic()
    _put_ndjson_chunks(states)
    timings["firehose"] = round(time.monotonic() - t, 2)

    t = time.monotonic()
    _write_live_snapshot(states, ingest_ts)
    timings["latest_json"] = round(time.monotonic() - t, 2)

    if SITE_BUCKET_NAME:
        t = time.monotonic()
        try:
            _write_map_snapshot(states, ingest_ts)
        except Exception:  # noqa: BLE001 - additive; the browser falls back to the API when this is missing/stale
            logger.exception("Map snapshot write failed, continuing without it this poll")
        timings["map_json"] = round(time.monotonic() - t, 2)

    t = time.monotonic()
    try:
        _update_hourly_stats(states)
    except Exception:  # noqa: BLE001 - the stats rollup is additive; never let it sink core ingestion
        logger.exception("Hourly stats update failed, continuing without it this poll")
    timings["hourly"] = round(time.monotonic() - t, 2)

    t = time.monotonic()
    try:
        _update_history(extras)
    except Exception:  # noqa: BLE001 - the ML history is additive; never let it sink core ingestion
        logger.exception("History update failed, continuing without it this poll")
    timings["history"] = round(time.monotonic() - t, 2)

    timings["total"] = round(time.monotonic() - t0, 2)
    # one greppable line per run: where the seconds go (fetch vs each S3 write);
    # see docs/improvements/06
    logger.info("PHASE_TIMINGS_S %s aircraft=%d", json.dumps(timings), len(states))

    return {"statusCode": 200, "fetched": len(states), "source": states[0]["source"]}
