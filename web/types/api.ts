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
}

export interface LiveFlightsResponse {
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
  anomaly_count: number;
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
}

export interface CorridorsResponse {
  total_corridors: number;
  returned: number;
  corridors: Corridor[];
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
