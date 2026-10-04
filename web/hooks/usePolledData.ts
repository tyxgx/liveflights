"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface PolledData<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

/** Fetches on mount, then every `intervalMs`. Exposes loading/error/retry
 * explicitly so every panel can render its own skeleton/error/empty state
 * instead of the app white-screening when the API is unreachable.
 *
 * Background refreshes are quiet: they never overlap a request that is still running, they are
 * skipped while the tab is hidden (and caught up when it returns), and they do not flip `loading`
 * (only the first load and an explicit `refetch()` do), which saves a render per panel per refresh.
 */
export function usePolledData<T>(fetcher: () => Promise<T>, intervalMs: number | null = null): PolledData<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const lastOkRef = useRef(0);

  const run = useCallback((background: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!background) setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        if (!mountedRef.current) return;
        lastOkRef.current = Date.now();
        setData(result);
        setError(null);
      })
      .catch((err: Error) => {
        if (mountedRef.current) setError(err.message ?? "Request failed");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  const refetch = useCallback(() => run(false), [run]);

  useEffect(() => {
    mountedRef.current = true;
    run(false);
    if (!intervalMs) {
      return () => {
        mountedRef.current = false;
      };
    }
    const tick = () => {
      if (!document.hidden) run(true);
    };
    const id = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (!document.hidden && Date.now() - lastOkRef.current >= intervalMs) run(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [run, intervalMs]);

  return { data, error, loading, refetch };
}
