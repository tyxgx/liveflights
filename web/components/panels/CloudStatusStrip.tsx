"use client";

import { useEffect, useState } from "react";
import type { LiveFlight } from "@/types/api";
import type { PollStatus } from "@/hooks/useFlightsPolling";

function pollStatusColor(status: PollStatus): string {
  if (status === "open") return "bg-accent-teal";
  if (status === "connecting" || status === "reconnecting") return "bg-warn";
  return "bg-danger";
}

/** Majority `source` value across the current flight batch — the cloud
 * ingest Lambda falls back from adsb.lol to the simulator on a live-fetch
 * failure, so this should visibly say so rather than silently mislabel
 * simulated data as live. */
function dominantSource(flights: LiveFlight[]): string {
  const counts = new Map<string, number>();
  for (const f of flights) {
    const s = f.source ?? "unknown";
    counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  let best = "unknown";
  let bestCount = 0;
  for (const [source, count] of counts) {
    if (count > bestCount) {
      best = source;
      bestCount = count;
    }
  }
  return best;
}

function sourceLabel(source: string): string {
  if (source === "adsb_lol") return "adsb.lol · live";
  if (source === "simulate_cloud") return "simulator (fallback)";
  if (source === "unknown") return "no data yet";
  return source;
}

export function CloudStatusStrip({
  pollStatus,
  flights,
  lastUpdatedAt,
}: {
  pollStatus: PollStatus;
  flights: LiveFlight[];
  lastUpdatedAt: number | null;
}) {
  // Re-render every second purely so the "Xs ago" text stays live —
  // doesn't touch the flights/poll state itself.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const source = dominantSource(flights);
  const secondsAgo = lastUpdatedAt ? Math.max(0, Math.round((Date.now() - lastUpdatedAt) / 1000)) : null;

  return (
    <div className="glass-panel flex h-9 items-center gap-5 rounded-b-lg border-t-0 px-4 text-[11px] text-ink-muted">
      <div className="flex items-center gap-1.5">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${pollStatusColor(pollStatus)}`} />
        <span className="font-mono tabular-nums">Poll: {pollStatus}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            source === "adsb_lol" ? "bg-accent-teal" : source === "simulate_cloud" ? "bg-warn" : "bg-ink-faint"
          }`}
        />
        <span>{sourceLabel(source)}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span>Updated:</span>
        <span className="font-mono tabular-nums">
          {secondsAgo === null ? "—" : `${secondsAgo}s ago`}
        </span>
      </div>
      <div className="ml-auto font-mono tabular-nums">
        {flights.length.toLocaleString()} aircraft tracked live
      </div>
    </div>
  );
}
