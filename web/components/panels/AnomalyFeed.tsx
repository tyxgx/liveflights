"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { isSyntheticTestRecord, timeAgo } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import { PanelHeader } from "@/components/ui/Panel";
import { Skeleton, ErrorState, EmptyState } from "@/components/ui/States";
import type { AnomalyEvent } from "@/types/api";

function scoreColor(score: number): string {
  if (score >= 0.85) return "text-danger";
  if (score >= 0.65) return "text-warn";
  return "text-ink-muted";
}

export function AnomalyFeed({
  onSelect,
  collapsed,
  onToggleCollapse,
  totalCorridors,
}: {
  onSelect: (event: AnomalyEvent) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** Anomaly scoring reads the corridor reference table — 0 means the ML
   * reference is empty (e.g. just reset after a region switch), not that
   * traffic happens to be anomaly-free. The empty state below needs to
   * say which of those it actually is. */
  totalCorridors: number;
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
        className="glass-panel flex h-full w-9 flex-col items-center gap-2 rounded-lg py-3 text-ink-muted hover:text-ink"
        title="Expand anomaly feed"
      >
        <span className="text-[10px] font-medium uppercase tracking-wider [writing-mode:vertical-rl]">
          Anomalies
        </span>
      </button>
    );
  }

  return (
    <div className="glass-panel flex h-full w-[340px] flex-col rounded-lg">
      <PanelHeader
        title="Anomaly Feed"
        subtitle={data ? `${events.length} shown of ${data.total} total` : undefined}
        right={
          <button onClick={onToggleCollapse} className="text-ink-muted hover:text-ink" title="Collapse">
            ‹
          </button>
        }
      />
      <div className="flex items-center justify-between px-4 py-2 text-[10px] text-ink-faint">
        <span>Newest first</span>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeTestRecords}
            onChange={(e) => setIncludeTestRecords(e.target.checked)}
            className="h-3 w-3 accent-accent-cyan"
          />
          include test records
        </label>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <div className="space-y-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : error && !data ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : events.length === 0 && totalCorridors === 0 ? (
          <EmptyState
            message="Anomaly detection is offline, not clean."
            detail="The ML corridor reference (what 'normal' looks like per route) is rebuilding after a recent region switch — anomaly scoring needs it and is skipped until it's ready. This isn't the same as zero anomalies."
          />
        ) : events.length === 0 ? (
          <EmptyState message="No anomalies detected right now." />
        ) : (
          <ul>
            {events.map((event, i) => (
              <li key={`${event.icao24}-${event.ingest_ts}-${i}`}>
                <button
                  onClick={() => onSelect(event)}
                  className="w-full border-b border-border px-4 py-3 text-left hover:bg-white/[0.03]"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm font-medium text-ink">
                      {event.callsign?.trim() || event.icao24}
                    </span>
                    <span className={`font-mono text-xs tabular-nums ${scoreColor(event.anomaly_score)}`}>
                      {event.anomaly_score.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-ink-muted">{event.anomaly_type.replace(/,/g, " · ")}</p>
                  <div className="mt-1.5 flex items-center justify-between text-[10px] text-ink-faint">
                    <span>
                      {event.nearest_corridor_id !== null
                        ? `corridor #${event.nearest_corridor_id}`
                        : "no corridor"}
                      {event.origin_country ? ` · ${event.origin_country}` : ""}
                    </span>
                    <span>{timeAgo(event.ingest_ts)}</span>
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
