"use client";

import { CircleMarker, Polyline, Tooltip } from "react-leaflet";
import { greatCirclePoints } from "@/lib/geo";
import type { AircraftDetailResponse } from "@/types/api";

/**
 * The selected aircraft's real trail (solid), the GRU model's predicted 5-minute path (dashed,
 * curved through the 30 points it actually returns — not a single guessed endpoint), and the
 * scheduled origin→destination route (a faint great-circle arc) — all three drawn together so the
 * "did we predict this right" comparison is visible at a glance, not spread across separate views.
 * Replaces the old GhostTrailLayer (single heading-based ghost point from the paused local-only
 * trajectory model) with the real, live-evaluated GRU model wired up 2026-09-28.
 *
 * `accent` swaps the prediction color (trail/route stay neutral either way) — compare mode
 * (2026-09-29) renders this twice, once per aircraft, and the two need to read as clearly separate
 * at a glance rather than both drawing identical cyan lines on top of each other.
 */
export function PredictionLayer({
  detail,
  accent = "#22d3ee",
}: {
  detail: AircraftDetailResponse | null;
  accent?: string;
}) {
  if (!detail) return null;
  const { trail, prediction, route } = detail;

  const trailPositions: [number, number][] = trail
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => [p.lat, p.lon]);

  return (
    <>
      {trailPositions.length > 1 && (
        <Polyline
          positions={trailPositions}
          pathOptions={{ color: "#e2e8f0", weight: 2, opacity: 0.8 }}
        />
      )}

      {route && (
        <Polyline
          positions={greatCirclePoints(
            [route.origin.lat, route.origin.lon],
            [route.destination.lat, route.destination.lon],
          )}
          pathOptions={{ color: "#7d8aa3", weight: 1.25, opacity: 0.55, dashArray: "1 7" }}
        >
          <Tooltip sticky className="!font-mono !text-[10px]">
            Scheduled: {route.origin.iata || route.origin.code} → {route.destination.iata || route.destination.code}
          </Tooltip>
        </Polyline>
      )}
      {route && (
        <>
          <CircleMarker
            center={[route.origin.lat, route.origin.lon]}
            radius={3}
            pathOptions={{ color: "#7d8aa3", fillColor: "#7d8aa3", fillOpacity: 0.9, weight: 1 }}
          >
            <Tooltip className="!font-mono !text-[10px]">
              {route.origin.city || route.origin.name} ({route.origin.iata || route.origin.code})
            </Tooltip>
          </CircleMarker>
          <CircleMarker
            center={[route.destination.lat, route.destination.lon]}
            radius={3}
            pathOptions={{ color: "#7d8aa3", fillColor: "#7d8aa3", fillOpacity: 0.9, weight: 1 }}
          >
            <Tooltip className="!font-mono !text-[10px]">
              {route.destination.city || route.destination.name} ({route.destination.iata || route.destination.code})
            </Tooltip>
          </CircleMarker>
        </>
      )}

      {prediction && (
        <>
          <Polyline
            positions={prediction.path}
            pathOptions={{ color: accent, weight: 2.25, opacity: 0.9, dashArray: "7 5" }}
          />
          <CircleMarker
            center={[prediction.pred_lat_5min, prediction.pred_lon_5min]}
            radius={5}
            pathOptions={{ color: accent, fillColor: accent, fillOpacity: 0.95, weight: 1.5 }}
          >
            <Tooltip className="!font-mono !text-[10px]">Predicted position, +5 min</Tooltip>
          </CircleMarker>
        </>
      )}
    </>
  );
}
