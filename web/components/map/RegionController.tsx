"use client";

import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import type { RegionConfig } from "@/lib/regions";

// MapContainer's `center`/`zoom` props only apply on initial mount — react-
// leaflet does not re-run them on prop changes. Recenter imperatively via
// map.setView whenever the selected region changes after mount.
export function RegionController({ region }: { region: RegionConfig }) {
  const map = useMap();
  const mounted = useRef(false);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    map.setView(region.center, region.zoom, { animate: true });
  }, [region, map]);

  return null;
}
