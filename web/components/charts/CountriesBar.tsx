"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { Skeleton, ErrorState, EmptyState } from "@/components/ui/States";

export function CountriesBar() {
  const { data, error, loading, refetch } = usePolledData(() => api.byCountry(8), 30000);

  if (loading && !data) return <Skeleton className="h-full w-full" />;
  if (error && !data) return <ErrorState message={error} onRetry={refetch} />;
  if (!data || data.countries.length === 0) return <EmptyState message="No country data yet." />;

  const rows = [...data.countries].sort((a, b) => a.flight_count - b.flight_count);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" horizontal={false} />
        <XAxis type="number" tick={{ fill: "#7d8aa3", fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          type="category"
          dataKey="origin_country"
          tick={{ fill: "#a3adc2", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={90}
        />
        <Tooltip
          contentStyle={{
            background: "#141a28",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: 6,
            fontSize: 12,
          }}
          cursor={{ fill: "rgba(148,163,184,0.06)" }}
        />
        <Bar dataKey="flight_count" fill="#22d3ee" radius={[0, 3, 3, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
