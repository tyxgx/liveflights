// Altitude bands + colors, shared between the map icons and the histogram
// so "what color means what altitude" stays consistent across the UI.

export const ALTITUDE_BANDS = [
  { max: 5000, color: "#2dd4bf", label: "0-5,000ft" },
  { max: 15000, color: "#22d3ee", label: "5,000-15,000ft" },
  { max: 30000, color: "#6366f1", label: "15,000-30,000ft" },
  { max: Infinity, color: "#a78bfa", label: "30,000ft+" },
];

export function altitudeColor(altitudeFt: number | null | undefined): string {
  if (altitudeFt == null) return "#7d8aa3";
  const band = ALTITUDE_BANDS.find((b) => altitudeFt < b.max);
  return band?.color ?? ALTITUDE_BANDS[ALTITUDE_BANDS.length - 1].color;
}

export function metersToFeet(m: number | null | undefined): number | null {
  if (m == null) return null;
  return m * 3.28084;
}

export function formatNumber(n: number | null | undefined, fractionDigits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

export function formatAltitude(ft: number | null | undefined): string {
  if (ft == null) return "—";
  return `${formatNumber(ft)} ft`;
}

export function formatSpeed(kmh: number | null | undefined): string {
  if (kmh == null) return "—";
  return `${formatNumber(kmh)} km/h`;
}

// The ~5 artificial dead0X records injected during the P3 restart-safety
// proof are still in silver and currently rank as top anomalies. Real
// signal, fabricated data — filter them out of the default anomaly feed so
// the demo reads cleanly, but never hide them from anyone who explicitly
// queries for them (this is purely a UI display filter, not a data filter).
export function isSyntheticTestRecord(icao24: string): boolean {
  return /^dead0\d$/.test(icao24);
}

export function timeAgo(isoString: string): string {
  const seconds = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}
