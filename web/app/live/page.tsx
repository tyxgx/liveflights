"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import { api, WS_URL } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { useFlightsWebSocket } from "@/hooks/useFlightsWebSocket";
import { useFlightsPolling } from "@/hooks/useFlightsPolling";
import { TopBar } from "@/components/panels/TopBar";
import { AnomalyFeed } from "@/components/panels/AnomalyFeed";
import { ChartsPanel } from "@/components/panels/ChartsPanel";
import { LayerControls } from "@/components/panels/LayerControls";
import { EmergencyBanner } from "@/components/panels/EmergencyBanner";
import { Skeleton } from "@/components/ui/States";
import { REGIONS, defaultRegion } from "@/lib/regions";
import { getEmergencySquawks } from "@/lib/flightInsights";
import type { AnomalyEvent, LiveFlight, TrajectoryResponse } from "@/types/api";

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
  const { data: corridorsData } = usePolledData(() => api.corridors(200), 120000);
  const { data: anomaliesData } = usePolledData(() => api.anomalies(1, 100), 15000);

  // Only one region is ever ingested by this cloud deployment (Europe) —
  // no state needed since there's nothing to switch to. See lib/regions.ts.
  const regionId = defaultRegion();
  const [showAircraft, setShowAircraft] = useState(true);
  const [showCorridors, setShowCorridors] = useState(true);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [showProximity, setShowProximity] = useState(false);
  const [anomaliesOnly, setAnomaliesOnly] = useState(false);
  const [corridorLimit, setCorridorLimit] = useState(20);

  const [anomalyFeedCollapsed, setAnomalyFeedCollapsed] = useState(false);
  const [chartsCollapsed, setChartsCollapsed] = useState(false);

  const [selectedIcao24, setSelectedIcao24] = useState<string | null>(null);
  const [flyToTarget, setFlyToTarget] = useState<[number, number] | null>(null);
  const [trajectory, setTrajectory] = useState<TrajectoryResponse | null>(null);

  const anomalyByIcao = useMemo(() => {
    const map = new Map<string, AnomalyEvent>();
    for (const event of anomaliesData?.events ?? []) {
      map.set(event.icao24, event);
    }
    return map;
  }, [anomaliesData]);

  const visibleCorridors = useMemo(
    () => (corridorsData?.corridors ?? []).slice(0, corridorLimit),
    [corridorsData, corridorLimit],
  );

  const emergencies = useMemo(() => getEmergencySquawks(flights), [flights]);

  const selectAircraft = useCallback((flight: LiveFlight) => {
    setSelectedIcao24(flight.icao24);
    if (flight.latitude != null && flight.longitude != null) {
      setFlyToTarget([flight.latitude, flight.longitude]);
    }
    api
      .trajectory(flight.icao24)
      .then(setTrajectory)
      .catch(() => setTrajectory(null));
  }, []);

  const selectAnomaly = useCallback(
    (event: AnomalyEvent) => {
      setSelectedIcao24(event.icao24);
      if (event.latitude != null && event.longitude != null) {
        setFlyToTarget([event.latitude, event.longitude]);
      }
      const liveMatch = flights.find((f) => f.icao24 === event.icao24);
      if (liveMatch) {
        api
          .trajectory(event.icao24)
          .then(setTrajectory)
          .catch(() => setTrajectory(null));
      } else {
        setTrajectory(null);
      }
    },
    [flights],
  );

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
            corridorLimit={corridorLimit}
            onCorridorLimitChange={setCorridorLimit}
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
              trajectory={trajectory}
            />
            <EmergencyBanner emergencies={emergencies} />
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
