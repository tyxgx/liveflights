"use client";

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getHubHealth } from "@/lib/flightInsights";
import { EmptyState } from "@/components/ui/States";
import type { LiveFlight } from "@/types/api";

/** One bar per configured adsb.lol hub circle — a bar reading near-zero
 * while its neighbors show normal counts is a real signal that hub's fetch
 * is failing this poll, not that the region is genuinely quiet. This is
 * geometry against the same coordinates the ingest Lambda itself polls,
 * not a separate measurement — see lib/hubs.ts. */
export function HubHealthBars({ flights }: { flights: LiveFlight[] }) {
  const rows = useMemo(() => {
    const health = getHubHealth(flights);
    return [...health].sort((a, b) => a.count - b.count);
  }, [flights]);

  if (flights.length === 0) return <EmptyState message="Waiting for live data…" />;

  const maxCount = Math.max(...rows.map((r) => r.count), 1);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" horizontal={false} />
        <XAxis type="number" tick={{ fill: "#7d8aa3", fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fill: "#a3adc2", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={110}
        />
        <Tooltip
          contentStyle={{
            background: "#141a28",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: 6,
            fontSize: 12,
          }}
          cursor={{ fill: "rgba(148,163,184,0.06)" }}
          formatter={(value: number) => [`${value} aircraft`, "In range"]}
        />
        <Bar dataKey="count" radius={[0, 3, 3, 0]} isAnimationActive={false}>
          {rows.map((r) => (
            <Cell key={r.id} fill={r.count < maxCount * 0.05 ? "#f43f5e" : "#2dd4bf"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
