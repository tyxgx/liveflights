"use client";

import { Panel, PanelHeader } from "@/components/ui/Panel";
import { TrafficForecastChart } from "@/components/charts/TrafficForecastChart";
import { CountriesBar } from "@/components/charts/CountriesBar";
import { AltitudeHistogram } from "@/components/charts/AltitudeHistogram";
import type { LiveFlight } from "@/types/api";

export function ChartsPanel({
  flights,
  collapsed,
  onToggleCollapse,
}: {
  flights: LiveFlight[];
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  if (collapsed) {
    return (
      <button
        onClick={onToggleCollapse}
        className="glass-panel flex h-9 w-full items-center justify-center rounded-lg text-[11px] uppercase tracking-wider text-ink-muted hover:text-ink"
      >
        Show charts ▲
      </button>
    );
  }

  // Recharts' ResponsiveContainer needs an ancestor chain with a DEFINITE
  // pixel height at every level — a percentage (`h-[calc(100%-Npx)]`) chain
  // through a grid cell doesn't reliably resolve and let the chart grow
  // unbounded (observed: ~41,000px tall, rendering nothing visible).
  // Fixed pixel heights at each level avoid that entirely.
  const PANEL_HEIGHT = 260;
  const HEADER_HEIGHT = 45;
  const LABEL_HEIGHT = 18;
  const CHART_HEIGHT = PANEL_HEIGHT - HEADER_HEIGHT - LABEL_HEIGHT - 24; // minus p-3 padding

  return (
    <Panel className="w-full" style={{ height: PANEL_HEIGHT }}>
      <PanelHeader
        title="Traffic & Environment"
        right={
          <button onClick={onToggleCollapse} className="text-ink-muted hover:text-ink" title="Collapse">
            Hide ▼
          </button>
        }
      />
      <div className="grid grid-cols-3 divide-x divide-border" style={{ height: PANEL_HEIGHT - HEADER_HEIGHT }}>
        <div className="p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
            Traffic by hour + 6h forecast
          </p>
          <div style={{ height: CHART_HEIGHT }}>
            <TrafficForecastChart />
          </div>
        </div>
        <div className="p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
            Top countries
          </p>
          <div style={{ height: CHART_HEIGHT }}>
            <CountriesBar />
          </div>
        </div>
        <div className="p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
            Altitude distribution (live)
          </p>
          <div style={{ height: CHART_HEIGHT }}>
            <AltitudeHistogram flights={flights} />
          </div>
        </div>
      </div>
    </Panel>
  );
}
