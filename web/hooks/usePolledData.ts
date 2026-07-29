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
 */
export function usePolledData<T>(fetcher: () => Promise<T>, intervalMs: number | null = null): PolledData<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err: Error) => {
        setError(err.message ?? "Request failed");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    if (!intervalMs) return;
    const id = setInterval(load, intervalMs);
    return () => clearInterval(id);
  }, [load, intervalMs]);

  return { data, error, loading, refetch: load };
}
