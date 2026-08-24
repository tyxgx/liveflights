"use client";

import type { EmergencyFlight } from "@/lib/flightInsights";

/** Only ever renders when a real emergency squawk (7500/7600/7700) is
 * present in the live feed — self-explanatory by absence: no banner means
 * nothing to report, not "feature not loaded". Shape + text carries the
 * meaning, not color alone (an accessibility rule this project follows
 * elsewhere too — see the anomaly-marker guidance this was built from). */
export function EmergencyBanner({ emergencies }: { emergencies: EmergencyFlight[] }) {
  if (emergencies.length === 0) return null;

  return (
    <div className="pointer-events-auto absolute inset-x-0 top-0 z-[1100] flex justify-center px-4 pt-3">
      <div className="flex max-w-2xl flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-danger/50 bg-danger/15 px-4 py-2 text-[13px] text-ink shadow-[0_0_20px_rgba(244,63,94,0.25)] backdrop-blur-md">
        <span className="animate-pulse-ring inline-block h-2 w-2 flex-shrink-0 rounded-full bg-danger" />
        <span className="font-semibold text-danger">
          {emergencies.length === 1 ? "Emergency squawk" : `${emergencies.length} emergency squawks`}
        </span>
        {emergencies.map(({ flight, squawk, meaning }) => (
          <span key={flight.icao24} className="font-mono text-[12px] text-ink-muted">
            {flight.callsign?.trim() || flight.icao24}
            <span className="text-danger"> · {squawk}</span> ({meaning})
          </span>
        ))}
      </div>
    </div>
  );
}
