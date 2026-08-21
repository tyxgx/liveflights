"""EventBridge-triggered Lambda: fetch live adsb.lol states, batch into one Firehose record.

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
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

from ingestion.airports import get_airports
from ingestion.schemas.adsb_lol_mapping import map_to_flight_state_dict
from ingestion.simulator import FlightSimulator

logger = logging.getLogger()
logger.setLevel(logging.INFO)

firehose = boto3.client("firehose")
dynamodb = boto3.resource("dynamodb")

FIREHOSE_STREAM_NAME = os.environ["FIREHOSE_STREAM_NAME"]
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
SIMULATOR_REGION = os.environ.get("SIMULATOR_REGION", "india")
SIMULATOR_AIRCRAFT_COUNT = int(os.environ.get("SIMULATOR_AIRCRAFT_COUNT", "40"))
SIMULATOR_ANOMALY_RATE = float(os.environ.get("SIMULATOR_ANOMALY_RATE", "0.02"))
STATE_TTL_SECONDS = 15 * 60  # DynamoDB item self-deletes 15 min after last sighting

# --- Departure/arrival detection + predicted-destination/ETA ---
#
# ADS-B (what adsb.lol relays) carries only the current state vector — it
# has no flight-plan field, so "where did this aircraft come from / where
# is it going" isn't in the raw data at all, from any source this project
# uses. Departure is derivable from OUR OWN data (a ground->airborne
# transition, matched against a known airport); arrival is the same in
# reverse. A destination *while still airborne* can only ever be an
# estimate — the heading-based heuristic below, not a fact — and is
# labeled as such everywhere it's surfaced (predicted_arrival_* fields).
TRAJECTORIES_TABLE_NAME = os.environ.get("TRAJECTORIES_TABLE_NAME")
FLIGHT_ROUTES_TABLE_NAME = os.environ.get("FLIGHT_ROUTES_TABLE_NAME")
AIRPORT_MATCH_RADIUS_KM = float(os.environ.get("AIRPORT_MATCH_RADIUS_KM", "50"))
TRAJECTORY_TTL_SECONDS = 48 * 60 * 60  # generous headroom over any real flight duration here
PREDICTION_MIN_RANGE_KM = 100.0   # closer than this, "predicted destination" would just restate departure
PREDICTION_MAX_RANGE_KM = 3000.0  # further than this isn't a plausible single-hop remaining leg
PREDICTION_MAX_BEARING_DIFF_DEG = 60.0  # candidate must be roughly "ahead", not behind/beside

_AIRPORTS = get_airports(SIMULATOR_REGION if SIMULATOR_REGION in ("europe", "us", "india") else "europe")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def _nearest_airport(lat: float, lon: float) -> tuple[dict, float] | tuple[None, None]:
    """Nearest known airport within AIRPORT_MATCH_RADIUS_KM, or (None, None).

    Only ~20 major airports per region (see ingestion/airports.py) — a real,
    stated limitation: a genuine departure/arrival at a smaller regional
    airport won't match anything here and is honestly left unmatched, not
    guessed.
    """
    best, best_dist = None, None
    for ap in _AIRPORTS:
        d = _haversine_km(lat, lon, ap.lat, ap.lon)
        if best_dist is None or d < best_dist:
            best, best_dist = ap, d
    if best_dist is not None and best_dist <= AIRPORT_MATCH_RADIUS_KM:
        return best, best_dist
    return None, None


def _predict_destination(
    lat: float, lon: float, heading_deg: float, exclude_iata: str | None
) -> tuple[dict, float] | tuple[None, None]:
    """Heuristic only, never a fact: among known airports in a plausible
    remaining-leg range, ahead of the current heading (not behind or
    beside), pick the one whose bearing best matches current track. With
    only ~20 candidate airports in range this is coarse by construction —
    it's the honest ceiling of what's derivable without a real flight-plan
    data source (see module docstring)."""
    best, best_diff, best_dist = None, None, None
    for ap in _AIRPORTS:
        if ap.iata == exclude_iata:
            continue
        dist = _haversine_km(lat, lon, ap.lat, ap.lon)
        if not (PREDICTION_MIN_RANGE_KM <= dist <= PREDICTION_MAX_RANGE_KM):
            continue
        bearing = _bearing_deg(lat, lon, ap.lat, ap.lon)
        diff = abs((bearing - heading_deg + 180) % 360 - 180)  # smallest signed angular diff
        if diff > PREDICTION_MAX_BEARING_DIFF_DEG:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff, best_dist = ap, diff, dist
    if best is None:
        return None, None
    return best, best_dist


def _get_previous_states(icao24_list: list[str]) -> dict[str, dict]:
    """Batch-fetch each aircraft's state from BEFORE this poll overwrites it
    — the only way to notice a ground<->airborne transition at all.
    DynamoDB's BatchGetItem caps at 100 keys/call, so this chunks."""
    if not icao24_list:
        return {}
    table_name = DYNAMODB_TABLE_NAME
    found: dict[str, dict] = {}
    for i in range(0, len(icao24_list), 100):
        chunk = icao24_list[i : i + 100]
        resp = dynamodb.batch_get_item(
            RequestItems={table_name: {"Keys": [{"icao24": icao} for icao in chunk]}}
        )
        for item in resp.get("Responses", {}).get(table_name, []):
            found[item["icao24"]] = item
    return found

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
            states.append(map_to_flight_state_dict(row, now=now, source="adsb_lol"))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Dropping malformed adsb.lol row: %s", exc)
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


