"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, WS_URL } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { useFlightsWebSocket } from "@/hooks/useFlightsWebSocket";
import { useFlightsPolling } from "@/hooks/useFlightsPolling";
import { TopBar } from "@/components/panels/TopBar";
import { AnomalyFeed } from "@/components/panels/AnomalyFeed";
import { ChartsPanel } from "@/components/panels/ChartsPanel";
import { LayerControls } from "@/components/panels/LayerControls";
import { EmergencyBanner } from "@/components/panels/EmergencyBanner";
import { AircraftDetailPanel } from "@/components/panels/AircraftDetailPanel";
import { Skeleton } from "@/components/ui/States";
import { REGIONS, defaultRegion } from "@/lib/regions";
import { getEmergencySquawks } from "@/lib/flightInsights";
import type { AircraftDetailResponse, AnomalyEvent, LiveFlight } from "@/types/api";

// Leaflet touches `window` at import time — importing it during Next.js SSR
// crashes the render. next/dynamic with ssr:false is mandatory here.
const FlightMap = dynamic(() => import("@/components/map/FlightMap"), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full rounded-none" />,
});

// Static per build (NEXT_PUBLIC_* env vars are inlined at build time, not
// runtime) — safe to branch which live-data hook is "enabled" on this
// without violating the rules of hooks, since it never changes between
// renders of a given deployed build.
const CLOUD_MODE = !WS_URL;

