"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";
import { altitudeColor, metersToFeet } from "@/lib/format";
import { projectPosition } from "@/lib/geo";
import { EMERGENCY_SQUAWKS } from "@/lib/flightInsights";
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

/** How often the markers glide forward between polls. An aircraft moves well under a pixel per second at
 * the default zoom, so 1 Hz looks continuous while costing 1/2 to 1/30 of the old per-tick work. */
const GLIDE_STEP_MS = 1000;

/**
 * Imperative aircraft marker registry. Never recreates markers, and (since docs/improvements/03) never
 * touches the DOM for a marker whose appearance did not change.
 *
 * Three jobs, three effects, so that each runs only when its own inputs change:
 *  A. [flights]               create/remove markers and snap them to the polled position (once per poll).
 *  B. [flights, selection...] colour, rotation, focus outline and dimming, written only when they differ
 *                             from what the marker already shows (cache per aircraft).
 *  C. [interpolateFrom]       glide: once a second, dead-reckon each visible aircraft from its polled
 *                             position. Off-screen aircraft are skipped and catch up on the next poll;
 *                             nothing runs while the tab is hidden.
 *
 * The old design rebuilt the whole 3,600-aircraft array in React state every 500 ms, which re-rendered the
 * entire dashboard and rewrote about 15 DOM properties per marker twice a second.
 */
export function AircraftLayer({
  flights,
  selectedIcao24,
  compareIcao24 = null,
  anomalyByIcao,
  onSelect,
  interpolateFrom = null,
}: {
  flights: LiveFlight[];
  selectedIcao24: string | null;
  /** Second aircraft in compare mode — outlined in accent-violet instead of
   * accent-cyan so the two focused markers stay visually distinguishable,
   * not just both "highlighted" the same way. Null outside compare mode. */
  compareIcao24?: string | null;
  anomalyByIcao: Map<string, AnomalyEvent>;
  onSelect: (flight: LiveFlight) => void;
  /** Timestamp (ms) at which `flights` positions were true. When set, aircraft glide forward from there
   * between polls. Null when the positions are already live (WebSocket mode). */
  interpolateFrom?: number | null;
}) {
  const map = useMap();
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const appliedRef = useRef<Map<string, string>>(new Map()); // aircraft -> what its DOM currently shows
  const flightsRef = useRef<LiveFlight[]>(flights);
  flightsRef.current = flights;
  const anomalyRef = useRef(anomalyByIcao);
  anomalyRef.current = anomalyByIcao;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const visualFor = (f: LiveFlight, anomalies: Map<string, AnomalyEvent>) => {
    const isAnomaly = anomalies.has(f.icao24);
    const isEmergency = Boolean(f.squawk && EMERGENCY_SQUAWKS[f.squawk]);
    const color = isAnomaly || isEmergency ? "#f43f5e" : altitudeColor(metersToFeet(f.baro_altitude));
    return { color, rotation: Math.round(f.true_track ?? 0), alert: isAnomaly || isEmergency };
  };

  // A. create / remove markers and snap to the polled position (runs once per poll)
  useEffect(() => {
    const markers = markersRef.current;
    const seen = new Set<string>();
    for (const f of flights) {
      if (f.latitude == null || f.longitude == null) continue;
      seen.add(f.icao24);
      const existing = markers.get(f.icao24);
      if (existing) {
        existing.setLatLng([f.latitude, f.longitude]);
        continue;
      }
      const { color, rotation } = visualFor(f, anomalyRef.current);
      const icon = L.divIcon({
        className: "aircraft-icon-wrapper",
        html: `<div class="aircraft-icon" style="transform: rotate(${rotation}deg)">${PLANE_SVG(color)}</div>`,
        iconSize: [20, 20],
        iconAnchor: [10, 10],
      });
      const marker = L.marker([f.latitude, f.longitude], { icon, riseOnHover: true });
      marker.addTo(map);
      // No Leaflet popup — AircraftDetailPanel (a real React panel, live-refreshing, with the
      // GRU prediction + route + accuracy) replaced it; a second, duplicate info box on click
      // was confusing, not additive (found by actually clicking an aircraft, 2026-09-29).
      marker.on("click", () => onSelectRef.current(f));
      markers.set(f.icao24, marker);
    }
    for (const [icao24, marker] of markers) {
      if (!seen.has(icao24)) {
        marker.remove();
        markers.delete(icao24);
        appliedRef.current.delete(icao24);
      }
    }
  }, [flights, map]);

  // B. appearance: colour, rotation, focus outline, dimming. Written only when it changed.
  useEffect(() => {
    const markers = markersRef.current;
    const applied = appliedRef.current;
    const anyFocused = Boolean(selectedIcao24) || Boolean(compareIcao24);
    for (const f of flights) {
      const marker = markers.get(f.icao24);
      if (!marker) continue;
      const { color, rotation, alert } = visualFor(f, anomalyByIcao);
      const isSelected = f.icao24 === selectedIcao24;
      const isCompare = f.icao24 === compareIcao24;
      const isFocused = isSelected || isCompare;
      const key = `${color}|${rotation}|${isSelected ? "s" : isCompare ? "c" : "-"}|${alert ? "a" : "-"}|${anyFocused && !isFocused ? "d" : "-"}`;
      if (applied.get(f.icao24) === key) continue;
      applied.set(f.icao24, key);

      const el = marker.getElement();
      if (!el) continue;
      const icon = el.querySelector<HTMLDivElement>(".aircraft-icon");
      if (icon) {
        icon.style.transform = `rotate(${rotation}deg)`;
        const path = icon.querySelector("path");
        if (path) path.setAttribute("fill", color);
      }
      el.classList.toggle("outline", isFocused);
      el.classList.toggle("outline-2", isFocused);
      el.classList.toggle("outline-accent-cyan", isSelected);
      el.classList.toggle("outline-accent-violet", isCompare);
      el.classList.toggle("rounded-full", isFocused);
      el.classList.toggle("animate-pulse-ring", alert);
      el.style.zIndex = isFocused ? "1000" : "";
      // Click-to-focus: dim every other aircraft so the selected one's (or, in compare mode,
      // the selected pair's) trail/prediction reads clearly against the traffic.
      el.style.opacity = anyFocused && !isFocused ? "0.22" : "1";
    }
  }, [flights, selectedIcao24, compareIcao24, anomalyByIcao, map]);

  // C. glide between polls, once a second, only for aircraft inside (or just outside) the view
  useEffect(() => {
    if (interpolateFrom == null) return;
    const markers = markersRef.current;
    let raf = 0;
    let last = 0;
    const loop = (t: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || t - last < GLIDE_STEP_MS) return;
      last = t;
      const elapsed = (Date.now() - interpolateFrom) / 1000;
      const view = map.getBounds().pad(0.3);
      for (const f of flightsRef.current) {
        if (f.on_ground || f.velocity == null || f.true_track == null) continue;
        if (f.latitude == null || f.longitude == null) continue;
        const marker = markers.get(f.icao24);
        if (!marker) continue;
        const [lat, lon] = projectPosition(f.latitude, f.longitude, f.true_track, f.velocity, elapsed);
        if (view.contains([lat, lon])) marker.setLatLng([lat, lon]);
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [interpolateFrom, map]);

  useEffect(() => {
    const markers = markersRef.current;
    const applied = appliedRef.current;
    return () => {
      for (const marker of markers.values()) marker.remove();
      markers.clear();
      applied.clear();
    };
  }, [map]);

  return null;
}
