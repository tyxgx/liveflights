"use client";

import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import { Panel } from "@/components/ui/Panel";
import { Skeleton, ErrorState } from "@/components/ui/States";

function Kpi({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3">
      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
        {label}
      </span>
      <span className="font-mono text-2xl font-semibold tabular-nums text-ink">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
      </span>
    </div>
  );
}

export function KpiPanel() {
  const { data, error, loading, refetch } = usePolledData(api.overview, 15000);

  return (
    <Panel className="w-[560px] max-w-[92vw]">
      {loading && !data ? (
        <div className="grid grid-cols-4 divide-x divide-border">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="px-4 py-3">
              <Skeleton className="mb-2 h-3 w-16" />
              <Skeleton className="h-6 w-12" />
            </div>
          ))}
        </div>
      ) : error && !data ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : (
        <div className="grid grid-cols-4 divide-x divide-border">
          <Kpi label="Active Flights" value={formatNumber(data?.active_flights)} />
          <Kpi label="Countries" value={formatNumber(data?.countries)} />
          <Kpi label="Avg Altitude" value={formatNumber(data?.avg_altitude_ft)} unit="ft" />
          <Kpi label="Anomalies" value={formatNumber(data?.anomaly_count)} />
        </div>
      )}
    </Panel>
  );
}
