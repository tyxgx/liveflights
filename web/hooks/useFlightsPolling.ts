"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LiveFlight, LiveFlightsResponse } from "@/types/api";

export type PollStatus = "connecting" | "open" | "reconnecting" | "closed";

const POLL_INTERVAL_MS = 15000;
/** A map snapshot older than this is treated as missing (ingest stalled) and the API is asked instead. */
const SNAPSHOT_MAX_AGE_MS = 180_000;
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
    let lastSnapshotAt: string | null | undefined;
    const controller = new AbortController();

    async function poll() {
      // never stack requests: a slow response must not be followed by a second one for the same data,
      // and there is no point fetching 1.6 MB for a tab nobody is looking at
      if (inFlight || cancelled || document.hidden) return;
      inFlight = true;
      try {
        // Preferred: the pre-gzipped snapshot straight from S3 (~140 KB, no Lambda). Fallback, if it is
        // missing, unreachable or older than SNAPSHOT_MAX_AGE_MS: the API call below.
        let res: LiveFlightsResponse | null = null;
        const snapshot = api.liveSnapshot(controller.signal);
        if (snapshot) {
          try {
            const snap = await snapshot;
            const ageMs = snap.updated_at ? Date.now() - Date.parse(snap.updated_at) : Infinity;
            if (snap.flights.length > 0 && ageMs < SNAPSHOT_MAX_AGE_MS) res = snap;
          } catch {
            if (controller.signal.aborted) return;
          }
        }
        if (!res) {
          // 1000 silently truncated a full-Europe snapshot (~3,600+ and climbing) to whatever happened to
          // be first in the API's list, which skews toward one geographic hub. 6000 covers current + headroom
          // and matches the API's cap.
          res = await api.liveFlights(6000, controller.signal);
        }
        if (cancelled) return;
        const now = Date.now();
        lastOkAt = now;
        // same data as last time (the snapshot changes once a minute, we poll every 15 s): nothing to re-render
        if (res.updated_at && res.updated_at === lastSnapshotAt) {
          setStatus("open");
          return;
        }
        lastSnapshotAt = res.updated_at;
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
