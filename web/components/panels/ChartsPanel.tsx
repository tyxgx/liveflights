"use client";

import { useState } from "react";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { TrafficForecastChart } from "@/components/charts/TrafficForecastChart";
import { CountriesBar } from "@/components/charts/CountriesBar";
import { AltitudeHistogram } from "@/components/charts/AltitudeHistogram";
import { FlightMixDonut } from "@/components/charts/FlightMixDonut";
import { TrafficRoseChart } from "@/components/charts/TrafficRoseChart";
import { HubHealthBars } from "@/components/charts/HubHealthBars";
import { Leaderboards } from "@/components/panels/Leaderboards";
import type { LiveFlight } from "@/types/api";

type TabId = "traffic" | "composition" | "flow";

const TABS: { id: TabId; label: string }[] = [
  { id: "traffic", label: "Traffic" },
  { id: "composition", label: "Composition" },
  { id: "flow", label: "Flow & Coverage" },
];

// Recharts' ResponsiveContainer needs an ancestor chain with a DEFINITE
// pixel height at every level — a percentage (`h-[calc(100%-Npx)]`) chain
// through a grid cell doesn't reliably resolve and let the chart grow
// unbounded (observed: ~41,000px tall, rendering nothing visible).
// Fixed pixel heights at each level avoid that entirely.
const PANEL_HEIGHT = 280;
const HEADER_HEIGHT = 68;
const LABEL_HEIGHT = 30;
const CHART_HEIGHT = PANEL_HEIGHT - HEADER_HEIGHT - LABEL_HEIGHT - 24; // minus p-3 padding

function Tile({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="p-3">
      <p className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">{title}</p>
      <p className="mb-1.5 text-[10px] leading-snug text-ink-faint">{subtitle}</p>
      <div style={{ height: CHART_HEIGHT }}>{children}</div>
    </div>
  );
}

export function ChartsPanel({
  flights,
  collapsed,
  onToggleCollapse,
}: {
  flights: LiveFlight[];
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const [tab, setTab] = useState<TabId>("traffic");

  if (collapsed) {
    return (
      <button
        onClick={onToggleCollapse}
        className="glass-panel flex h-9 w-full items-center justify-center rounded-lg text-[11px] uppercase tracking-wider text-ink-muted hover:text-ink"
      >
        Show insights ▲
      </button>
    );
  }

  return (
    <Panel className="w-full" style={{ height: PANEL_HEIGHT }}>
      <PanelHeader
        title="Insights"
        right={
          <button onClick={onToggleCollapse} className="text-ink-muted hover:text-ink" title="Collapse">
            Hide ▼
          </button>
        }
      />
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded px-2.5 py-1 text-[11px] font-medium transition-colors ${
              tab === t.id
                ? "bg-accent-cyan/15 text-accent-cyan"
                : "text-ink-muted hover:bg-white/[0.05] hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "traffic" && (
        <div
          className="grid grid-cols-3 divide-x divide-border"
          style={{ height: PANEL_HEIGHT - HEADER_HEIGHT }}
        >
          <Tile title="Traffic by hour" subtitle="Last 24h volume, + 6h forecast where available.">
            <TrafficForecastChart />
          </Tile>
          <Tile title="Top countries" subtitle="Registration country of currently tracked aircraft.">
            <CountriesBar />
          </Tile>
          <Tile title="Altitude distribution" subtitle="How many aircraft are in each altitude band, right now.">
            <AltitudeHistogram flights={flights} />
          </Tile>
        </div>
      )}

      {tab === "composition" && (
        <div
          className="grid grid-cols-3 divide-x divide-border"
          style={{ height: PANEL_HEIGHT - HEADER_HEIGHT }}
        >
          <Tile title="Flight mix" subtitle="Climbing, cruising, descending, or still on the ground.">
            <FlightMixDonut flights={flights} />
          </Tile>
          <Tile title="Leaderboards" subtitle="Fastest and highest aircraft in view right now.">
            <Leaderboards flights={flights} />
          </Tile>
          <Tile title="Altitude distribution" subtitle="How many aircraft are in each altitude band, right now.">
            <AltitudeHistogram flights={flights} />
          </Tile>
        </div>
      )}

      {tab === "flow" && (
        <div
          className="grid grid-cols-2 divide-x divide-border"
          style={{ height: PANEL_HEIGHT - HEADER_HEIGHT }}
        >
          <Tile title="Traffic flow" subtitle="Which direction airborne traffic is heading, right now — not ML, just headings grouped by compass point.">
            <TrafficRoseChart flights={flights} />
          </Tile>
          <Tile title="Hub coverage health" subtitle="Aircraft seen per source region. A bar near zero may mean that region's feed is down, not that it's quiet.">
            <HubHealthBars flights={flights} />
          </Tile>
        </div>
      )}
    </Panel>
  );
}
