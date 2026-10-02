"use client";

import { useMapEvents } from "react-leaflet";

/** Clicking empty map (not an aircraft marker — those stop propagation via Leaflet's own marker
 * click handling) clears the current selection, same as the panel's own close button. */
export function DeselectController({ onDeselect }: { onDeselect: () => void }) {
  useMapEvents({
    click: () => onDeselect(),
  });
  return null;
}
