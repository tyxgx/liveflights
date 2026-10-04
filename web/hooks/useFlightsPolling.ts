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
    let inFlight = false;
    let lastOkAt = 0;
    const controller = new AbortController();

    async function poll() {
      // never stack requests: a slow response must not be followed by a second one for the same data,
      // and there is no point fetching 1.6 MB for a tab nobody is looking at
      if (inFlight || cancelled || document.hidden) return;
      inFlight = true;
      try {
        // 1000 silently truncated a full-Europe snapshot (~3,600+ and
        // climbing) to whatever happened to be first in the API's list —
        // which skews toward one geographic hub (whichever adsb.lol point
        // responded fastest that poll), not a random cross-section. The map
        // was rendering ~100% British Isles and nothing else as a result.
        // 6000 comfortably covers current + headroom; matches the API's cap.
        const res = await api.liveFlights(6000, controller.signal);
        if (cancelled) return;
        const now = Date.now();
        lastOkAt = now;
        setFlights(res.flights);
        setLastMessageAt(now);
        setStatus("open");
      } catch {
        if (!cancelled && !controller.signal.aborted) setStatus("reconnecting");
      } finally {
        inFlight = false;
      }
    }

    // coming back to the tab: refresh straight away if the data is stale instead of waiting for the next tick
    function onVisible() {
      if (!document.hidden && Date.now() - lastOkAt >= POLL_INTERVAL_MS) poll();
    }

    poll();
    const pollId = setInterval(poll, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(pollId);
      document.removeEventListener("visibilitychange", onVisible);
      setStatus("closed");
    };
  }, [enabled]);

  return { flights, status, lastMessageAt };
}
