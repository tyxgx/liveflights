"use client";

import "leaflet/dist/leaflet.css";
import { MapContainer, TileLayer } from "react-leaflet";
import { AircraftLayer } from "@/components/map/AircraftLayer";
import { CorridorLayer } from "@/components/map/CorridorLayer";
import { GhostTrailLayer } from "@/components/map/GhostTrailLayer";
import { FlyToController } from "@/components/map/FlyToController";
import { RegionController } from "@/components/map/RegionController";
import type { RegionConfig } from "@/lib/regions";
import type { AnomalyEvent, Corridor, LiveFlight, TrajectoryResponse } from "@/types/api";

// CARTO dark_matter: no API key required, matches the dark UI. Default OSM
// tiles are light and would look wrong against this theme.
const DARK_TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const DARK_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

export interface FlightMapProps {
  region: RegionConfig;
  flights: LiveFlight[];
  corridors: Corridor[];
  showAircraft: boolean;
  showCorridors: boolean;
  anomaliesOnly: boolean;
  anomalyByIcao: Map<string, AnomalyEvent>;
  selectedIcao24: string | null;
  onSelectFlight: (flight: LiveFlight) => void;
  flyToTarget: [number, number] | null;
  trajectory: TrajectoryResponse | null;
}

// Default export required for next/dynamic({ ssr: false }) — Leaflet
// touches `window` at import time and will crash Next.js SSR otherwise.
export default function FlightMap({
  region,
  flights,
  corridors,
  showAircraft,
  showCorridors,
  anomaliesOnly,
  anomalyByIcao,
  selectedIcao24,
  onSelectFlight,
  flyToTarget,
  trajectory,
}: FlightMapProps) {
  const visibleFlights = anomaliesOnly
    ? flights.filter((f) => anomalyByIcao.has(f.icao24))
    : flights;

  return (
    <MapContainer
      center={region.center}
      zoom={region.zoom}
      className="h-full w-full"
      zoomControl={false}
      attributionControl={true}
      preferCanvas
    >
      <TileLayer url={DARK_TILE_URL} attribution={DARK_TILE_ATTRIBUTION} />
      {showCorridors && <CorridorLayer corridors={corridors} />}
      {showAircraft && (
        <AircraftLayer
          flights={visibleFlights}
          selectedIcao24={selectedIcao24}
          anomalyByIcao={anomalyByIcao}
          onSelect={onSelectFlight}
        />
      )}
      <GhostTrailLayer trajectory={trajectory} />
      <FlyToController target={flyToTarget} />
      <RegionController region={region} />
    </MapContainer>
  );
}
