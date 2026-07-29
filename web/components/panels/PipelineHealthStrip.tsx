"use client";

import { api } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import type { WsStatus } from "@/hooks/useFlightsWebSocket";

function Dot({ ok }: { ok: boolean | undefined }) {
  return (
    <span
      className={`inline-block h-1.5 w-1.5 rounded-full ${
        ok === undefined ? "bg-ink-faint" : ok ? "bg-accent-teal" : "bg-danger"
      }`}
    />
  );
}

function wsStatusColor(status: WsStatus): string {
  if (status === "open") return "bg-accent-teal";
  if (status === "connecting" || status === "reconnecting") return "bg-warn";
  return "bg-danger";
}

export function PipelineHealthStrip({
  wsStatus,
  liveFlightCount,
}: {
  wsStatus: WsStatus;
  liveFlightCount: number;
}) {
  const { data: health } = usePolledData(api.health, 20000);

  return (
    <div className="glass-panel flex h-9 items-center gap-5 rounded-b-lg border-t-0 px-4 text-[11px] text-ink-muted">
      <div className="flex items-center gap-1.5">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${wsStatusColor(wsStatus)}`} />
        <span className="font-mono tabular-nums">WS: {wsStatus}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Dot ok={health?.database.ok} />
        <span>Postgres</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Dot ok={health?.redis.ok} />
        <span>Redis</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Dot ok={health?.kafka_live_store.ok} />
        <span>Kafka feed</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Dot ok={health?.trajectory_model.ok} />
        <span>Trajectory model ({health?.trajectory_model.detail ?? "—"})</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Dot ok={health?.forecast_model.ok} />
        <span>Forecast model ({health?.forecast_model.detail ?? "—"})</span>
      </div>
      <div className="ml-auto font-mono tabular-nums">
        {liveFlightCount.toLocaleString()} aircraft tracked live
      </div>
    </div>
  );
}
