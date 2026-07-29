"use client";

import { useEffect, useRef, useState } from "react";
import { WS_URL } from "@/lib/api";
import type { LiveFlight } from "@/types/api";

export type WsStatus = "connecting" | "open" | "reconnecting" | "closed";

const MAX_BACKOFF_MS = 15000;
const BASE_BACKOFF_MS = 500;

interface WsMessage {
  count: number;
  flights: LiveFlight[];
}

/**
 * Connects to /ws/flights and keeps the latest snapshot in state.
 * Reconnects with exponential backoff (capped at 15s) — the socket WILL
 * drop (server restarts, laptop sleep, network blips) and must recover
 * without user intervention.
 */
export function useFlightsWebSocket() {
  const [flights, setFlights] = useState<LiveFlight[]>([]);
  const [status, setStatus] = useState<WsStatus>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const attemptRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;
      setStatus((prev) => (prev === "open" ? prev : attemptRef.current === 0 ? "connecting" : "reconnecting"));

      const ws = new WebSocket(WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setStatus("open");
      };

      ws.onmessage = (event) => {
        try {
          const data: WsMessage = JSON.parse(event.data);
          setFlights(data.flights);
          setLastMessageAt(Date.now());
        } catch {
          // Ignore malformed frames rather than tearing down the socket.
        }
      };

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setStatus("reconnecting");
        const delay = Math.min(BASE_BACKOFF_MS * 2 ** attemptRef.current, MAX_BACKOFF_MS);
        attemptRef.current += 1;
        timeoutRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      socketRef.current?.close();
      setStatus("closed");
    };
  }, []);

  return { flights, status, lastMessageAt };
}
