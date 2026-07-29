"use client";

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ALTITUDE_BANDS, metersToFeet } from "@/lib/format";
import { EmptyState } from "@/components/ui/States";
import type { LiveFlight } from "@/types/api";

/** No dedicated altitude-histogram endpoint exists — computed client-side
 * from the live WebSocket feed using the same bands the map icons use, so
 * the histogram and the map are always describing the same color legend.
 */
export function AltitudeHistogram({ flights }: { flights: LiveFlight[] }) {
  const rows = useMemo(() => {
    const counts = ALTITUDE_BANDS.map((b) => ({ label: b.label, count: 0, color: b.color }));
    for (const f of flights) {
      const ft = metersToFeet(f.baro_altitude);
      if (ft == null || f.on_ground) continue;
      const idx = ALTITUDE_BANDS.findIndex((b) => ft < b.max);
      if (idx >= 0) counts[idx].count += 1;
    }
    return counts;
  }, [flights]);

  if (flights.length === 0) return <EmptyState message="Waiting for live data…" />;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" vertical={false} />
        <XAxis dataKey="label" tick={{ fill: "#7d8aa3", fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: "#7d8aa3", fontSize: 10 }} axisLine={false} tickLine={false} width={28} />
        <Tooltip
          contentStyle={{
            background: "#141a28",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: 6,
            fontSize: 12,
          }}
          cursor={{ fill: "rgba(148,163,184,0.06)" }}
        />
        <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {rows.map((r) => (
            <Cell key={r.label} fill={r.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
