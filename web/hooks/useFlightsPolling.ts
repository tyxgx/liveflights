"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LiveFlight } from "@/types/api";

export type PollStatus = "connecting" | "open" | "reconnecting" | "closed";

const POLL_INTERVAL_MS = 15000;
/**
 * Polls /api/flights/live every 15s (the cloud API is Lambda-backed — there's no WebSocket to
 * push updates). `flights` changes ONLY when a poll lands. Smooth motion between polls is done
 * by AircraftLayer directly on the map markers (see `interpolateFrom` there): it used to be a
 * 500 ms timer that rebuilt the whole 3,600-aircraft array in React state, which re-rendered the
 * entire dashboard twice a second and was the main cause of the stutter (docs/improvements/03).
 * Same {flights, status, lastMessageAt} shape as useFlightsWebSocket, so app/page.tsx can swap
 * between them without touching anything downstream.
 */
export function useFlightsPolling(enabled: boolean = true) {
  const [flights, setFlights] = useState<LiveFlight[]>([]);
  const [status, setStatus] = useState<PollStatus>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    async function poll() {
      try {
        // 1000 silently truncated a full-Europe snapshot (~3,600+ and
        // climbing) to whatever happened to be first in the API's list —
        // which skews toward one geographic hub (whichever adsb.lol point
        // responded fastest that poll), not a random cross-section. The map
        // was rendering ~100% British Isles and nothing else as a result.
        // 6000 comfortably covers current + headroom; matches the API's cap.
        const res = await api.liveFlights(6000);
        if (cancelled) return;
        const now = Date.now();
        setFlights(res.flights);
        setLastMessageAt(now);
        setStatus("open");
      } catch {
        if (!cancelled) setStatus("reconnecting");
      }
    }

    poll();
    const pollId = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(pollId);
      setStatus("closed");
    };
  }, [enabled]);

  return { flights, status, lastMessageAt };
}
