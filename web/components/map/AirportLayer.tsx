"use client";

import { CircleMarker, Tooltip } from "react-leaflet";

// Mirrors ingestion/airports.py's AIRPORTS_EUROPE -- kept as a small static
// list here rather than fetched from the API, since it's reference data
// (doesn't change) and CorridorLayer's corridor endpoints already snap to
// these same coordinates. See ml/scratch/train_corridors_fixed.py's
// nearest_airport_ahead() for how a corridor's own polyline end gets
// matched to one of these.
export const MAJOR_AIRPORTS: { iata: string; lat: number; lon: number }[] = [
  { iata: "LHR", lat: 51.4700, lon: -0.4543 },
  { iata: "CDG", lat: 49.0097, lon: 2.5479 },
  { iata: "FRA", lat: 50.0379, lon: 8.5622 },
  { iata: "AMS", lat: 52.3105, lon: 4.7683 },
  { iata: "MAD", lat: 40.4983, lon: -3.5676 },
  { iata: "FCO", lat: 41.8003, lon: 12.2389 },
  { iata: "ZRH", lat: 47.4647, lon: 8.5492 },
  { iata: "VIE", lat: 48.1103, lon: 16.5697 },
  { iata: "CPH", lat: 55.6180, lon: 12.6560 },
  { iata: "ARN", lat: 59.6519, lon: 17.9186 },
  { iata: "WAW", lat: 52.1657, lon: 20.9671 },
  { iata: "PRG", lat: 50.1008, lon: 14.2600 },
  { iata: "BRU", lat: 50.9014, lon: 4.4844 },
  { iata: "MUC", lat: 48.3538, lon: 11.7861 },
  { iata: "BCN", lat: 41.2971, lon: 2.0785 },
  { iata: "MXP", lat: 45.6306, lon: 8.7281 },
  { iata: "OSL", lat: 60.1939, lon: 11.1004 },
  { iata: "HEL", lat: 60.3172, lon: 24.9633 },
  { iata: "DUB", lat: 53.4213, lon: -6.2701 },
  { iata: "LIS", lat: 38.7813, lon: -9.1359 },
  // Added 2026-08-31 alongside ingestion/airports.py -- closes the
  // Baltic-states / interior-Balkans hole (was WAW/VIE and then nothing
  // until Turkey).
  { iata: "RIX", lat: 56.9236, lon: 23.9711 },
  { iata: "VNO", lat: 54.6341, lon: 25.2858 },
  { iata: "TLL", lat: 59.4133, lon: 24.8328 },
  { iata: "OTP", lat: 44.5711, lon: 26.0850 },
  { iata: "SOF", lat: 42.6952, lon: 23.4062 },
  { iata: "BEG", lat: 44.8184, lon: 20.3091 },
];

export function AirportLayer() {
  return (
    <>
      {MAJOR_AIRPORTS.map((ap) => (
        <CircleMarker
          key={ap.iata}
          center={[ap.lat, ap.lon]}
          radius={4}
          pathOptions={{
            color: "#fbbf24",
            fillColor: "#fbbf24",
            fillOpacity: 0.9,
            weight: 1.5,
          }}
        >
          <Tooltip permanent direction="top" offset={[0, -4]} className="airport-label">
            {ap.iata}
          </Tooltip>
        </CircleMarker>
      ))}
    </>
  );
}
