"use client";

import { CircleMarker, Polyline } from "react-leaflet";
import type { TrajectoryResponse } from "@/types/api";

export function GhostTrailLayer({ trajectory }: { trajectory: TrajectoryResponse | null }) {
  if (!trajectory) return null;

  const track = trajectory.recent_track.filter(
    (p): p is { time_position: number | null; latitude: number; longitude: number } =>
      p.latitude != null && p.longitude != null,
  );
  if (track.length === 0) return null;

  const solidPositions: [number, number][] = track.map((p) => [p.latitude, p.longitude]);
  const last = track[track.length - 1];

  return (
    <>
      <Polyline positions={solidPositions} pathOptions={{ color: "#e2e8f0", weight: 2, opacity: 0.85 }} />
      {trajectory.predicted && (
        <>
          <Polyline
            positions={[
              [last.latitude, last.longitude],
              [trajectory.predicted.predicted_latitude, trajectory.predicted.predicted_longitude],
            ]}
            pathOptions={{ color: "#f5a524", weight: 2, dashArray: "6 6", opacity: 0.9 }}
          />
          <CircleMarker
            center={[trajectory.predicted.predicted_latitude, trajectory.predicted.predicted_longitude]}
            radius={4}
            pathOptions={{ color: "#f5a524", fillColor: "#f5a524", fillOpacity: 0.9 }}
          />
        </>
      )}
    </>
  );
}
