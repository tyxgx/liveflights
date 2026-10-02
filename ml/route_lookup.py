"""callsign -> {origin, destination} airport info, from VRS standing data (routes.csv +
airports.csv, CC0, downloaded to data/vrs/ by ml/scratch/download_vrs.sh).

`routes.csv`'s `AirportCodes` column is the FULL scheduled route as "ORIGIN-...-DESTINATION"
(94% of rows are a simple 2-airport origin-destination; ~6% have one or more stops in between).
This is the SCHEDULE for that callsign, not proof a specific aircraft actually flew it that day -
shown to the user as "scheduled route", not asserted as fact (see
ml/scratch/build_windows_v2.py's load_dest_table, which uses only the destination end of this
same data for the ML model's destination-bearing feature; this module is the fuller version, for
DISPLAY - names/cities/IATA codes, both ends, any stops).

Usage:
    from route_lookup import load_route_lookup
    routes = load_route_lookup()  # ~620k callsigns, loaded once
    routes.get("DLH123")  # -> {"origin": {...}, "destination": {...}, "stops": [...]} | None

Memory: the unfiltered table measured ~197 MB steady-state / 447 MB peak while building it
(620,700 small dict entries - Python's per-dict overhead dominates, not the actual airport data)
- fine on a laptop, but this alone overflowed the predict Lambda's 512 MB limit (see the
2026-09-28 journal entry "REAL BUGS found in the first live Lambda invocations"). `region_box`
filters to routes touching a region at LOAD time, for callers (the live predict Lambda) that only
ever look up callsigns of aircraft currently flying in one region and never need the rest of the
world's schedules - NOT used by ml/scratch/build_windows_v2.py's load_dest_table() (training),
which deliberately keeps the full unfiltered table since a Europe-transiting flight's destination
can be anywhere on Earth.
"""

from __future__ import annotations

import csv
import glob
import os


def _load_airports(vrs_dir: str) -> dict[str, dict]:
    """ICAO code -> {code, name, city, iata, lat, lon}."""
    path = os.path.join(vrs_dir, "airports.csv")
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = row.get("Code")
            if not code:
                continue
            try:
                lat, lon = float(row["Latitude"]), float(row["Longitude"])
            except (KeyError, ValueError):
                continue
            out[code] = {
                "code": code, "name": row.get("Name") or "", "city": row.get("Location") or "",
                "iata": row.get("IATA") or "", "country": row.get("CountryISO2") or "",
                "lat": lat, "lon": lon,
            }
    return out


class RouteTable:
    """Lightweight callsign -> route lookup: stores only small (origin_code, dest_code, stop_codes)
    string tuples per callsign (cheap - no per-route dict objects), plus the ~34,128-entry airport
    dict (code -> {name, city, iata, lat, lon}, itself small). Expands to the full
    {"origin": {...}, "destination": {...}, "stops": [...]} shape only for the ONE callsign a
    caller actually asks for via `.get()`, not for all ~377k-620k routes up front.

    This is what fixed the predict Lambda's real OutOfMemory (see the 2026-09-28 journal entry):
    the old `load_route_lookup()` building ~377k-620k small dicts eagerly used 126-447 MB by
    itself; this class holds the same routes in a few tens of MB.
    """

    def __init__(self, airports: dict[str, dict], routes_compact: dict[str, tuple]) -> None:
        self._airports = airports
        self._routes = routes_compact

    def __len__(self) -> int:
        return len(self._routes)

    def get(self, callsign: str) -> dict | None:
        compact = self._routes.get(callsign)
        if compact is None:
            return None
        origin_code, dest_code, stop_codes = compact
        return {
            "origin": self._airports[origin_code],
            "destination": self._airports[dest_code],
            "stops": [self._airports[c] for c in stop_codes if c in self._airports],
        }


