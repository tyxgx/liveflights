"use client";

import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import { Panel } from "@/components/ui/Panel";
import { Skeleton, ErrorState } from "@/components/ui/States";

// Minimal inline SVGs, not an icon library — this project adds no new
// dependencies for anything cosmetic (see docs/engineering-notes.md's
// running theme). Stroke-only, 16px, matches the rest of the UI's
// restrained line weight.
// The flights icon reuses AircraftLayer.tsx's exact plane silhouette
// (rotated to point right rather than the map's north-up 0deg) so the KPI
// panel and the map markers read as the same visual language.
const ICONS: Record<string, React.ReactNode> = {
  flights: (
    <path
      transform="rotate(90 12 12)"
      fill="currentColor"
      d="M12 1.5 L13.2 7.5 L21.5 12 L21.5 13.6 L13.4 11.4 L14.3 17.3 L17.3 19.4 L17.3 21 L12 19.3
             L6.7 21 L6.7 19.4 L9.7 17.3 L10.6 11.4 L2.5 13.6 L2.5 12 L10.8 7.5 Z"
    />
  ),
  countries: <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.4" />,
  altitude: <path d="M4 18l5-9 3 4 3-6 5 11H4z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />,
  anomalies: (
    <path
      d="M12 3l9 16H3l9-16z"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    />
  ),
};

function Kpi({
  icon,
  label,
  value,
  unit,
  note,
  accent = false,
}: {
  icon: keyof typeof ICONS;
  label: string;
  value: string;
  unit?: string;
  /** Shown instead of a plain number when the value can't be read at
   * face value (e.g. "0 anomalies" while detection is offline reads as
   * "all clear" — it isn't). */
  note?: string;
  accent?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 px-5 py-4">
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        className={`mt-0.5 flex-shrink-0 ${accent ? "text-warn" : "text-ink-faint"}`}
        aria-hidden="true"
      >
        {ICONS[icon]}
      </svg>
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
          {label}
        </span>
        <span className="font-mono text-2xl font-semibold leading-none tabular-nums text-ink">
          {value}
          {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
        </span>
        {note && <span className="text-[9px] leading-tight text-warn/80">{note}</span>}
      </div>
    </div>
  );
}

export function KpiPanel() {
  const { data, error, loading, refetch } = usePolledData(api.overview, 15000);
  const mlPaused = data?.ml_paused ?? false;

  return (
    <Panel className="w-[560px] max-w-[92vw]">
      {loading && !data ? (
        <div className="grid grid-cols-4 divide-x divide-border">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="px-5 py-4">
              <Skeleton className="mb-2 h-3 w-16" />
              <Skeleton className="h-6 w-12" />
            </div>
          ))}
        </div>
      ) : error && !data ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : (
        <div className="grid grid-cols-4 divide-x divide-border">
          <Kpi icon="flights" label="Active Flights" value={formatNumber(data?.active_flights)} />
          <Kpi icon="countries" label="Countries" value={formatNumber(data?.countries)} />
          <Kpi icon="altitude" label="Avg Altitude" value={formatNumber(data?.avg_altitude_ft)} unit="ft" />
          <Kpi
            icon="anomalies"
            label="Anomalies"
            value={mlPaused ? "—" : formatNumber(data?.anomaly_count)}
            note={mlPaused ? "ML paused" : undefined}
            accent={mlPaused}
          />
        </div>
      )}
    </Panel>
  );
}