def _track_routes(states: list[dict[str, Any]], ingest_epoch: int) -> None:
    """Mutates each state dict in place with departure/predicted-destination
    fields, and writes trajectory points + finalized flight_routes as a
    side effect. See the module docstring above for what's a fact
    (departure, once matched) vs. an estimate (predicted destination/ETA,
    always — see _predict_destination)."""
    if not TRAJECTORIES_TABLE_NAME or not FLIGHT_ROUTES_TABLE_NAME:
        return  # not configured (e.g. an un-migrated deploy) — skip, don't fail ingestion over it

    previous = _get_previous_states([s["icao24"] for s in states])
    trajectories = dynamodb.Table(TRAJECTORIES_TABLE_NAME)
    routes = dynamodb.Table(FLIGHT_ROUTES_TABLE_NAME)

    with trajectories.batch_writer(overwrite_by_pkeys=["icao24", "timestamp"]) as traj_batch:
        for state in states:
            icao24 = state["icao24"]
            prev = previous.get(icao24)
            was_on_ground = bool(prev["on_ground"]) if prev and "on_ground" in prev else None
            is_on_ground = bool(state.get("on_ground"))
            lat, lon = state.get("latitude"), state.get("longitude")

            if was_on_ground is True and is_on_ground is False and lat is not None and lon is not None:
                # Departure: this poll is the first airborne sighting after
                # a ground sighting — match against a known airport.
                airport, dist_km = _nearest_airport(lat, lon)
                if airport is not None:
                    state["departure_iata"] = airport.iata
                    state["departure_country"] = airport.country
                    state["departure_time"] = ingest_epoch
                    logger.info(
                        "Departure detected: %s from %s (%.1fkm match)",
                        state.get("callsign", icao24), airport.iata, dist_km,
                    )
            elif prev and prev.get("departure_iata") and is_on_ground is False:
                # Mid-flight: carry the departure fields forward — each poll
                # overwrites the whole item, so without this the departure
                # info set above would vanish on the very next poll.
                state["departure_iata"] = prev["departure_iata"]
                state["departure_country"] = prev.get("departure_country")
                state["departure_time"] = int(prev["departure_time"])

            if was_on_ground is False and is_on_ground is True and lat is not None and lon is not None:
                # Arrival: finalize the route (if departure was ever known)
                # and don't carry departure/prediction fields onto a
                # now-landed, at-rest aircraft.
                airport, dist_km = _nearest_airport(lat, lon)
                if airport is not None and prev and prev.get("departure_iata"):
                    dep_time = int(prev["departure_time"])
                    duration_min = max((ingest_epoch - dep_time) / 60.0, 0.01)
                    dep_ap = next((a for a in _AIRPORTS if a.iata == prev["departure_iata"]), None)
                    distance_km = _haversine_km(dep_ap.lat, dep_ap.lon, lat, lon) if dep_ap else None
                    routes.put_item(Item={
                        "icao24": icao24,
                        "arrival_time": ingest_epoch,
                        "callsign": state.get("callsign") or "",
                        "departure_iata": prev["departure_iata"],
                        "departure_time": dep_time,
                        "arrival_iata": airport.iata,
                        "duration_minutes": Decimal(str(round(duration_min, 1))),
                        **({"distance_km": Decimal(str(round(distance_km, 1)))} if distance_km else {}),
                        **({"avg_speed_kmh": Decimal(str(round(distance_km / (duration_min / 60), 1)))}
                           if distance_km else {}),
                    })
                    logger.info(
                        "Arrival detected: %s %s->%s in %.0fmin",
                        state.get("callsign", icao24), prev["departure_iata"], airport.iata, duration_min,
                    )
                state.pop("departure_iata", None)
                state.pop("departure_country", None)
                state.pop("departure_time", None)

            # Predicted destination + ETA: only while airborne with a known
            # departure, and only ever an estimate (see _predict_destination).
            heading = state.get("true_track")
            if (
                is_on_ground is False
                and state.get("departure_iata")
                and heading is not None
                and lat is not None
                and lon is not None
            ):
                pred_airport, pred_dist_km = _predict_destination(lat, lon, heading, state["departure_iata"])
                if pred_airport is not None:
                    state["predicted_arrival_iata"] = pred_airport.iata
                    state["predicted_arrival_country"] = pred_airport.country
                    speed_kmh = (state.get("velocity") or 0) * 3.6  # velocity is m/s per FlightState
                    if speed_kmh > 5:  # avoid a near-infinite ETA off a near-zero speed reading
                        # Plain float, not Decimal: `state` still needs to be
                        # JSON-serializable for the Firehose/bronze write
                        # below — Decimal only belongs in the DynamoDB-bound
                        # trajectory/route items constructed separately.
                        state["eta_minutes"] = round(pred_dist_km / speed_kmh * 60, 1)

            if state.get("departure_iata") and is_on_ground is False:
                # altitude_ft doesn't exist yet at this stage (it's a
                # silver-enrichment column, see docs/architecture.md) —
                # baro_altitude/geo_altitude here are meters, per FlightState.
                alt_m = state.get("baro_altitude") or state.get("geo_altitude")
                traj_batch.put_item(Item={
                    "icao24": icao24,
                    "timestamp": ingest_epoch,
                    "latitude": Decimal(str(lat)) if lat is not None else None,
                    "longitude": Decimal(str(lon)) if lon is not None else None,
                    "altitude_ft": Decimal(str(round(alt_m * 3.28084, 1))) if alt_m else None,
                    "expires_at": ingest_epoch + TRAJECTORY_TTL_SECONDS,
                })


