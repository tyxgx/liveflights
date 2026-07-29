import type {
  AnomaliesResponse,
  ByCountryResponse,
  CorridorsResponse,
  ForecastResponse,
  HealthResponse,
  LiveFlightsResponse,
  OverviewStats,
  TrafficByHourResponse,
  TrajectoryResponse,
} from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError(`Could not reach the API at ${BASE_URL}`);
  }
  if (!res.ok) {
    throw new ApiError(`${path} failed with ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthResponse>("/health"),
  liveFlights: (limit = 1000) => get<LiveFlightsResponse>(`/api/flights/live?limit=${limit}`),
  trajectory: (icao24: string) => get<TrajectoryResponse>(`/api/flights/${icao24}/trajectory`),
  overview: () => get<OverviewStats>("/api/stats/overview"),
  trafficByHour: () => get<TrafficByHourResponse>("/api/stats/traffic-by-hour"),
  byCountry: (limit = 10) => get<ByCountryResponse>(`/api/stats/by-country?limit=${limit}`),
  anomalies: (page = 1, pageSize = 50) =>
    get<AnomaliesResponse>(`/api/anomalies?page=${page}&page_size=${pageSize}`),
  corridors: (limit = 20) => get<CorridorsResponse>(`/api/corridors?limit=${limit}`),
  forecast: () => get<ForecastResponse>("/api/forecast/traffic"),
};

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/flights";
