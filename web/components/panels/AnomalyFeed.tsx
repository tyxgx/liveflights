"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { isSyntheticTestRecord, timeAgo } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import { Skeleton, ErrorState, EmptyState } from "@/components/ui/States";
import type { AnomalyEvent } from "@/types/api";

function severityDotClass(score: number): string {
  if (score >= 0.85) return "bg-danger";
  if (score >= 0.65) return "bg-warn";
  return "bg-ink-faint";
}

function severityTextClass(score: number): string {
  if (score >= 0.85) return "text-danger";
  if (score >= 0.65) return "text-warn";
  return "text-ink-faint";
}

export function AnomalyFeed({
  onSelect,
  collapsed,
  onToggleCollapse,
  mlPaused,
}: {
  onSelect: (event: AnomalyEvent) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** From the API's own `ml_paused` flag — an empty feed while ML is paused
   * means "not running", not "checked, found nothing unusual". The empty
   * state below needs to say which of those it actually is. */
  mlPaused: boolean;
}) {
  const { data, error, loading, refetch } = usePolledData(() => api.anomalies(1, 50), 10000);
  const [includeTestRecords, setIncludeTestRecords] = useState(false);

  const events = useMemo(() => {
    const all = data?.events ?? [];
    return includeTestRecords ? all : all.filter((e) => !isSyntheticTestRecord(e.icao24));
  }, [data, includeTestRecords]);

  if (collapsed) {
    return (
      <button
        onClick={onToggleCollapse}
        className="press flex h-9 w-full flex-shrink-0 items-center justify-center text-[11px] uppercase tracking-wider text-ink-faint transition-colors hover:text-ink"
      >
        Show anomalies
      </button>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-shrink-0 items-baseline justify-between gap-3 px-5 pb-1 pt-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[11px] uppercase tracking-wider text-ink-faint">Anomalies</h2>
          {data && <span className="font-mono text-[10px] text-ink-faint">{events.length}/{data.total}</span>}
        </div>
        <button onClick={onToggleCollapse} className="press text-ink-faint transition-colors hover:text-ink" title="Collapse">
          ⌄
        </button>
      </div>
      <label className="flex items-center gap-1.5 px-5 pb-4 pt-2 text-[10px] text-ink-faint cursor-pointer select-none">
        <input
          type="checkbox"
          checked={includeTestRecords}
          onChange={(e) => setIncludeTestRecords(e.target.checked)}
          className="h-3 w-3 accent-accent-cyan"
        />
        include test records
      </label>
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <div className="space-y-4 px-5 py-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : error && !data ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : events.length === 0 && mlPaused ? (
          <EmptyState
            message="Anomaly detection is paused, not clean."
            detail="ML (corridor discovery + anomaly scoring) is currently paused on this deployment — this MVP focuses on live flight data and dashboards, not model inference. This isn't the same as zero anomalies."
          />
        ) : events.length === 0 ? (
          <EmptyState message="No anomalies detected right now." />
        ) : (
          <ul>
            {events.map((event, i) => (
              <li key={`${event.icao24}-${event.ingest_ts}-${i}`}>
                <button
                  onClick={() => onSelect(event)}
                  className="press flex w-full items-start gap-3 px-5 py-3 text-left transition-colors hover:bg-white/[0.03]"
                >
                  <span className={`mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full ${severityDotClass(event.anomaly_score)}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-mono text-[13px] font-medium text-ink">
                        {event.callsign?.trim() || event.icao24}
                      </span>
                      <span className={`flex-shrink-0 font-mono text-[11px] tabular-nums ${severityTextClass(event.anomaly_score)}`}>
                        {event.anomaly_score.toFixed(2)}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-ink-muted">
                      {event.anomaly_type.replace(/,/g, " · ")}
                    </p>
                    <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-ink-faint">
                      <span className="truncate">
                        {event.nearest_corridor_id !== null
                          ? `#${event.nearest_corridor_id}`
                          : "no corridor"}
                        {event.origin_country ? ` · ${event.origin_country}` : ""}
                      </span>
                      <span className="flex-shrink-0">{timeAgo(event.ingest_ts)}</span>
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
