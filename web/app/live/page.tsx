"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import { api, WS_URL } from "@/lib/api";
import { usePolledData } from "@/hooks/usePolledData";
import { useFlightsWebSocket } from "@/hooks/useFlightsWebSocket";
import { useFlightsPolling } from "@/hooks/useFlightsPolling";
import { KpiPanel } from "@/components/panels/KpiPanel";
import { PipelineHealthStrip } from "@/components/panels/PipelineHealthStrip";
import { CloudStatusStrip } from "@/components/panels/CloudStatusStrip";
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
    <main className="relative h-screen w-screen overflow-hidden bg-base">
      <div className="absolute inset-0">
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
      </div>

      <EmergencyBanner emergencies={emergencies} />

      {/* Top-left: KPI cards + pipeline health strip underneath.
          z-[1000]+ is required: Leaflet's internal panes use z-index up to
          700, and neither <main> nor the map wrapper establishes its own
          stacking context, so without an explicit z-index here these
          floating panels render BEHIND the map regardless of DOM order. */}
      <div className="pointer-events-none absolute left-4 top-4 z-[1000] flex flex-col">
        <a
          href="/index.html"
          className="glass-panel pointer-events-auto mb-2 flex w-fit items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-medium text-ink-muted transition-colors hover:text-accent-cyan"
        >
          <span className="text-sm leading-none">←</span>
          <span className="font-mono">liveflights</span>
        </a>
        <div className="pointer-events-auto">
          <KpiPanel />
        </div>
        <div className="pointer-events-auto -mt-px">
          {CLOUD_MODE ? (
            <CloudStatusStrip pollStatus={wsStatus} flights={flights} lastUpdatedAt={lastMessageAt} />
          ) : (
            <PipelineHealthStrip wsStatus={wsStatus} liveFlightCount={flights.length} />
          )}
        </div>
      </div>

      {/* Right side: layer toggles + anomaly feed, stacked in a real flex
          column (not two independently-positioned absolute panels with a
          guessed pixel gap) so a taller layer-toggle panel can never
          overlap the feed below it. */}
      <div className="pointer-events-none absolute bottom-4 right-4 top-4 z-[1000] flex flex-col gap-4">
        <div className="pointer-events-auto flex-shrink-0">
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
        </div>
        <div className="pointer-events-auto min-h-0 flex-1">
          <AnomalyFeed
            onSelect={selectAnomaly}
            collapsed={anomalyFeedCollapsed}
            onToggleCollapse={() => setAnomalyFeedCollapsed((v) => !v)}
            mlPaused={anomaliesData?.ml_paused ?? false}
          />
        </div>
      </div>

      {/* Bottom: charts panel */}
      <div className="pointer-events-auto absolute bottom-4 left-4 right-[360px] z-[1000]">
        <ChartsPanel
          flights={flights}
          collapsed={chartsCollapsed}
          onToggleCollapse={() => setChartsCollapsed((v) => !v)}
        />
      </div>
    </main>
  );
}
