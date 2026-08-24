// Best-effort "possibly military" flag from an aircraft's icao24 hex
// address. ICAO allocates 24-bit addresses in national blocks, and several
// countries carve a sub-range of their own block out for military use —
// but there's no single official public registry mapping every such
// sub-range. The ranges below are the small set commonly repeated across
// open ADS-B community trackers (dump1090/tar1090-derived "military range"
// lists), not verified against an authoritative ICAO source. Treat this as
// a low-confidence hint, not a fact — false negatives (real military
// aircraft not flagged) are likely and expected; false positives are
// possible too. Never label this "verified" or "confirmed" in the UI.
interface HexRange {
  label: string;
  lo: number;
  hi: number;
}

const RANGES: HexRange[] = [
  { label: "US military", lo: 0xadf7c8, hi: 0xafffff },
  { label: "UK military", lo: 0x43c000, hi: 0x43cfff },
  { label: "French military", lo: 0x3ac000, hi: 0x3affff },
  { label: "German military", lo: 0x3f4000, hi: 0x3f7fff },
];

export function possibleMilitaryLabel(icao24: string | null | undefined): string | null {
  if (!icao24) return null;
  const hex = parseInt(icao24, 16);
  if (Number.isNaN(hex)) return null;
  const match = RANGES.find((r) => hex >= r.lo && hex <= r.hi);
  return match ? match.label : null;
}
