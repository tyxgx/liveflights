"use client";

import { useEffect } from "react";
import { useMap } from "react-leaflet";

/** Leaflet caches its container's pixel size at init and on `setView`. If
 * that size is wrong at the moment it's read — e.g. a sibling panel (layer
 * controls, anomaly feed) hasn't finished its own layout pass yet, so the
 * map's flex cell hasn't settled to its final width/height — every marker
 * lat/lng gets projected against the wrong pixel grid, which reads as
 * "the whole world crammed into a tiny blob" rather than a normal zoomed-in
 * view. `invalidateSize()` forces Leaflet to re-read the real container
 * size and re-render; this component calls it once after mount (covers the
 * common case: some other panel finishes laying out a tick after the map
 * does) and again on every actual resize of the map's own container. */
export function MapResizeController() {
  const map = useMap();

  useEffect(() => {
    const container = map.getContainer();

    // requestAnimationFrame, not a raw effect call — gives the surrounding
    // flex layout (LayerControls' async-loaded content, AnomalyFeed's
    // first data fetch, etc.) one paint to settle before Leaflet re-checks
    // its size, without an arbitrary fixed delay.
    const raf = requestAnimationFrame(() => map.invalidateSize());

    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(container);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [map]);

  return null;
}
