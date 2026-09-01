"use client";

import { Polyline, CircleMarker, Tooltip } from "react-leaflet";
import { MAJOR_AIRPORTS } from "@/components/map/AirportLayer";
import type { Corridor } from "@/types/api";

/** Corridors update on a ~minutes cadence (a batch ML run), not every 3s,
 * so plain declarative react-leaflet components are fine here — the
 * imperative-registry treatment in AircraftLayer is specifically because
 * aircraft update every 3s at 150+ instances.
 */
function corridorLabel(c: Corridor): string | null {
  const [start, end] = c.airports ?? [null, null];
  if (start && end) return `${start} ↔ ${end} (approx.)`;
  if (start) return `near ${start} (approx.)`;
  if (end) return `near ${end} (approx.)`;
  if (c.hub_airport) return `${c.hub_airport} hub traffic (approx.)`;
  return null;
}

export function CorridorLayer({ corridors }: { corridors: Corridor[] }) {
  return (
    <>
      {corridors.map((c) => {
        const label = corridorLabel(c);
        return (
          <Polyline
            key={c.corridor_id}
            positions={c.polyline}
            pathOptions={{ color: "#22d3ee", weight: 1.5, opacity: 0.45 }}
          >
            {label && <Tooltip sticky>{label}</Tooltip>}
          </Polyline>
        );
      })}
      {corridors.map((c) => (
        <CircleMarker
          key={`centroid-${c.corridor_id}`}
          center={[c.centroid_lat, c.centroid_lon]}
          radius={2}
          pathOptions={{ color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.6, opacity: 0.6 }}
        />
      ))}
      {/* Hub-only matches (see Corridor.hub_airport): the corridor's own
          ends point away from the airport in both directions, so instead
          of distorting the main polyline's shape, draw a short dashed
          spur from centroid to airport -- visually "this corridor's
          traffic sits in this airport's terminal area" without claiming
          the main route line itself goes there. */}
      {corridors
        .filter((c) => c.hub_airport)
        .map((c) => {
          const ap = MAJOR_AIRPORTS.find((a) => a.iata === c.hub_airport);
          if (!ap) return null;
          return (
            <Polyline
              key={`hub-${c.corridor_id}`}
              positions={[
                [c.centroid_lat, c.centroid_lon],
                [ap.lat, ap.lon],
              ]}
              pathOptions={{
                color: "#fbbf24",
                weight: 1,
                opacity: 0.5,
                dashArray: "2,4",
              }}
            />
          );
        })}
    </>
  );
}