def _write_dynamodb_latest(states: list[dict[str, Any]]) -> None:
    """Upsert latest state per icao24, with a TTL so stale aircraft self-delete."""
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    expires_at = int(time.time()) + STATE_TTL_SECONDS
    with table.batch_writer(overwrite_by_pkeys=["icao24"]) as batch:
        for state in states:
            item = {
                k: (Decimal(str(v)) if isinstance(v, float) else v)
                for k, v in state.items()
                if v is not None
            }
            item["icao24"] = state["icao24"]
            item["expires_at"] = expires_at
            batch.put_item(Item=item)


def handler(event: dict, context: object) -> dict:
    """EventBridge entrypoint: fetch live states (or simulate), fan out to Firehose+DynamoDB."""
    ingest_ts = datetime.now(UTC).isoformat()

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
        logger.info("Generated %d simulated states (region=%s)", len(states), SIMULATOR_REGION)

    if not states:
        return {"statusCode": 200, "fetched": 0}

    for state in states:
        state["ingest_ts"] = ingest_ts

    try:
        _track_routes(states, ingest_epoch=int(time.time()))
    except Exception:  # noqa: BLE001 - route tracking is additive; never let it sink core ingestion
        logger.exception("Route/trajectory tracking failed, continuing without it this poll")

    # Newline-delimited JSON, one line per aircraft, matching the shape
    # bronze_stream.py already expects locally. Firehose caps a single
    # PutRecord at 1000 KiB — the old single-circle fetch never got close,
    # but merging 8 Europe hub-points in one invocation can (thousands of
    # aircraft in a busy poll), so this chunks into multiple records instead
    # of assuming one record always fits.
    _put_ndjson_chunks(states)

    _write_dynamodb_latest(states)

    return {"statusCode": 200, "fetched": len(states), "source": states[0]["source"]}
