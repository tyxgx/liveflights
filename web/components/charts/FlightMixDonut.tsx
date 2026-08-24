"use client";

import { useMemo } from "react";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { getGroundAirSplit, getVerticalMix } from "@/lib/flightInsights";
import { EmptyState } from "@/components/ui/States";
import type { LiveFlight } from "@/types/api";

const COLORS: Record<string, string> = {
  Climbing: "#2dd4bf",
  Cruising: "#22d3ee",
  Descending: "#6366f1",
  Ground: "#4b5670",
};

/** What every currently-tracked aircraft is doing right now — climbing,
 * holding altitude, descending, or still on the ground. Straight from
 * vertical_rate/on_ground, no model involved. */
export function FlightMixDonut({ flights }: { flights: LiveFlight[] }) {
  const rows = useMemo(() => {
    const mix = getVerticalMix(flights);
    const { ground } = getGroundAirSplit(flights);
    return [
      { name: "Climbing", value: mix.climbing },
      { name: "Cruising", value: mix.level },
      { name: "Descending", value: mix.descending },
      { name: "Ground", value: ground },
    ].filter((r) => r.value > 0);
  }, [flights]);

  if (flights.length === 0) return <EmptyState message="Waiting for live data…" />;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={rows}
          dataKey="value"
          nameKey="name"
          innerRadius="55%"
          outerRadius="85%"
          paddingAngle={2}
          isAnimationActive={false}
        >
          {rows.map((r) => (
            <Cell key={r.name} fill={COLORS[r.name]} stroke="none" />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: "#141a28",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: 6,
            fontSize: 12,
          }}
          formatter={(value: number, name: string) => [`${value} aircraft`, name]}
        />
        <Legend
          verticalAlign="bottom"
          height={24}
          iconType="circle"
          wrapperStyle={{ fontSize: 10, color: "#a3adc2" }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
