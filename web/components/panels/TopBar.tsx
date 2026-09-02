"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import type { LiveFlight } from "@/types/api";
import type { PollStatus } from "@/hooks/useFlightsPolling";

// No icons here on purpose -- the first pass paired every stat with an
// inline SVG, which read as busy for a bar this narrow. Numbers carry
// their own weight (mono, tabular); a quiet uppercase label is enough
// context without a glyph competing for the same few pixels.
function Stat({
  label,
  value,
  note,
  accent = false,
}: {
  label: string;
  value: string;
  note?: string;
  accent?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wider text-ink-faint">{label}</span>
      <span className={`font-mono text-base font-medium leading-none tabular-nums ${accent ? "text-warn" : "text-ink"}`}>
        {value}
        {note && <span className="ml-1.5 text-[9px] font-normal text-warn/70">{note}</span>}
      </span>
    </div>
  );
}

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
  if (source === "adsb_lol") return "adsb.lol";
  if (source === "simulate_cloud") return "simulator (fallback)";
  if (source === "unknown") return "no data yet";
  return source;
}

function pollStatusColor(status: PollStatus): string {
  if (status === "open") return "bg-accent-teal";
  if (status === "connecting" || status === "reconnecting") return "bg-warn";
  return "bg-danger";
}

/** One docked header bar replacing the old floating KPI card + status strip
 * pair that sat in the map's top-left corner. Brand, live status, KPIs, and
 * poll health now read as one continuous instrument row instead of two
 * stacked glass panels — the map area is no longer partially covered by
 * them, so the zoom control's old 168px push-down offset goes away too. */
export function TopBar({
  pollStatus,
  flights,
  lastUpdatedAt,
}: {
  pollStatus: PollStatus;
  flights: LiveFlight[];
  lastUpdatedAt: number | null;
}) {
  const { data } = usePolledData(api.overview, 15000);
  const mlPaused = data?.ml_paused ?? false;

  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const source = dominantSource(flights);
  const secondsAgo = lastUpdatedAt ? Math.max(0, Math.round((Date.now() - lastUpdatedAt) / 1000)) : null;

  return (
    <header className="relative z-[1000] flex h-14 flex-shrink-0 items-center gap-10 border-b border-border px-6">
      <a href="/index.html" className="press flex flex-shrink-0 items-center gap-2 text-ink-muted transition-colors hover:text-accent-cyan">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-teal opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-teal" />
        </span>
        <span className="font-mono text-[13px] font-medium tracking-wide text-ink">liveflights</span>
      </a>

      <div className="hidden min-w-0 items-center gap-8 overflow-x-auto md:flex">
        <Stat label="Active" value={formatNumber(data?.active_flights)} />
        <Stat label="Countries" value={formatNumber(data?.countries)} />
        <Stat
          label="Avg alt"
          value={data?.avg_altitude_ft != null ? `${formatNumber(data.avg_altitude_ft)}ft` : "—"}
        />
        <Stat
          label="Anomalies"
          value={mlPaused ? "—" : formatNumber(data?.anomaly_count)}
          note={mlPaused ? "paused" : undefined}
          accent={mlPaused}
        />
      </div>

      <div className="ml-auto flex flex-shrink-0 items-center gap-4 text-[11px] text-ink-faint">
        <span className="hidden font-mono tabular-nums lg:inline">{sourceLabel(source)}</span>
        <span className="flex items-center gap-1.5 font-mono tabular-nums">
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${pollStatusColor(pollStatus)}`} />
          {secondsAgo === null ? "—" : `${secondsAgo}s ago`}
        </span>
      </div>
    </header>
  );
}
