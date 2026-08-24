"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";
import { getProximityPairs } from "@/lib/flightInsights";
import type { LiveFlight } from "@/types/api";

/** Draws a line between any two aircraft currently within ~3nm laterally
 * and ~1,000ft vertically — pure geometry against the live snapshot, not a
 * trajectory-aware separation check. Explicitly NOT a real conflict-alert
 * system (no closing-speed prediction, no ATC separation minima, no
 * altitude-band rules) — labeled that way everywhere it's surfaced. */
export function ProximityLayer({ flights }: { flights: LiveFlight[] }) {
  const map = useMap();
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    const group = L.layerGroup().addTo(map);
    layerRef.current = group;
    return () => {
      group.remove();
      layerRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    const group = layerRef.current;
    if (!group) return;
    group.clearLayers();

    const pairs = getProximityPairs(flights);
    for (const pair of pairs) {
      if (pair.a.latitude == null || pair.a.longitude == null) continue;
      if (pair.b.latitude == null || pair.b.longitude == null) continue;
      const line = L.polyline(
        [
          [pair.a.latitude, pair.a.longitude],
          [pair.b.latitude, pair.b.longitude],
        ],
        { color: "#f43f5e", weight: 2, opacity: 0.85, dashArray: "4 4", interactive: false },
      );
      const label = `${pair.a.callsign?.trim() || pair.a.icao24} / ${pair.b.callsign?.trim() || pair.b.icao24}: ${pair.lateralNm.toFixed(1)}nm apart, ${Math.round(pair.altitudeDiffFt)}ft altitude difference`;
      line.bindTooltip(label, { sticky: true, className: "proximity-tooltip" });
      group.addLayer(line);
    }
  }, [flights]);

  return null;
}
