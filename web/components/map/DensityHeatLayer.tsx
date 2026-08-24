"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";
import { getDensityGrid } from "@/lib/flightInsights";
import type { LiveFlight } from "@/types/api";

const CELL_SIZE_DEG = 1; // ~110km at the equator, narrower N-S near the poles

/** Colored rectangles, one per non-empty grid cell — deliberately not the
 * usual radial-gradient "heatmap" plugin (would be a new dependency); a
 * discrete grid is honest about its own resolution too (a smooth gradient
 * implies more positional precision than a 1°-cell count actually has). */
export function DensityHeatLayer({ flights }: { flights: LiveFlight[] }) {
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

    const cells = getDensityGrid(flights, CELL_SIZE_DEG);
    if (cells.length === 0) return;
    const maxCount = Math.max(...cells.map((c) => c.count));

    for (const cell of cells) {
      const intensity = cell.count / maxCount; // 0..1
      const rect = L.rectangle(
        [
          [cell.latMin, cell.lonMin],
          [cell.latMax, cell.lonMax],
        ],
        {
          stroke: false,
          fillColor: "#f43f5e",
          fillOpacity: 0.08 + intensity * 0.35,
          interactive: false,
        },
      );
      group.addLayer(rect);
    }
  }, [flights]);

  return null;
}
