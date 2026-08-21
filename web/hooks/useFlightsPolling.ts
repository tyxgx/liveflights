"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { LiveFlight } from "@/types/api";

export type PollStatus = "connecting" | "open" | "reconnecting" | "closed";

const POLL_INTERVAL_MS = 15000;
const INTERPOLATE_TICK_MS = 500;
const EARTH_RADIUS_M = 6_371_000;

/**
 * Dead-reckoning projection: given a flight's last known position, project
 * where it'd be `elapsedSeconds` later along its true_track at its current
 * velocity. Same great-circle forward-geodesic math real flight trackers
 * use to interpolate between sparse position updates — not just a linear
 * lat/lon nudge, which distorts badly at high latitude or over any real
 * distance.
 */
function projectPosition(
  lat: number,
  lon: number,
  trueTrackDeg: number,
  velocityMps: number,
  elapsedSeconds: number,
): [number, number] {
  const distance = velocityMps * elapsedSeconds;
  if (distance === 0) return [lat, lon];

  const angularDistance = distance / EARTH_RADIUS_M;
  const bearing = (trueTrackDeg * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lon1 = (lon * Math.PI) / 180;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2),
    );

  return [(lat2 * 180) / Math.PI, (((lon2 * 180) / Math.PI + 540) % 360) - 180];
}

/**
 * Polls /api/flights/live every 15s (the cloud API is Lambda-backed —
 * there's no WebSocket to push updates) and dead-reckons each aircraft's
 * displayed position between polls so planes glide instead of teleporting.
 * Snaps to the true polled position on every fresh fetch. Same
 * {flights, status, lastMessageAt} shape as useFlightsWebSocket, so
 * app/page.tsx can swap between them without touching anything downstream.
 */
export function useFlightsPolling(enabled: boolean = true) {
  const [flights, setFlights] = useState<LiveFlight[]>([]);
  const [status, setStatus] = useState<PollStatus>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);

  const basePositionsRef = useRef<Map<string, { lat: number; lon: number; polledAt: number }>>(
    new Map(),
  );
  const baseFlightsRef = useRef<LiveFlight[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    async function poll() {
      try {
        const res = await api.liveFlights(1000);
        if (cancelled) return;
        const now = Date.now();
        baseFlightsRef.current = res.flights;
        const positions = new Map<string, { lat: number; lon: number; polledAt: number }>();
        for (const f of res.flights) {
          if (f.latitude != null && f.longitude != null) {
            positions.set(f.icao24, { lat: f.latitude, lon: f.longitude, polledAt: now });
          }
        }
        basePositionsRef.current = positions;
        setFlights(res.flights);
        setLastMessageAt(now);
        setStatus("open");
      } catch {
        if (!cancelled) setStatus("reconnecting");
      }
    }

    poll();
    const pollId = setInterval(poll, POLL_INTERVAL_MS);

    const tickId = setInterval(() => {
      if (cancelled || baseFlightsRef.current.length === 0) return;
      const now = Date.now();
      const interpolated = baseFlightsRef.current.map((f) => {
        const base = basePositionsRef.current.get(f.icao24);
        if (!base || f.on_ground || f.velocity == null || f.true_track == null) return f;
        const elapsedSeconds = (now - base.polledAt) / 1000;
        const [lat, lon] = projectPosition(base.lat, base.lon, f.true_track, f.velocity, elapsedSeconds);
        return { ...f, latitude: lat, longitude: lon };
      });
      setFlights(interpolated);
    }, INTERPOLATE_TICK_MS);

    return () => {
      cancelled = true;
      clearInterval(pollId);
      clearInterval(tickId);
      setStatus("closed");
    };
  }, [enabled]);

  return { flights, status, lastMessageAt };
}
