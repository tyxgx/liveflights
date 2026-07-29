export type RegionId = "india" | "europe" | "us" | "all";

export interface RegionConfig {
  id: RegionId;
  label: string;
  center: [number, number];
  zoom: number;
}

// Keep in sync with ingestion/config.py:REGION_BBOXES — same four regions,
// same idea (India is the target-audience default; Europe/US stay fully
// selectable, not replaced).
export const REGIONS: Record<RegionId, RegionConfig> = {
  india: { id: "india", label: "India", center: [22.0, 79.0], zoom: 5 },
  europe: { id: "europe", label: "Europe", center: [50.5, 10.5], zoom: 5 },
  us: { id: "us", label: "United States", center: [39.0, -98.0], zoom: 4 },
  all: { id: "all", label: "All regions", center: [20.0, 30.0], zoom: 2 },
};

const VALID_REGIONS = new Set<string>(Object.keys(REGIONS));

function isRegionId(value: string): value is RegionId {
  return VALID_REGIONS.has(value);
}

export function defaultRegion(): RegionId {
  const envRegion = process.env.NEXT_PUBLIC_DEFAULT_REGION;
  if (envRegion && isRegionId(envRegion)) return envRegion;
  return "india";
}
