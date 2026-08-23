"use client";

import { api } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { metersToFeet, formatNumber } from "@/lib/format";
import type { LiveFlight } from "@/types/api";

/** A real ticker, not decoration: every row is a live flight pulled straight
 * from GET /api/flights/live (the same endpoint the map polls), refreshed
 * every 15s. Duplicated once so the CSS marquee loops seamlessly. */
function TickerRow({ f }: { f: LiveFlight }) {
  const altFt = metersToFeet(f.baro_altitude);
  return (
    <div className="mx-3 flex items-center gap-2 whitespace-nowrap rounded-md border border-border bg-base-panel/60 px-3 py-1.5 text-[12px]">
      <span className="font-mono font-semibold text-accent-cyan">{f.callsign?.trim() || f.icao24}</span>
      <span className="text-ink-faint">·</span>
      <span className="text-ink-muted">{f.origin_country ?? "Unknown"}</span>
      {altFt != null && (
        <>
          <span className="text-ink-faint">·</span>
          <span className="font-mono text-ink-muted">{formatNumber(altFt)} ft</span>
        </>
      )}
    </div>
  );
}

export function LiveTicker() {
  const { data } = usePolledData(() => api.liveFlights(24), 15000);
  const flights = data?.flights ?? [];

  if (flights.length === 0) {
    return (
      <div className="border-y border-border bg-base-panel/40 py-3 text-center text-[12px] text-ink-faint">
        Waiting for live data…
      </div>
    );
  }

  return (
    <div className="group relative overflow-hidden border-y border-border bg-base-panel/40 py-3">
      <div className="flex w-max animate-[ticker-scroll_45s_linear_infinite] group-hover:[animation-play-state:paused] motion-reduce:animate-none">
        {[...flights, ...flights].map((f, i) => (
          <TickerRow key={`${f.icao24}-${i}`} f={f} />
        ))}
      </div>
      <style>{`
        @keyframes ticker-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
}