export default function DashboardPage() {
  const ws = useFlightsWebSocket(!CLOUD_MODE);
  const polling = useFlightsPolling(CLOUD_MODE);
  const { flights, status: wsStatus, lastMessageAt } = CLOUD_MODE ? polling : ws;
  // 5000 comfortably covers the full corridor set (1,831 as of the
  // 2026-09-21 retrain) -- always fetch and show all of them, no
  // client-side cap. See LayerControls for the read-only count display
  // that replaced the old "corridors shown" slider.
  const { data: corridorsData } = usePolledData(() => api.corridors(5000), 120000);
  const { data: anomaliesData } = usePolledData(() => api.anomalies(1, 100), 15000);

  // Only one region is ever ingested by this cloud deployment (Europe) —
  // no state needed since there's nothing to switch to. See lib/regions.ts.
  const regionId = defaultRegion();
  const [showAircraft, setShowAircraft] = useState(true);
  const [showCorridors, setShowCorridors] = useState(true);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [showProximity, setShowProximity] = useState(false);
  const [anomaliesOnly, setAnomaliesOnly] = useState(false);

  const [anomalyFeedCollapsed, setAnomalyFeedCollapsed] = useState(false);
  const [chartsCollapsed, setChartsCollapsed] = useState(false);

  const [selectedIcao24, setSelectedIcao24] = useState<string | null>(null);
  const [flyToTarget, setFlyToTarget] = useState<[number, number] | null>(null);
  const [aircraftDetail, setAircraftDetail] = useState<AircraftDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Compare mode: a second aircraft, entered via the primary panel's "Compare" button. `picking`
  // is true only in the window between clicking that button and clicking a second aircraft on the
  // map — AircraftLayer's onSelect below checks it to decide whether a click sets the compare slot
  // instead of just replacing the primary selection (its normal behavior).
  const [compareIcao24, setCompareIcao24] = useState<string | null>(null);
  const [compareDetail, setCompareDetail] = useState<AircraftDetailResponse | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [pickingCompare, setPickingCompare] = useState(false);

  // Today's live model accuracy (metrics/daily.json, via the predict Lambda's real evaluation
  // loop) — shown in the detail panel next to the model's own prediction, so "how good is this
  // guess" is answered with a live number, not just asserted.
  const { data: accuracyData } = usePolledData(() => api.accuracy(1), 60000);
  const todayAccuracy = accuracyData?.days[accuracyData.days.length - 1] ?? null;

  const anomalyByIcao = useMemo(() => {
    const map = new Map<string, AnomalyEvent>();
    for (const event of anomaliesData?.events ?? []) {
      map.set(event.icao24, event);
    }
    return map;
  }, [anomaliesData]);

  const visibleCorridors = corridorsData?.corridors ?? [];

  const emergencies = useMemo(() => getEmergencySquawks(flights), [flights]);

  const focusAircraft = useCallback((icao24: string, lat: number | null, lon: number | null) => {
    setSelectedIcao24(icao24);
    if (lat != null && lon != null) setFlyToTarget([lat, lon]);
    // Declutter first: a click on the map is "look at this one thing", so the other rail panels
    // (which are about the whole fleet, not this aircraft) get out of the way automatically.
    setAnomalyFeedCollapsed(true);
    setChartsCollapsed(true);
    setDetailLoading(true);
    api
      .aircraftDetail(icao24)
      .then(setAircraftDetail)
      .catch(() => setAircraftDetail(null))
      .finally(() => setDetailLoading(false));
  }, []);

  const focusCompare = useCallback((icao24: string) => {
    setCompareIcao24(icao24);
    setPickingCompare(false);
    setCompareLoading(true);
    api
      .aircraftDetail(icao24)
      .then(setCompareDetail)
      .catch(() => setCompareDetail(null))
      .finally(() => setCompareLoading(false));
  }, []);

  const closeCompare = useCallback(() => {
    setCompareIcao24(null);
    setCompareDetail(null);
    setPickingCompare(false);
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedIcao24(null);
    setAircraftDetail(null);
    closeCompare();
  }, [closeCompare]);

  const selectAircraft = useCallback(
    (flight: LiveFlight) => {
      // Mid-pick: a click while "Compare with another aircraft" is armed fills the second slot
      // instead of replacing the primary selection (this function's normal job everywhere else).
      if (pickingCompare) {
        if (flight.icao24 !== selectedIcao24) focusCompare(flight.icao24);
        return;
      }
      // Clicking the aircraft currently in the compare slot (outside picking mode) would otherwise
      // leave both slots pointing at the same aircraft — clear compare instead of duplicating it.
      if (flight.icao24 === compareIcao24) closeCompare();
      focusAircraft(flight.icao24, flight.latitude, flight.longitude);
    },
    [pickingCompare, compareIcao24, closeCompare, focusCompare, focusAircraft],
  );

  const selectAnomaly = useCallback(
    (event: AnomalyEvent) => focusAircraft(event.icao24, event.latitude, event.longitude),
    [focusAircraft],
  );

  useEffect(() => {
    if (!selectedIcao24) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (pickingCompare) setPickingCompare(false);
        else if (compareIcao24) closeCompare();
        else closeDetail();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedIcao24, pickingCompare, compareIcao24, closeCompare, closeDetail]);

  // Predictions refresh every minute in reality (the predict Lambda's own schedule) — keep the
  // open panel(s) and the map's dashed path(s) current while an aircraft stays selected, not
  // frozen at the moment of the click.
  useEffect(() => {
    if (!selectedIcao24) return;
    const id = setInterval(() => {
      api
        .aircraftDetail(selectedIcao24)
        .then(setAircraftDetail)
        .catch(() => {});
      if (compareIcao24) {
        api
          .aircraftDetail(compareIcao24)
          .then(setCompareDetail)
          .catch(() => {});
      }
    }, 15000);
    return () => clearInterval(id);
  }, [selectedIcao24, compareIcao24]);

  return (
    // Docked app-shell instead of floating cards over a full-bleed map: a
    // top instrument bar, one left rail stacking input controls above the
    // anomaly feed, and a bottom drawer for analytics -- the map fills
    // exactly the remaining rectangle rather than sitting underneath
    // everything with z-[1000] panels layered on top. No absolute-
    // positioning / z-index juggling needed since every panel is a real
    // sibling in normal flex flow.
    <main className="flex h-screen w-screen flex-col overflow-hidden bg-base">
      <TopBar pollStatus={wsStatus} flights={flights} lastUpdatedAt={lastMessageAt} />

      <div className="relative flex min-h-0 flex-1">
        {/* Shared left rail: layer controls on top (content-sized, own
            scroll if it overflows), anomaly feed docked below it and
            taking the remaining height -- moved here from a separate
            right-hand rail so the map reads as the visual center of the
            page instead of being boxed in on both sides. */}
        <div className="flex w-[260px] flex-shrink-0 flex-col overflow-hidden border-r border-border">
          <LayerControls
            regionId={regionId}
            showAircraft={showAircraft}
            onToggleAircraft={() => setShowAircraft((v) => !v)}
            showCorridors={showCorridors}
            onToggleCorridors={() => setShowCorridors((v) => !v)}
            showHeatmap={showHeatmap}
            onToggleHeatmap={() => setShowHeatmap((v) => !v)}
            showProximity={showProximity}
            onToggleProximity={() => setShowProximity((v) => !v)}
            anomaliesOnly={anomaliesOnly}
            onToggleAnomaliesOnly={() => setAnomaliesOnly((v) => !v)}
            totalCorridors={corridorsData?.total_corridors ?? 0}
            mlPaused={corridorsData?.ml_paused ?? false}
          />
          <AnomalyFeed
            onSelect={selectAnomaly}
            collapsed={anomalyFeedCollapsed}
            onToggleCollapse={() => setAnomalyFeedCollapsed((v) => !v)}
            mlPaused={anomaliesData?.ml_paused ?? false}
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <FlightMap
              region={REGIONS[regionId]}
              flights={flights}
              corridors={visibleCorridors}
              showAircraft={showAircraft}
              showCorridors={showCorridors}
              showHeatmap={showHeatmap}
              showProximity={showProximity}
              anomaliesOnly={anomaliesOnly}
              anomalyByIcao={anomalyByIcao}
              selectedIcao24={selectedIcao24}
              onSelectFlight={selectAircraft}
              flyToTarget={flyToTarget}
              aircraftDetail={aircraftDetail}
              onDeselect={closeDetail}
              compareIcao24={compareIcao24}
              compareDetail={compareDetail}
              positionsAsOf={CLOUD_MODE ? lastMessageAt : null}
            />
            <EmergencyBanner emergencies={emergencies} />
            {selectedIcao24 && (
              <AircraftDetailPanel
                detail={aircraftDetail}
                loading={detailLoading}
                accuracy={todayAccuracy}
                onClose={closeDetail}
                onCompare={() => setPickingCompare(true)}
                picking={pickingCompare}
                onCancelCompare={() => setPickingCompare(false)}
              />
            )}
            {compareIcao24 && (
              <AircraftDetailPanel
                variant="compare"
                detail={compareDetail}
                loading={compareLoading}
                accuracy={todayAccuracy}
                onClose={closeCompare}
              />
            )}
          </div>

          <ChartsPanel
            flights={flights}
            collapsed={chartsCollapsed}
            onToggleCollapse={() => setChartsCollapsed((v) => !v)}
          />
        </div>
      </div>
    </main>
  );
}
