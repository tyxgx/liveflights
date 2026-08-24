// Every function here computes straight off the live flight list the
// dashboard already has in memory (useFlightsPolling's `flights`) — no new
// API calls, no ML, no training. Just real-time counting, grouping, and
// geometry over the fields adsb.lol already gives us. Kept as pure
// functions (not hooks) so they're trivially testable and reusable between
// the map layers and the Insights panel.
import { metersToFeet } from "@/lib/format";
import { HUB_POINTS, haversineNm } from "@/lib/hubs";
import { possibleMilitaryLabel } from "@/lib/militaryRanges";
import type { LiveFlight } from "@/types/api";

// --- Emergency squawks -----------------------------------------------

export const EMERGENCY_SQUAWKS: Record<string, string> = {
  "7500": "Hijack",
  "7600": "Radio failure",
  "7700": "General emergency",
};

export interface EmergencyFlight {
  flight: LiveFlight;
  squawk: string;
  meaning: string;
}

export function getEmergencySquawks(flights: LiveFlight[]): EmergencyFlight[] {
  const out: EmergencyFlight[] = [];
  for (const f of flights) {
    const raw = f.squawk;
    if (raw && EMERGENCY_SQUAWKS[raw]) {
      out.push({ flight: f, squawk: raw, meaning: EMERGENCY_SQUAWKS[raw] });
    }
  }
  return out;
}

// --- Climb / descend / level split -------------------------------------

export interface VerticalMix {
  climbing: number;
  descending: number;
  level: number;
  unknown: number;
}

// ±1.5 m/s (~300 ft/min) dead zone around zero — real cruise flight isn't
// perfectly flat, so a tighter threshold would mislabel gentle altitude
// hold as "climbing"/"descending".
const LEVEL_THRESHOLD_MPS = 1.5;

export function getVerticalMix(flights: LiveFlight[]): VerticalMix {
  const mix: VerticalMix = { climbing: 0, descending: 0, level: 0, unknown: 0 };
  for (const f of flights) {
    if (f.on_ground) continue;
    if (f.vertical_rate == null) {
      mix.unknown += 1;
    } else if (f.vertical_rate > LEVEL_THRESHOLD_MPS) {
      mix.climbing += 1;
    } else if (f.vertical_rate < -LEVEL_THRESHOLD_MPS) {
      mix.descending += 1;
    } else {
      mix.level += 1;
    }
  }
  return mix;
}

// --- Ground / airborne split --------------------------------------------

export function getGroundAirSplit(flights: LiveFlight[]): { ground: number; airborne: number } {
  let ground = 0;
  let airborne = 0;
  for (const f of flights) {
    if (f.on_ground) ground += 1;
    else airborne += 1;
  }
  return { ground, airborne };
}

// --- Leaderboards ---------------------------------------------------------

// Sanity caps against ADS-B sensor noise, tuned for what this dataset
// actually is — commercial/GA traffic over Europe, not military test
// flights. A first pass capped at 4,000 km/h / 30,000m altitude (SR-71-
// class limits) still let obviously-bad values through (observed: 1,765
// km/h and 55,900ft on ordinary airline callsigns, neither plausible for
// scheduled commercial traffic). Tightened using a real documented
// extreme instead of a generic ceiling: ADS-B velocity is GROUND speed,
// and a Virgin Atlantic 787 recorded 1,327 km/h ground speed in a Feb 2024
// jet-stream tailwind — a genuine, unusual outlier, not noise. 1,500 km/h
// keeps that kind of real extreme while still excluding clear garbage.
// 51,000ft covers the highest civil operating ceilings in regular service
// (some Gulfstream/Global Express business jets) with headroom.
const MAX_PLAUSIBLE_VELOCITY_MPS = 417; // ~1,500 km/h
const MAX_PLAUSIBLE_ALTITUDE_M = 15545; // ~51,000ft

export function getSpeedLeaderboard(flights: LiveFlight[], n = 5): LiveFlight[] {
  return [...flights]
    .filter((f) => !f.on_ground && f.velocity != null && f.velocity <= MAX_PLAUSIBLE_VELOCITY_MPS)
    .sort((a, b) => (b.velocity ?? 0) - (a.velocity ?? 0))
    .slice(0, n);
}

export function getAltitudeLeaderboard(flights: LiveFlight[], n = 5): LiveFlight[] {
  return [...flights]
    .filter(
      (f) => !f.on_ground && f.baro_altitude != null && f.baro_altitude <= MAX_PLAUSIBLE_ALTITUDE_M,
    )
    .sort((a, b) => (b.baro_altitude ?? 0) - (a.baro_altitude ?? 0))
    .slice(0, n);
}

// --- Traffic-flow rose (heading histogram) --------------------------------

const COMPASS_LABELS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

export interface RosePoint {
  direction: string;
  count: number;
}

/** Buckets true_track into 8 compass directions. This is the "corridors
 * without ML" alternative — a real descriptive stat (which way is traffic
 * actually flowing right now), not a trained model, so it stays live and
 * meaningful even with corridor discovery paused. */
export function getHeadingRose(flights: LiveFlight[]): RosePoint[] {
  const counts = new Array(8).fill(0);
  for (const f of flights) {
    if (f.on_ground || f.true_track == null) continue;
    const idx = Math.round(f.true_track / 45) % 8;
    counts[idx] += 1;
  }
  return COMPASS_LABELS.map((direction, i) => ({ direction, count: counts[i] }));
}

