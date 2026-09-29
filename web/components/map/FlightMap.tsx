"use client";

import "leaflet/dist/leaflet.css";
import { MapContainer, TileLayer, ZoomControl } from "react-leaflet";
import { AircraftLayer } from "@/components/map/AircraftLayer";
import { AirportLayer } from "@/components/map/AirportLayer";
import { CorridorLayer } from "@/components/map/CorridorLayer";
import { PredictionLayer } from "@/components/map/PredictionLayer";
import { DeselectController } from "@/components/map/DeselectController";
import { FlyToController } from "@/components/map/FlyToController";
import { RegionController } from "@/components/map/RegionController";
import { MapResizeController } from "@/components/map/MapResizeController";
import { DensityHeatLayer } from "@/components/map/DensityHeatLayer";
import { ProximityLayer } from "@/components/map/ProximityLayer";
import type { RegionConfig } from "@/lib/regions";
import type { AircraftDetailResponse, AnomalyEvent, Corridor, LiveFlight } from "@/types/api";

// CARTO's dark_all basemap (used here originally) started rendering an "API
// KEY REQUIRED" watermark tile on every pan/zoom — confirmed 2026-09-29 via a
// direct curl against both of CARTO's anonymous CDN hosts: both now return
// the same watermark PNG instead of a real tile, i.e. CARTO fully retired
// free anonymous access, not a transient outage. Esri's World Dark Gray
// Canvas is the replacement: no key, no signup, genuinely dark-styled (not a
// CSS-inverted light basemap), and free for this traffic level. Note the
// {z}/{y}/{x} order in the URL -- Esri's REST tile API swaps x/y from the
// {z}/{x}/{y} convention every other provider here uses.
const DARK_TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const DARK_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.esri.com">Esri</a> &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors';

export interface FlightMapProps {
  region: RegionConfig;
  flights: LiveFlight[];
  corridors: Corridor[];
  showAircraft: boolean;
  showCorridors: boolean;
  showHeatmap: boolean;
  showProximity: boolean;
  anomaliesOnly: boolean;
  anomalyByIcao: Map<string, AnomalyEvent>;
  selectedIcao24: string | null;
  onSelectFlight: (flight: LiveFlight) => void;
  flyToTarget: [number, number] | null;
  aircraftDetail: AircraftDetailResponse | null;
  onDeselect: () => void;
  /** Compare mode's second aircraft — see AircraftDetailPanel's "Compare" button. */
  compareIcao24?: string | null;
  compareDetail?: AircraftDetailResponse | null;
}

// Default export required for next/dynamic({ ssr: false }) — Leaflet
// touches `window` at import time and will crash Next.js SSR otherwise.
export default function FlightMap({
  region,
  flights,
  corridors,
  showAircraft,
  showCorridors,
  showHeatmap,
  showProximity,
  anomaliesOnly,
  anomalyByIcao,
  selectedIcao24,
  onSelectFlight,
  flyToTarget,
  aircraftDetail,
  onDeselect,
  compareIcao24 = null,
  compareDetail = null,
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
      {/* Default zoomControl is disabled on MapContainer above and re-added
          here — every one of Leaflet's 4 corner presets is already covered
          by a floating panel (KPI top-left, layer/anomaly column right,
          Insights bottom), so this uses topleft with a CSS push-down
          (globals.css: .leaflet-top.leaflet-left) to land in the one
          genuinely open strip: the left edge, below the KPI cluster and
          above the Insights panel. */}
      <ZoomControl position="topleft" />
      <TileLayer url={DARK_TILE_URL} attribution={DARK_TILE_ATTRIBUTION} />
      {showHeatmap && <DensityHeatLayer flights={flights} />}
      {showCorridors && <AirportLayer />}
      {showCorridors && <CorridorLayer corridors={corridors} dimmed={Boolean(selectedIcao24)} />}
      {showProximity && <ProximityLayer flights={flights} />}
      {showAircraft && (
        <AircraftLayer
          flights={visibleFlights}
          selectedIcao24={selectedIcao24}
          compareIcao24={compareIcao24}
          anomalyByIcao={anomalyByIcao}
          onSelect={onSelectFlight}
        />
      )}
      <PredictionLayer detail={aircraftDetail} accent="#22d3ee" />
      {compareDetail && <PredictionLayer detail={compareDetail} accent="#a78bfa" />}
      <DeselectController onDeselect={onDeselect} />
      <FlyToController target={flyToTarget} />
      <RegionController region={region} />
      <MapResizeController />
    </MapContainer>
  );
}
