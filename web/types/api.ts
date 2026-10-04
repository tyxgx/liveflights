// Mirrors api/models/*.py response schemas. Keep in sync manually — no
// codegen step in this project; the schema-contract tests live server-side.

export interface LiveFlight {
  icao24: string;
  callsign: string | null;
  origin_country: string | null;
  latitude: number | null;
  longitude: number | null;
  baro_altitude: number | null;
  velocity: number | null;
  true_track: number | null;
  vertical_rate: number | null;
  on_ground: boolean;
  time_position: number | null;
  source: string | null;
  squawk?: string | null;
  // Departure is a fact once matched (a ground->airborne transition against
  // a known airport); predicted_arrival/eta are always an estimate — ADS-B
  // carries no flight-plan data, see docs/architecture.md. All optional:
  // most aircraft won't have a departure match (only ones seen taking off
  // since this deployment started tracking do), and prediction needs a
  // known heading + departure to run at all.
  departure_iata?: string;
  departure_country?: string;
  departure_time?: number;
  predicted_arrival_iata?: string;
  predicted_arrival_country?: string;
  eta_minutes?: number;
}

export interface LiveFlightsResponse {
  /** when the ingest Lambda wrote this snapshot (ISO); absent on old responses */
  updated_at?: string | null;
  count: number;
  flights: LiveFlight[];
}

export interface TrackPoint {
  time_position: number | null;
  latitude: number | null;
  longitude: number | null;
}

export interface GhostPoint {
  predicted_latitude: number;
  predicted_longitude: number;
  horizon_seconds: number;
}

export interface TrajectoryResponse {
  icao24: string;
  recent_track: TrackPoint[];
  predicted: GhostPoint | null;
}

export interface OverviewStats {
  active_flights: number;
  countries: number;
  avg_altitude_ft: number | null;
  // null (not 0) while ML is paused — a real 0 would claim "checked, found
  // nothing unusual", which anomaly detection isn't running to actually say.
  anomaly_count: number | null;
  ml_paused: boolean;
}

export interface TrafficByHourPoint {
  hour_bucket: string;
  flight_count: number;
  avg_altitude_ft: number | null;
  avg_speed_kmh: number | null;
  is_synthetic: boolean;
}

export interface TrafficByHourResponse {
  points: TrafficByHourPoint[];
}

export interface CountryStat {
  origin_country: string;
  flight_count: number;
  avg_altitude_ft: number | null;
  avg_speed_kmh: number | null;
}

export interface ByCountryResponse {
  countries: CountryStat[];
}

export interface AnomalyEvent {
  icao24: string;
  callsign: string | null;
  origin_country: string | null;
  ingest_ts: string;
  latitude: number | null;
  longitude: number | null;
  altitude_ft: number | null;
  speed_kmh: number | null;
  anomaly_score: number;
  anomaly_type: string;
  nearest_corridor_id: number | null;
  lateral_distance_km: number | null;
  heading_deviation_deg: number | null;
  altitude_z: number | null;
  unassigned_corridor: boolean | null;
}

export interface AnomaliesResponse {
  total: number;
  page: number;
  page_size: number;
  events: AnomalyEvent[];
  ml_paused: boolean;
}

export interface Corridor {
  corridor_id: number;
  centroid_lat: number;
  centroid_lon: number;
  modal_heading_deg: number;
  altitude_p10_ft: number;
  altitude_p50_ft: number;
  altitude_p90_ft: number;
  member_count: number;
  polyline: [number, number][];
  // [start, end] IATA codes, each nullable. A nearest-major-airport-ahead
  // match against the corridor's own polyline end, NOT a matched flight
  // plan -- ADS-B carries no route data, see docs/architecture.md. Most
  // small/low-member corridors far from any hub won't have either end
  // populated, and that's the honest answer, not missing data.
  airports?: [string | null, string | null];
  // Set only when neither end of `polyline` snapped to an airport but the
  // corridor's own centroid sits right on top of one (a wide hub-area
  // cluster whose ends point away from the hub in both directions) --
  // see ml/scratch/train_all.py's HUB_SNAP_MAX_KM.
  hub_airport?: string | null;
}

export interface CorridorsResponse {
  total_corridors: number;
  returned: number;
  corridors: Corridor[];
  ml_paused: boolean;
}

export interface ForecastPoint {
  hour_bucket: string;
  predicted_flight_count: number;
  lower_bound: number;
  upper_bound: number;
}

export interface ForecastResponse {
  trained_on_synthetic_history: boolean;
  points: ForecastPoint[];
}

export interface ComponentStatus {
  ok: boolean;
  detail: string | null;
}

export interface HealthResponse {
  status: string;
  database: ComponentStatus;
  redis: ComponentStatus;
  kafka_live_store: ComponentStatus;
  trajectory_model: ComponentStatus;
  forecast_model: ComponentStatus;
}

// --- GRU trajectory prediction (api/cloud/app.py's /api/predictions, /api/aircraft/{icao24},
// /api/stats/accuracy — the live model trained 2026-09-28, see docs/liveflights-ml-journal.md) ---

export interface AirportInfo {
  code: string;
  name: string;
  city: string;
  iata: string;
  country: string;
  lat: number;
  lon: number;
}

/** The SCHEDULED route for this callsign (VRS standing data) — not proof this specific aircraft
 * is flying it today. Always labeled "scheduled" wherever shown, never presented as confirmed. */
export interface RouteInfo {
  origin: AirportInfo;
  destination: AirportInfo;
  stops: AirportInfo[];
}

export interface PredictionRecord {
  made_at: number;
  target_ts: number;
  /** 30 points, one every 10s out to +5 min, [lat, lon] */
  path: [number, number][];
  pred_lat_5min: number;
  pred_lon_5min: number;
  start_lat: number;
  start_lon: number;
  route: RouteInfo | null;
}

export interface PredictionsResponse {
  count: number;
  predictions: Record<string, PredictionRecord>;
}

export interface TrailPoint {
  ts: number;
  lat: number;
  lon: number;
}

export interface AircraftDetailResponse {
  icao24: string;
  found: boolean;
  state: LiveFlight | null;
  trail: TrailPoint[];
  prediction: PredictionRecord | null;
  route: RouteInfo | null;
}

export interface AccuracyDay {
  day: string;
  n: number;
  mean_km: number | null;
  p90_km: number | null;
  // The honest "typical prediction" number - mean_km is skewed hard by rare large-error outliers
  // (a sharp turn, or the icao24-reuse eval-matching bug fixed 2026-09-29); median barely moves.
  // Prefer this for display; mean_km is kept for anyone who wants the skewed-by-outliers number.
  median_km: number | null;
}

export interface AccuracyResponse {
  days: AccuracyDay[];
}
