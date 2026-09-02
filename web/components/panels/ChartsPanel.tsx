"use client";

import { useState } from "react";
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
const PANEL_HEIGHT = 320;
const HEADER_HEIGHT = 48; // single tab + collapse row (merged from two rows in the first layout pass)
const LABEL_HEIGHT = 34; // tile title + subtitle
const TILE_PADDING = 32; // p-4 top+bottom
const CHART_HEIGHT = PANEL_HEIGHT - HEADER_HEIGHT - LABEL_HEIGHT - TILE_PADDING;

function Tile({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  // min-w-0 matters here: a CSS grid track defaults to min-width: auto, so
  // if the chart inside (Recharts' ResponsiveContainer, in particular)
  // ever reports an intrinsic width wider than this tile's fr-share, the
  // grid track — and the whole grid, and the flex column it sits in —
  // grows to fit it instead of clipping the chart down to size. At wide
  // window widths that pushed the total layout past the viewport, and
  // since the app shell has overflow-hidden, the overflow was silently
  // clipped rather than scrollable: the right-hand tile (and its title/
  // subtitle text) just got cut off with no visual indication why.
  return (
    <div className="min-w-0 overflow-hidden p-4">
      <p className="truncate text-[10px] font-medium uppercase tracking-wider text-ink-muted">{title}</p>
      <p className="mb-2 mt-0.5 truncate text-[10px] leading-snug text-ink-faint">{subtitle}</p>
      <div className="min-w-0" style={{ height: CHART_HEIGHT }}>
        {children}
      </div>
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
        className="press flex h-9 w-full flex-shrink-0 items-center justify-center border-t border-border text-[11px] uppercase tracking-wider text-ink-faint transition-colors hover:text-ink"
      >
        Show insights
      </button>
    );
  }

  return (
    <div className="w-full flex-shrink-0 border-t border-border" style={{ height: PANEL_HEIGHT }}>
      <div className="flex items-center gap-1 px-5 pb-2 pt-3">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`press rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors ${
              tab === t.id
                ? "bg-accent-cyan/15 text-accent-cyan"
                : "text-ink-faint hover:bg-white/[0.05] hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
        <button onClick={onToggleCollapse} className="press ml-auto text-ink-faint transition-colors hover:text-ink" title="Collapse">
          Hide
        </button>
      </div>

      {tab === "traffic" && (
        <div
          className="grid min-w-0 grid-cols-3 divide-x divide-border"
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
          className="grid min-w-0 grid-cols-3 divide-x divide-border"
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
          className="grid min-w-0 grid-cols-2 divide-x divide-border"
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
    </div>
  );
}