def load_route_table(vrs_dir: str = "data/vrs",
                     region_box: tuple[float, float, float, float] | None = None) -> RouteTable:
    """The memory-efficient form of load_route_lookup(), for a caller (the live predict Lambda)
    that only ever looks up a few thousand specific callsigns per minute and should not pay to
    materialise every route as a full dict up front. See RouteTable's docstring."""
    airports = _load_airports(vrs_dir)

    def in_box(a: dict) -> bool:
        if region_box is None:
            return True
        lat0, lat1, lon0, lon1 = region_box
        return lat0 <= a["lat"] <= lat1 and lon0 <= a["lon"] <= lon1

    routes_compact: dict[str, tuple] = {}
    routes_path = os.path.join(vrs_dir, "routes.csv")
    with open(routes_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):  # streamed, not materialised into a list first
            callsign = row.get("Callsign")
            codes = [c for c in (row.get("AirportCodes") or "").split("-") if c]
            if not callsign or len(codes) < 2:
                continue
            origin_code, dest_code = codes[0], codes[-1]
            origin, dest = airports.get(origin_code), airports.get(dest_code)
            if origin is None or dest is None:
                continue
            if region_box is not None and not (in_box(origin) or in_box(dest)):
                continue
            routes_compact[callsign] = (origin_code, dest_code, tuple(codes[1:-1]))
    return RouteTable(airports, routes_compact)


def load_route_lookup(
    vrs_dir: str = "data/vrs",
    region_box: tuple[float, float, float, float] | None = None,
) -> dict[str, dict]:
    """callsign -> {"origin", "destination", "stops": [...]}, each value an airport info dict.

    An airport code with no match in airports.csv (rare - the extractor's data validation found
    99.99% of destination codes resolve) is dropped from that route entirely (returns None for
    that callsign) rather than showing a half-populated route.

    `region_box` = (lat0, lat1, lon0, lon1): if given, keeps only routes whose ORIGIN OR DESTINATION
    (not intermediate stops) falls inside the box - e.g. a London-New York route still counts as
    "touching Europe" because London does, even though the flight itself crosses the Atlantic. See
    the module docstring for why (memory, and who should vs shouldn't use this).
    """
    routes_path = os.path.join(vrs_dir, "routes.csv")
    if not os.path.exists(routes_path):
        # some VRS downloads keep the per-country sharded files instead of the bulk CSV
        shard_files = glob.glob(
            os.path.join(vrs_dir, "standing-data", "routes", "schema-01", "*", "*.csv")
        )
        rows = []
        for fp in shard_files:
            with open(fp, encoding="utf-8-sig", newline="") as f:
                rows.extend(csv.DictReader(f))
    else:
        with open(routes_path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

    airports = _load_airports(vrs_dir)

    def in_box(a: dict) -> bool:
        if region_box is None:
            return True
        lat0, lat1, lon0, lon1 = region_box
        return lat0 <= a["lat"] <= lat1 and lon0 <= a["lon"] <= lon1

    out: dict[str, dict] = {}
    for row in rows:
        callsign = row.get("Callsign")
        codes_raw = row.get("AirportCodes") or ""
        codes = [c for c in codes_raw.split("-") if c]
        if not callsign or len(codes) < 2:
            continue
        legs = [airports.get(c) for c in codes]
        if any(a is None for a in (legs[0], legs[-1])):  # origin or destination unresolved: skip
            continue
        if region_box is not None and not (in_box(legs[0]) or in_box(legs[-1])):
            continue
        out[callsign] = {
            "origin": legs[0],
            "destination": legs[-1],
            "stops": [a for a in legs[1:-1] if a is not None],
        }
    return out


if __name__ == "__main__":
    import json
    import sys

    routes = load_route_lookup()
    print(f"{len(routes):,} callsigns with a resolvable route (origin + destination both known)")
    for cs in sys.argv[1:] or ["DLH123", "BAW406"]:
        print(cs, "->", json.dumps(routes.get(cs), indent=1))
