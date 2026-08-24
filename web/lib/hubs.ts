// Mirrors infra/terraform/variables.tf's adsb_lol_points — the 8 fixed hub
// circles the ingest Lambda actually polls (each a 250nm/~463km radius, the
// hard cap adsb.lol's point+radius endpoint enforces; no single point can
// cover a continent). Kept here, not fetched from an API, because it never
// changes at runtime — it's deploy-time config, same as the Terraform
// source of truth. Update both together if the points ever change.
export interface HubPoint {
  id: string;
  label: string;
  lat: number;
  lon: number;
  distNm: number;
}

export const HUB_POINTS: HubPoint[] = [
  { id: "british-isles", label: "British Isles", lat: 53.0, lon: -2.0, distNm: 250 },
  { id: "france-benelux", label: "France / Benelux", lat: 50.0, lon: 2.5, distNm: 250 },
  { id: "germany-central", label: "Germany / Central Europe", lat: 50.5, lon: 10.0, distNm: 250 },
  { id: "scandinavia", label: "Scandinavia", lat: 59.0, lon: 15.0, distNm: 250 },
  { id: "iberia", label: "Iberia", lat: 40.0, lon: -3.5, distNm: 250 },
  { id: "italy", label: "Italy", lat: 42.0, lon: 12.5, distNm: 250 },
  { id: "poland-eastern", label: "Poland / Eastern Europe", lat: 50.5, lon: 22.0, distNm: 250 },
  { id: "balkans-greece", label: "Balkans / Greece", lat: 40.0, lon: 22.0, distNm: 250 },
];

const EARTH_RADIUS_NM = 3440.065;

/** Great-circle distance in nautical miles — same unit adsb.lol's own
 * radius parameter uses, so a flight "inside" a hub is exactly "would this
 * hub's fetch have returned it". */
export function haversineNm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return EARTH_RADIUS_NM * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
