"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";
import { altitudeColor, formatAltitude, formatSpeed, metersToFeet } from "@/lib/format";
import type { AnomalyEvent, LiveFlight } from "@/types/api";

// Recognizable top-down aircraft silhouette (narrow fuselage, wide main
// wings, small tail wings) — nose points up (0deg = north), matching
// true_track's compass convention directly via CSS rotate().
const PLANE_SVG = (color: string) => `
  <svg width="20" height="20" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 1.5 L13.2 7.5 L21.5 12 L21.5 13.6 L13.4 11.4 L14.3 17.3 L17.3 19.4 L17.3 21 L12 19.3
             L6.7 21 L6.7 19.4 L9.7 17.3 L10.6 11.4 L2.5 13.6 L2.5 12 L10.8 7.5 Z"
      fill="${color}" stroke="rgba(6,9,16,0.7)" stroke-width="0.6" stroke-linejoin="round" />
  </svg>
`;

function derivePhase(f: LiveFlight): string {
  if (f.on_ground) return "ground";
  if (f.vertical_rate == null) return "cruise";
  if (f.vertical_rate > 1) return "climb";
  if (f.vertical_rate < -1) return "descent";
  return "cruise";
}

function popupHtml(f: LiveFlight, anomaly?: AnomalyEvent): string {
  const altFt = metersToFeet(f.baro_altitude);
  const speedKmh = f.velocity != null ? f.velocity * 3.6 : null;
  const rows: [string, string][] = [
    ["Callsign", f.callsign?.trim() || "—"],
    ["Country", f.origin_country ?? "—"],
    ["Altitude", formatAltitude(altFt)],
    ["Speed", formatSpeed(speedKmh)],
    ["Phase", derivePhase(f)],
    ["Corridor", anomaly?.nearest_corridor_id != null ? `#${anomaly.nearest_corridor_id}` : "—"],
    ["Anomaly score", anomaly ? anomaly.anomaly_score.toFixed(2) : "—"],
  ];
  return `
    <div style="font-family: var(--font-jetbrains, monospace); font-size: 11px; min-width: 170px;">
      <div style="font-weight:600; font-size:12px; margin-bottom:6px; color:#e2e8f0;">${f.icao24}</div>
      ${rows
        .map(
          ([label, value]) =>
            `<div style="display:flex; justify-content:space-between; gap:12px; padding:1px 0; color:#a3adc2;">
               <span>${label}</span><span style="color:#e2e8f0;">${value}</span>
             </div>`,
        )
        .join("")}
    </div>
  `;
}

/**
 * Imperative aircraft marker registry — critical for perf at 150+ aircraft
 * updating every 3s. Never recreates markers; updates position, rotation,
 * and color on the existing Leaflet marker/DOM node directly. Recreating
 * markers each tick visibly stutters.
 */
export function AircraftLayer({
  flights,
  selectedIcao24,
  anomalyByIcao,
  onSelect,
}: {
  flights: LiveFlight[];
  selectedIcao24: string | null;
  anomalyByIcao: Map<string, AnomalyEvent>;
  onSelect: (flight: LiveFlight) => void;
}) {
  const map = useMap();
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const markers = markersRef.current;
    const seen = new Set<string>();

    for (const f of flights) {
      if (f.latitude == null || f.longitude == null) continue;
      seen.add(f.icao24);

      const anomaly = anomalyByIcao.get(f.icao24);
      const isAnomaly = Boolean(anomaly);
      const color = isAnomaly ? "#f43f5e" : altitudeColor(metersToFeet(f.baro_altitude));
      const rotation = f.true_track ?? 0;
      const isSelected = f.icao24 === selectedIcao24;

      let marker = markers.get(f.icao24);
      if (!marker) {
        const icon = L.divIcon({
          className: "aircraft-icon-wrapper",
          html: `<div class="aircraft-icon" style="transform: rotate(${rotation}deg)">${PLANE_SVG(color)}</div>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        });
        marker = L.marker([f.latitude, f.longitude], { icon, riseOnHover: true });
        marker.addTo(map);
        marker.bindPopup("");
        marker.on("click", () => onSelectRef.current(f));
        markers.set(f.icao24, marker);
      } else {
        marker.setLatLng([f.latitude, f.longitude]);
        const el = marker.getElement()?.querySelector<HTMLDivElement>(".aircraft-icon");
        if (el) {
          el.style.transform = `rotate(${rotation}deg)`;
          const path = el.querySelector("path");
          if (path) path.setAttribute("fill", color);
        }
      }

      marker.setPopupContent(popupHtml(f, anomaly));

      const el = marker.getElement();
      if (el) {
        el.classList.toggle("outline", isSelected);
        el.classList.toggle("outline-2", isSelected);
        el.classList.toggle("outline-accent-cyan", isSelected);
        el.classList.toggle("rounded-full", isSelected);
        el.classList.toggle("animate-pulse-ring", isAnomaly);
        el.style.zIndex = isSelected ? "1000" : "";
      }
    }

    for (const [icao24, marker] of markers) {
      if (!seen.has(icao24)) {
        marker.remove();
        markers.delete(icao24);
      }
    }
  }, [flights, selectedIcao24, anomalyByIcao, map]);

  useEffect(() => {
    const markers = markersRef.current;
    return () => {
      for (const marker of markers.values()) marker.remove();
      markers.clear();
    };
  }, [map]);

  return null;
}
