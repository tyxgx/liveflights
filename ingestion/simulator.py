"""Synthetic aircraft-state generator.

Moves simulated aircraft along great-circle routes between real airport
coordinates, with a plausible climb/cruise/descent altitude profile and a
small rate of injected anomalies. Requires no external network access, so the
whole pipeline can be demoed offline.
"""

from __future__ import annotations

import math
import random
import string
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingestion.airports import Airport, get_airports

if TYPE_CHECKING:
    from ingestion.schemas.flight_state import FlightState

EARTH_RADIUS_M = 6_371_000.0
CRUISE_ALTITUDE_M = 10_500.0
CRUISE_SPEED_MPS = 230.0
CLIMB_DESCENT_FRACTION = 0.12  # portion of the route spent climbing/descending


def _haversine_distance_m(a: Airport, b: Airport) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _great_circle_point(a: Airport, b: Airport, fraction: float) -> tuple[float, float]:
    """Interpolate a point along the great-circle path from a to b at [0, 1]."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    d = _haversine_distance_m(a, b) / EARTH_RADIUS_M
    if d == 0:
        return a.lat, a.lon
    sin_d = math.sin(d)
    A = math.sin((1 - fraction) * d) / sin_d
    B = math.sin(fraction * d) / sin_d
    x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
    y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
    z = A * math.sin(lat1) + B * math.sin(lat2)
    lat = math.atan2(z, math.sqrt(x**2 + y**2))
    lon = math.atan2(y, x)
    return math.degrees(lat), math.degrees(lon)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _random_icao24(rng: random.Random) -> str:
    return "".join(rng.choice(string.hexdigits.lower()[:16]) for _ in range(6))


# ICAO callsign prefixes weighted by approximate real-world share of each
# carrier's domestic/regional traffic, so simulated flights actually match
# `streaming/utils/airlines.py`'s AIRLINE_PREFIXES lookup instead of resolving
# to "Unknown/Other". Go First (GOW) is deliberately excluded here even though
# it's still in the airlines lookup table — it ceased operations in 2023 and
# would be wrong to spawn as if it were flying.
_CALLSIGN_PREFIXES_BY_REGION: dict[str, list[tuple[str, int]]] = {
    "europe": [
        ("RYR", 25), ("EZY", 15), ("DLH", 12), ("BAW", 8), ("AFR", 7),
        ("KLM", 6), ("WZZ", 8), ("VLG", 4), ("IBE", 4), ("SWR", 3),
        ("AUA", 2), ("SAS", 2), ("FIN", 1), ("LOT", 1), ("TAP", 1), ("THY", 5),
    ],
    "us": [
        ("AAL", 20), ("UAL", 18), ("DAL", 18), ("SWA", 20), ("JBU", 6),
        ("ASA", 6), ("FDX", 4), ("UPS", 4), ("ACA", 4),
    ],
    "india": [
        ("IGO", 50), ("AIC", 20), ("VTI", 10), ("SEJ", 8), ("AKJ", 6),
        ("AXB", 4), ("LLR", 2),
    ],
}
_CALLSIGN_PREFIXES_BY_REGION["all"] = [
    pair for prefixes in _CALLSIGN_PREFIXES_BY_REGION.values() for pair in prefixes
]


def _random_callsign(rng: random.Random, region: str) -> str:
    weighted = _CALLSIGN_PREFIXES_BY_REGION.get(region, _CALLSIGN_PREFIXES_BY_REGION["all"])
    prefixes, weights = zip(*weighted, strict=True)
    prefix = rng.choices(prefixes, weights=weights, k=1)[0]
    return f"{prefix}{rng.randint(100, 9999)}"


@dataclass
class SimulatedAircraft:
    icao24: str
    callsign: str
    origin: Airport
    destination: Airport
    departed_at: float
    duration_s: float
    total_distance_m: float = field(init=False)

    def __post_init__(self) -> None:
        self.total_distance_m = _haversine_distance_m(self.origin, self.destination)

    def fraction_complete(self, now: float) -> float:
        if self.duration_s <= 0:
            return 1.0
        return max(0.0, min(1.0, (now - self.departed_at) / self.duration_s))

    def state_at(self, now: float, rng: random.Random) -> dict:
        f = self.fraction_complete(now)
        lat, lon = _great_circle_point(self.origin, self.destination, f)

        # Altitude/phase profile: climb -> cruise -> descend.
        if f < CLIMB_DESCENT_FRACTION:
            phase_frac = f / CLIMB_DESCENT_FRACTION
            altitude = CRUISE_ALTITUDE_M * phase_frac
            vertical_rate = CRUISE_ALTITUDE_M / (self.duration_s * CLIMB_DESCENT_FRACTION)
            on_ground = f < 0.01
        elif f > 1 - CLIMB_DESCENT_FRACTION:
            phase_frac = (1 - f) / CLIMB_DESCENT_FRACTION
            altitude = CRUISE_ALTITUDE_M * phase_frac
            vertical_rate = -CRUISE_ALTITUDE_M / (self.duration_s * CLIMB_DESCENT_FRACTION)
            on_ground = f > 0.99
        else:
            altitude = CRUISE_ALTITUDE_M + rng.uniform(-50, 50)
            vertical_rate = rng.uniform(-0.5, 0.5)
            on_ground = False

        velocity = 0.0 if on_ground else CRUISE_SPEED_MPS + rng.uniform(-15, 15)

        if f < 1.0 and f > 0.0:
            look_ahead = min(1.0, f + 0.01)
            lat2, lon2 = _great_circle_point(self.origin, self.destination, look_ahead)
            track = _bearing_deg(lat, lon, lat2, lon2)
        else:
            o, d = self.origin, self.destination
            track = _bearing_deg(o.lat, o.lon, d.lat, d.lon)

        return {
            "icao24": self.icao24,
            "callsign": self.callsign,
            "origin_country": self.origin.country,
            "time_position": int(now),
            "last_contact": int(now),
            "longitude": round(lon, 5),
            "latitude": round(lat, 5),
            "baro_altitude": round(altitude, 1),
            "on_ground": on_ground,
            "velocity": round(velocity, 1),
            "true_track": round(track, 1),
            "vertical_rate": round(vertical_rate, 2),
            "geo_altitude": round(altitude + rng.uniform(-10, 10), 1),
            "squawk": f"{rng.randint(1000, 7777):04d}",
            "spi": False,
            "position_source": 0,
        }


class FlightSimulator:
    """Maintains a pool of simulated aircraft and steps them forward in time."""

    def __init__(
        self,
        aircraft_count: int,
        anomaly_rate: float,
        seed: int | None = None,
        region: str = "india",
    ) -> None:
        self.anomaly_rate = anomaly_rate
        self.rng = random.Random(seed)
        self.region = region
        self.airports: list[Airport] = get_airports(region)
        self.aircraft: list[SimulatedAircraft] = [self._spawn() for _ in range(aircraft_count)]

    def _spawn(self, origin: Airport | None = None) -> SimulatedAircraft:
        origin = origin or self.rng.choice(self.airports)
        destination = self.rng.choice([a for a in self.airports if a != origin])
        distance_m = _haversine_distance_m(origin, destination)
        duration_s = max(600.0, distance_m / CRUISE_SPEED_MPS * self.rng.uniform(1.05, 1.25))
        now = time.time()
        return SimulatedAircraft(
            icao24=_random_icao24(self.rng),
            callsign=_random_callsign(self.rng, self.region),
            origin=origin,
            destination=destination,
            departed_at=now - duration_s * self.rng.uniform(0, 0.95),
            duration_s=duration_s,
        )

    def _inject_anomaly(self, state: dict) -> dict:
        anomaly_type = self.rng.choice(
            [
                "altitude_spike",
                "velocity_spike",
                "vertical_rate_spike",
                "missing_position",
                "squawk_emergency",
            ]
        )
        if anomaly_type == "altitude_spike":
            state["baro_altitude"] = (state["baro_altitude"] or 0) + self.rng.uniform(4000, 8000)
        elif anomaly_type == "velocity_spike":
            state["velocity"] = (state["velocity"] or 0) + self.rng.uniform(200, 400)
        elif anomaly_type == "vertical_rate_spike":
            state["vertical_rate"] = self.rng.choice([-1, 1]) * self.rng.uniform(30, 60)
        elif anomaly_type == "missing_position":
            state["longitude"] = None
            state["latitude"] = None
        elif anomaly_type == "squawk_emergency":
            state["squawk"] = self.rng.choice(["7500", "7600", "7700"])
        return state

    def tick(self) -> list[dict]:
        """Advance simulated time and return one state dict per aircraft."""
        now = time.time()
        states = []
        for i, ac in enumerate(self.aircraft):
            state = ac.state_at(now, self.rng)
            if self.rng.random() < self.anomaly_rate:
                state = self._inject_anomaly(state)
            states.append(state)
            if ac.fraction_complete(now) >= 1.0:
                self.aircraft[i] = self._spawn(origin=ac.destination)
        return states

    def tick_flight_states(self) -> list[FlightState]:
        # Imported lazily so `tick()` alone (dicts only) works without pydantic
        # installed — the AWS ingest Lambda vendors this module but not pydantic.
        from ingestion.schemas.flight_state import FlightState

        return [FlightState(source="simulate", **s) for s in self.tick()]
