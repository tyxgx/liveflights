import type {
  AccuracyResponse,
  AircraftDetailResponse,
  AnomaliesResponse,
  ByCountryResponse,
  CorridorsResponse,
  ForecastResponse,
  HealthResponse,
  LiveFlightsResponse,
  OverviewStats,
  PredictionsResponse,
  TrafficByHourResponse,
  TrajectoryResponse,
} from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
/** Pre-gzipped map snapshot in the public site bucket (written by the ingest Lambda every minute).
 * Unset = not used, the browser polls the API instead. See docs/improvements/07. */
const MAP_SNAPSHOT_URL = process.env.NEXT_PUBLIC_MAP_SNAPSHOT_URL ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Hard ceiling for one request. Without it a hung Lambda leaves a poll "in flight" for as long as the browser allows. */
const REQUEST_TIMEOUT_MS = 25_000;

async function get<T>(path: string, signal?: AbortSignal, absoluteUrl?: string, cache: RequestCache = "no-store"): Promise<T> {
  const controller = new AbortController();
  const onCallerAbort = () => controller.abort();
  signal?.addEventListener("abort", onCallerAbort);
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(absoluteUrl ?? `${BASE_URL}${path}`, { cache, signal: controller.signal });
  } catch (err) {
    // a deliberate cancel (unmount, tab hidden) is not an API failure; let callers tell the two apart
    if (signal?.aborted) throw err;
    throw new ApiError(`Could not reach ${absoluteUrl ?? BASE_URL}`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  }
  if (!res.ok) {
    throw new ApiError(`${path} failed with ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthResponse>("/health"),
  liveFlights: (limit = 1000, signal?: AbortSignal) =>
    get<LiveFlightsResponse>(`/api/flights/live?limit=${limit}`, signal),
  /** Same shape as liveFlights(); null when no snapshot URL is configured. The browser HTTP cache is allowed
   * (the object says max-age=10), which is the point: most polls cost nothing. */
  liveSnapshot: (signal?: AbortSignal) =>
    MAP_SNAPSHOT_URL ? get<LiveFlightsResponse>("", signal, MAP_SNAPSHOT_URL, "default") : null,
  trajectory: (icao24: string) => get<TrajectoryResponse>(`/api/flights/${icao24}/trajectory`),
  overview: () => get<OverviewStats>("/api/stats/overview"),
  trafficByHour: () => get<TrafficByHourResponse>("/api/stats/traffic-by-hour"),
  byCountry: (limit = 10) => get<ByCountryResponse>(`/api/stats/by-country?limit=${limit}`),
  anomalies: (page = 1, pageSize = 50) =>
    get<AnomaliesResponse>(`/api/anomalies?page=${page}&page_size=${pageSize}`),
  corridors: (limit = 20) => get<CorridorsResponse>(`/api/corridors?limit=${limit}`),
  forecast: () => get<ForecastResponse>("/api/forecast/traffic"),
  // GRU trajectory model (live, 2026-09-28 —) — real predicted-vs-actual, not the paused
  // heading-based estimate `trajectory()`/`departure_iata` above still cover for local dev.
  predictions: () => get<PredictionsResponse>("/api/predictions"),
  aircraftDetail: (icao24: string, trailMinutes = 15) =>
    get<AircraftDetailResponse>(`/api/aircraft/${icao24}?trail_minutes=${trailMinutes}`),
  accuracy: (days = 30) => get<AccuracyResponse>(`/api/stats/accuracy?days=${days}`),
};

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/flights";
