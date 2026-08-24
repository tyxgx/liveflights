"use client";

import { useMemo } from "react";
import { getAltitudeLeaderboard, getSpeedLeaderboard } from "@/lib/flightInsights";
import { metersToFeet, formatNumber } from "@/lib/format";
import { EmptyState } from "@/components/ui/States";
import type { LiveFlight } from "@/types/api";

function Row({ flight, value }: { flight: LiveFlight; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/60 py-1.5 text-[12px] last:border-0">
      <span className="truncate font-mono text-ink">
        {flight.callsign?.trim() || flight.icao24}
      </span>
      <span className="ml-3 flex-shrink-0 font-mono tabular-nums text-ink-muted">{value}</span>
    </div>
  );
}

/** Two small leaderboards, straight from the live feed — fastest ground
 * speed and highest altitude right now. No history, no ranking logic
 * beyond a sort; refreshes every poll like everything else. */
export function Leaderboards({ flights }: { flights: LiveFlight[] }) {
  const fastest = useMemo(() => getSpeedLeaderboard(flights, 5), [flights]);
  const highest = useMemo(() => getAltitudeLeaderboard(flights, 5), [flights]);

  if (flights.length === 0) return <EmptyState message="Waiting for live data…" />;

  return (
    <div className="grid h-full grid-cols-2 gap-6">
      <div>
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
          Fastest right now
        </p>
        {fastest.map((f) => (
          <Row key={f.icao24} flight={f} value={`${formatNumber((f.velocity ?? 0) * 3.6)} km/h`} />
        ))}
      </div>
      <div>
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
          Highest right now
        </p>
        {highest.map((f) => (
          <Row key={f.icao24} flight={f} value={`${formatNumber(metersToFeet(f.baro_altitude))} ft`} />
        ))}
      </div>
    </div>
  );
}
