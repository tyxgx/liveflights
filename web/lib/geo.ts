/** Great-circle interpolation for drawing a route as a gentle curve instead of a straight
 * (rhumb) line on the map — the way every real flight-tracking product draws a scheduled
 * route. Spherical-linear interpolation (slerp) between two [lat, lon] points in degrees. */
export function greatCirclePoints(
  from: [number, number],
  to: [number, number],
  steps = 48,
): [number, number][] {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const [lat1, lon1] = [toRad(from[0]), toRad(from[1])];
  const [lat2, lon2] = [toRad(to[0]), toRad(to[1])];

  const d =
    2 *
    Math.asin(
      Math.sqrt(
        Math.sin((lat2 - lat1) / 2) ** 2 +
          Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2,
      ),
    );
  if (d < 1e-9) return [from, to];

  const points: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const a = Math.sin((1 - f) * d) / Math.sin(d);
    const b = Math.sin(f * d) / Math.sin(d);
    const x = a * Math.cos(lat1) * Math.cos(lon1) + b * Math.cos(lat2) * Math.cos(lon2);
    const y = a * Math.cos(lat1) * Math.sin(lon1) + b * Math.cos(lat2) * Math.sin(lon2);
    const z = a * Math.sin(lat1) + b * Math.sin(lat2);
    const lat = Math.atan2(z, Math.sqrt(x * x + y * y));
    const lon = Math.atan2(y, x);
    points.push([toDeg(lat), toDeg(lon)]);
  }
  return points;
}

export function haversineKm(a: [number, number], b: [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const s =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 6371.0088 * 2 * Math.asin(Math.sqrt(s));
}

const EARTH_RADIUS_M = 6_371_000;

/**
 * Dead-reckoning projection: given a flight's last known position, where it will be `elapsedSeconds`
 * later along its true track at its current speed. Great-circle forward geodesic, not a linear
 * lat/lon nudge (which distorts badly at high latitude or over any real distance).
 */
export function projectPosition(
  lat: number,
  lon: number,
  trueTrackDeg: number,
  velocityMps: number,
  elapsedSeconds: number,
): [number, number] {
  const distance = velocityMps * elapsedSeconds;
  if (distance === 0) return [lat, lon];

  const angularDistance = distance / EARTH_RADIUS_M;
  const bearing = (trueTrackDeg * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lon1 = (lon * Math.PI) / 180;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2),
    );

  return [(lat2 * 180) / Math.PI, (((lon2 * 180) / Math.PI + 540) % 360) - 180];
}
