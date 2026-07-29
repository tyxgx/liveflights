"use client";

import { useEffect } from "react";
import { useMap } from "react-leaflet";

export function FlyToController({ target }: { target: [number, number] | null }) {
  const map = useMap();

  useEffect(() => {
    if (target) {
      map.flyTo(target, Math.max(map.getZoom(), 7), { duration: 1.1 });
    }
  }, [target, map]);

  return null;
}