// --- Hub coverage health ---------------------------------------------------

export interface HubHealth {
  id: string;
  label: string;
  count: number;
}

/** How many currently-live aircraft fall within each of the 8 configured
 * hub circles — not a distinct field the API sends, just geometry against
 * the same coordinates the ingest Lambda itself polls. A hub reading ~0
 * while others show normal traffic is a real signal that point's fetch is
 * failing, not that the region is genuinely empty. */
export function getHubHealth(flights: LiveFlight[]): HubHealth[] {
  return HUB_POINTS.map((hub) => {
    let count = 0;
    for (const f of flights) {
      if (f.latitude == null || f.longitude == null) continue;
      if (haversineNm(hub.lat, hub.lon, f.latitude, f.longitude) <= hub.distNm) count += 1;
    }
    return { id: hub.id, label: hub.label, count };
  });
}

// --- Density grid (for the map heatmap layer) -----------------------------

export interface DensityCell {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
  count: number;
}

/** Buckets aircraft into a fixed-size lat/lon grid so the map can render a
 * density overlay without a heatmap library — plain colored rectangles,
 * one per non-empty cell. */
export function getDensityGrid(flights: LiveFlight[], cellSizeDeg = 1): DensityCell[] {
  const cells = new Map<string, number>();
  for (const f of flights) {
    if (f.latitude == null || f.longitude == null) continue;
    const latCell = Math.floor(f.latitude / cellSizeDeg);
    const lonCell = Math.floor(f.longitude / cellSizeDeg);
    const key = `${latCell}:${lonCell}`;
    cells.set(key, (cells.get(key) ?? 0) + 1);
  }
  const out: DensityCell[] = [];
  for (const [key, count] of cells) {
    const [latCellStr, lonCellStr] = key.split(":");
    const latCell = Number(latCellStr);
    const lonCell = Number(lonCellStr);
    out.push({
      latMin: latCell * cellSizeDeg,
      latMax: (latCell + 1) * cellSizeDeg,
      lonMin: lonCell * cellSizeDeg,
      lonMax: (lonCell + 1) * cellSizeDeg,
      count,
    });
  }
  return out;
}

// --- Proximity pairs --------------------------------------------------------

export interface ProximityPair {
  a: LiveFlight;
  b: LiveFlight;
  lateralNm: number;
  altitudeDiffFt: number;
}

const PROXIMITY_LATERAL_NM = 3;
const PROXIMITY_ALTITUDE_FT = 1000;
const GRID_CELL_DEG = 0.5; // ~30nm at mid-latitudes — comfortably bigger than the 3nm threshold

/** Flags aircraft pairs currently close in both lateral distance and
 * altitude — pure geometry, not a trained model, and explicitly NOT a real
 * separation-loss/conflict alert (no ATC-grade vertical/lateral separation
 * rules, no trajectory prediction, no altitude-band awareness). A spatial
 * grid keeps this from being an O(n²) scan across thousands of aircraft:
 * only pairs sharing or neighboring a grid cell are ever compared. */
export function getProximityPairs(flights: LiveFlight[]): ProximityPair[] {
  const airborne = flights.filter(
    (f) => !f.on_ground && f.latitude != null && f.longitude != null && f.baro_altitude != null,
  );

  const grid = new Map<string, LiveFlight[]>();
  const cellKey = (lat: number, lon: number) =>
    `${Math.floor(lat / GRID_CELL_DEG)}:${Math.floor(lon / GRID_CELL_DEG)}`;

  for (const f of airborne) {
    const key = cellKey(f.latitude!, f.longitude!);
    const bucket = grid.get(key);
    if (bucket) bucket.push(f);
    else grid.set(key, [f]);
  }

  const seen = new Set<string>();
  const pairs: ProximityPair[] = [];

  for (const f of airborne) {
    const cellLat = Math.floor(f.latitude! / GRID_CELL_DEG);
    const cellLon = Math.floor(f.longitude! / GRID_CELL_DEG);
    for (let dLat = -1; dLat <= 1; dLat++) {
      for (let dLon = -1; dLon <= 1; dLon++) {
        const bucket = grid.get(`${cellLat + dLat}:${cellLon + dLon}`);
        if (!bucket) continue;
        for (const g of bucket) {
          if (f.icao24 === g.icao24) continue;
          const pairKey = [f.icao24, g.icao24].sort().join("-");
          if (seen.has(pairKey)) continue;
          const lateralNm = haversineNm(f.latitude!, f.longitude!, g.latitude!, g.longitude!);
          if (lateralNm > PROXIMITY_LATERAL_NM) continue;
          const altA = metersToFeet(f.baro_altitude) ?? 0;
          const altB = metersToFeet(g.baro_altitude) ?? 0;
          const altitudeDiffFt = Math.abs(altA - altB);
          if (altitudeDiffFt > PROXIMITY_ALTITUDE_FT) continue;
          seen.add(pairKey);
          pairs.push({ a: f, b: g, lateralNm, altitudeDiffFt });
        }
      }
    }
  }
  return pairs;
}

// --- Possible-military ------------------------------------------------------

export { possibleMilitaryLabel };
