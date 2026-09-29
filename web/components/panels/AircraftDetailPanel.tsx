"use client";

import { useEffect, useState } from "react";
import { altitudeColor, formatAltitude, formatSpeed, metersToFeet } from "@/lib/format";
import { haversineKm } from "@/lib/geo";
import { EMERGENCY_SQUAWKS, possibleMilitaryLabel } from "@/lib/flightInsights";
import { Skeleton } from "@/components/ui/States";
import type { AccuracyDay, AircraftDetailResponse } from "@/types/api";

function derivePhase(onGround: boolean, verticalRate: number | null): string {
  if (onGround) return "Ground";
  if (verticalRate == null) return "Cruise";
  if (verticalRate > 1) return "Climb";
  if (verticalRate < -1) return "Descent";
  return "Cruise";
}

function Row({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 text-[12px]">
      <span className="text-ink-faint">{label}</span>
      <span className={`font-mono tabular-nums ${accent ?? "text-ink"}`}>{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border px-4 py-3 first:border-t-0">
      <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-faint">{title}</p>
      {children}
    </div>
  );
}

/**
 * Click-to-focus detail panel — everything about the selected aircraft in one place: its live
 * state, its scheduled route (VRS, labeled as scheduled, never asserted as fact), and the GRU
 * model's own 5-minute prediction next to how accurate that model has actually been evaluated to
 * be today (metrics/daily.json, live). Slides in as the one authored motion on this surface
 * (see globals.css); the caller (live/page.tsx) is responsible for collapsing the other rail
 * panels when this opens, so the screen reads as "look at this one thing" instead of five panels
 * competing at once.
 */
export function AircraftDetailPanel({
  detail,
  loading,
  accuracy,
  onClose,
  variant = "primary",
  onCompare,
  picking = false,
  onCancelCompare,
}: {
  detail: AircraftDetailResponse | null;
  loading: boolean;
  accuracy: AccuracyDay | null;
  onClose: () => void;
  /** "compare" renders as the second, violet-accented panel (see FlightMap's
   * compareIcao24) — same layout, no Compare button of its own. */
  variant?: "primary" | "compare";
  /** Primary panel only: enters compare-picking mode (click another aircraft
   * on the map to compare). Omitted on the compare panel itself. */
  onCompare?: () => void;
  /** True while waiting for the user to click a second aircraft — swaps the
   * Compare button for a "click an aircraft…" hint + cancel affordance. */
  picking?: boolean;
  onCancelCompare?: () => void;
}) {
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const state = detail?.state;
  const prediction = detail?.prediction;
  const route = detail?.route;
  const secondsToTarget = prediction ? Math.round(prediction.target_ts - Date.now() / 1000) : null;
  const routeDistanceKm =
    route && route.origin && route.destination
      ? haversineKm([route.origin.lat, route.origin.lon], [route.destination.lat, route.destination.lon])
      : null;

  const emergencyMeaning = state?.squawk ? EMERGENCY_SQUAWKS[state.squawk] : undefined;
  const military = detail ? possibleMilitaryLabel(detail.icao24) : null;
  const isCompare = variant === "compare";
  // Matches PredictionLayer's `accent` prop and AircraftLayer's outline colors — the dot here is
  // the one visual anchor tying "this panel" to "that line/marker on the map" when two are open.
  const accentDot = isCompare ? "bg-accent-violet" : "bg-accent-cyan";

  return (
    <div
      className={`glass-panel absolute top-3 z-[900] w-[300px] overflow-hidden rounded-xl shadow-panel transition-all duration-[420ms] ease-[cubic-bezier(0.16,1,0.3,1)] ${
        isCompare ? "right-[324px]" : "right-3"
      } ${entered ? "translate-x-0 opacity-100" : "translate-x-6 opacity-0"}`}
    >
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className={`mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full ${accentDot}`} />
          <div className="min-w-0">
            <h2 className="truncate font-mono text-[13px] font-medium text-ink">
              {state?.callsign?.trim() || detail?.icao24 || "—"}
            </h2>
            <p className="mt-0.5 font-mono text-[10px] text-ink-faint">{detail?.icao24}</p>
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close aircraft detail"
          className="press flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors hover:bg-white/[0.06] hover:text-ink"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M1 1L11 11M11 1L1 11" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {!isCompare && onCompare && (
        <div className="border-b border-border px-4 py-2">
          {picking ? (
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] text-accent-violet">Click another aircraft to compare…</p>
              <button
                onClick={onCancelCompare}
                className="press flex-shrink-0 text-[11px] text-ink-faint transition-colors hover:text-ink"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={onCompare}
              className="press flex w-full items-center justify-center gap-1.5 rounded-md border border-border py-1.5 text-[11px] text-ink-muted transition-colors hover:border-accent-violet/40 hover:text-accent-violet"
            >
              <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
                <path d="M2 2v8M10 2v8M2 6h8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
              Compare with another aircraft
            </button>
          )}
        </div>
      )}

      {loading && !detail && (
        <div className="space-y-2 p-4">
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}

      {detail && !detail.found && (
        <div className="px-4 py-6 text-[12px] text-ink-faint">
          No longer in live coverage — it may have landed or left the tracked area.
        </div>
      )}

      {state && (
        <Section title="Live state">
          {emergencyMeaning && (
            <div className="mb-2 rounded-md bg-danger/10 px-2 py-1.5 text-[11px] font-medium text-danger">
              ⚠ Squawk {state.squawk} — {emergencyMeaning}
            </div>
          )}
          <Row label="Phase" value={derivePhase(state.on_ground, state.vertical_rate)} />
          <Row
            label="Altitude"
            value={formatAltitude(metersToFeet(state.baro_altitude))}
            accent={`text-ink`}
          />
          <Row label="Ground speed" value={formatSpeed(state.velocity != null ? state.velocity * 3.6 : null)} />
          <Row label="Heading" value={state.true_track != null ? `${Math.round(state.true_track)}°` : "—"} />
          <Row label="Country" value={state.origin_country ?? "—"} />
          {military && <Row label="Possibly military" value={`${military} (unverified)`} accent="text-warn" />}
        </Section>
      )}

      {route && (
        <Section title="Scheduled route">
          <div className="flex items-center gap-2 py-1">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[13px] text-ink">{route.origin.iata || route.origin.code}</p>
              <p className="truncate text-[10px] text-ink-faint">{route.origin.city || route.origin.name}</p>
            </div>
            <svg width="16" height="10" viewBox="0 0 16 10" className="flex-shrink-0 text-ink-faint">
              <path d="M0 5H15M15 5L10 1M15 5L10 9" stroke="currentColor" strokeWidth="1.2" fill="none" />
            </svg>
            <div className="min-w-0 flex-1 text-right">
              <p className="truncate font-mono text-[13px] text-ink">
                {route.destination.iata || route.destination.code}
              </p>
              <p className="truncate text-[10px] text-ink-faint">{route.destination.city || route.destination.name}</p>
            </div>
          </div>
          {routeDistanceKm != null && (
            <p className="mt-1.5 text-[10px] text-ink-faint">
              {formatDistance(routeDistanceKm)} scheduled — from airline schedule data, not a confirmed flight plan
            </p>
          )}
        </Section>
      )}

      {prediction && (
        <Section title="GRU model prediction">
          <Row
            label="+5 min target"
            value={
              secondsToTarget != null
                ? secondsToTarget > 0
                  ? `in ${Math.max(0, Math.round(secondsToTarget / 60))} min`
                  : "reached"
                : "—"
            }
          />
          {accuracy && accuracy.mean_km != null && (
            <Row
              label="Today's live accuracy"
              value={`±${accuracy.mean_km.toFixed(1)} km avg`}
              accent="text-accent-cyan"
            />
          )}
          <p className="mt-1.5 text-[10px] leading-relaxed text-ink-faint">
            Dashed line on the map is the model&apos;s predicted path for the next 5 minutes, evaluated
            live against what actually happens.
          </p>
        </Section>
      )}
    </div>
  );
}

function formatDistance(km: number): string {
  return `${Math.round(km).toLocaleString()} km`;
}
