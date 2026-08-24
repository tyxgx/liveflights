"use client";

import { useMemo } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { getHeadingRose } from "@/lib/flightInsights";
import { EmptyState } from "@/components/ui/States";
import type { LiveFlight } from "@/types/api";

/** Which compass direction current airborne traffic is heading, bucketed
 * into 8 points. Not corridor discovery (no clustering, no training) —
 * just a count of true_track values, grouped. A cheap, honest stand-in for
 * "where is traffic flowing" while corridor ML stays paused. */
export function TrafficRoseChart({ flights }: { flights: LiveFlight[] }) {
  const rows = useMemo(() => getHeadingRose(flights), [flights]);
  const total = rows.reduce((sum, r) => sum + r.count, 0);

  if (total === 0) return <EmptyState message="No airborne heading data yet." />;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart data={rows} outerRadius="75%">
        <PolarGrid stroke="rgba(148,163,184,0.15)" />
        <PolarAngleAxis dataKey="direction" tick={{ fill: "#a3adc2", fontSize: 11 }} />
        <PolarRadiusAxis tick={{ fill: "#7d8aa3", fontSize: 9 }} axisLine={false} tickCount={3} />
        <Tooltip
          contentStyle={{
            background: "#141a28",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: 6,
            fontSize: 12,
          }}
          formatter={(value: number) => [`${value} aircraft`, "Heading this way"]}
        />
        <Radar
          dataKey="count"
          stroke="#22d3ee"
          fill="#22d3ee"
          fillOpacity={0.25}
          isAnimationActive={false}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
