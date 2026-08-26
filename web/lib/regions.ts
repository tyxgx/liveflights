// This cloud deployment only ever ingests Europe (see infra/terraform's
// adsb_lol_points) — India/US/"all regions" were local-dev-stack-only
// options that didn't correspond to any live data here, so selecting them
// just re-centered the map on an empty area while the KPI/charts stayed on
// the (unfiltered) Europe dataset. Removed rather than left half-working;
// India/US region config still exists for the local `make up` stack in
// ingestion/config.py if multi-region cloud coverage comes back later.
export type RegionId = "europe";

export interface RegionConfig {
  id: RegionId;
  label: string;
  center: [number, number];
  zoom: number;
}

export const REGIONS: Record<RegionId, RegionConfig> = {
  europe: { id: "europe", label: "Europe", center: [50.5, 10.5], zoom: 5 },
};

export function defaultRegion(): RegionId {
  return "europe";
}
