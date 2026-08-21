"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { Skeleton, ErrorState, EmptyState } from "@/components/ui/States";

interface Row {
  label: string;
  observed: number | null;
  forecast: number | null;
  band: [number, number] | null;
}

export function TrafficForecastChart() {
  const traffic = usePolledData(api.trafficByHour, 60000);
  const forecast = usePolledData(api.forecast, 60000);

  const loading = traffic.loading && !traffic.data;
  const error = traffic.error && !traffic.data;

  if (loading) return <Skeleton className="h-full w-full" />;
  if (error) return <ErrorState message={traffic.error!} onRetry={traffic.refetch} />;
  if (!traffic.data || traffic.data.points.length === 0) {
    return <EmptyState message="No traffic history yet." />;
  }

  const observedRows: Row[] = traffic.data.points.map((p) => ({
    label: new Date(p.hour_bucket).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    observed: p.flight_count,
    forecast: null,
    band: null,
  }));

  const forecastRows: Row[] =
    forecast.data?.points.map((p) => ({
      label: new Date(p.hour_bucket).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      observed: null,
      forecast: p.predicted_flight_count,
      band: [p.lower_bound, p.upper_bound],
    })) ?? [];

  // Bridge the gap so the forecast line connects visually to the last
  // observed point instead of floating disconnected.
  if (observedRows.length > 0 && forecastRows.length > 0) {
    const last = observedRows[observedRows.length - 1];
    forecastRows.unshift({ ...last, forecast: last.observed });
  }

  const rows = [...observedRows, ...forecastRows];

  return (
    <div className="flex h-full flex-col">
      {forecast.data?.trained_on_synthetic_history && (
        <p className="mb-1 text-[10px] text-warn">
          Dashed segment = forecast (model trained on synthetic history), not observed traffic.
        </p>
      )}
      {!forecast.loading && !forecast.data && (
        <p className="mb-1 text-[10px] text-ink-faint">
          Forecast unavailable here — not yet ported to the serverless cloud API, local-stack only for now.
        </p>
      )}
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "#7d8aa3", fontSize: 10 }} axisLine={{ stroke: "rgba(148,163,184,0.15)" }} tickLine={false} />
          <YAxis tick={{ fill: "#7d8aa3", fontSize: 10 }} axisLine={false} tickLine={false} width={36} />
          <Tooltip
            contentStyle={{
              background: "#141a28",
              border: "1px solid rgba(148,163,184,0.18)",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "#7d8aa3" }}
          />
          <Area
            dataKey="band"
            stroke="none"
            fill="#f5a524"
            fillOpacity={0.12}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="observed"
            stroke="#22d3ee"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="forecast"
            stroke="#f5a524"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
