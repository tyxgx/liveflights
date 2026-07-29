"use client";

import { Polyline, CircleMarker } from "react-leaflet";
import type { Corridor } from "@/types/api";

/** Corridors update on a ~minutes cadence (a batch ML run), not every 3s,
 * so plain declarative react-leaflet components are fine here — the
 * imperative-registry treatment in AircraftLayer is specifically because
 * aircraft update every 3s at 150+ instances.
 */
export function CorridorLayer({ corridors }: { corridors: Corridor[] }) {
  return (
    <>
      {corridors.map((c) => (
        <Polyline
          key={c.corridor_id}
          positions={c.polyline}
          pathOptions={{ color: "#22d3ee", weight: 1.5, opacity: 0.45 }}
        />
      ))}
      {corridors.map((c) => (
        <CircleMarker
          key={`centroid-${c.corridor_id}`}
          center={[c.centroid_lat, c.centroid_lon]}
          radius={2}
          pathOptions={{ color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.6, opacity: 0.6 }}
        />
      ))}
    </>
  );
}
