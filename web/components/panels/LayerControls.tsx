"use client";

import { Panel } from "@/components/ui/Panel";
import { ALTITUDE_BANDS } from "@/lib/format";
import { REGIONS, type RegionId } from "@/lib/regions";

interface Props {
  regionId: RegionId;
  onRegionChange: (region: RegionId) => void;
  showAircraft: boolean;
  onToggleAircraft: () => void;
  showCorridors: boolean;
  onToggleCorridors: () => void;
  showHeatmap: boolean;
  onToggleHeatmap: () => void;
  showProximity: boolean;
  onToggleProximity: () => void;
  anomaliesOnly: boolean;
  onToggleAnomaliesOnly: () => void;
  corridorLimit: number;
  onCorridorLimitChange: (n: number) => void;
  totalCorridors: number;
  mlPaused: boolean;
}

function Toggle({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors ${
        active
          ? "bg-accent-cyan/15 text-accent-cyan"
          : "bg-white/[0.03] text-ink-muted hover:bg-white/[0.06] hover:text-ink"
      }`}
    >
      {label}
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-muted">{children}</p>
  );
}

export function LayerControls({
  regionId,
  onRegionChange,
  showAircraft,
  onToggleAircraft,
  showCorridors,
  onToggleCorridors,
  showHeatmap,
  onToggleHeatmap,
  showProximity,
  onToggleProximity,
  anomaliesOnly,
  onToggleAnomaliesOnly,
  corridorLimit,
  onCorridorLimitChange,
  totalCorridors,
  mlPaused,
}: Props) {
  return (
    // Capped + internally scrollable: this panel's content (region +
    // layers + altitude legend, plus conditional corridor/proximity notes)
    // can get taller than short viewports, and it sits above the Anomaly
    // Feed in a shared-height flex column — without a cap, a long content
    // state pushes the feed almost entirely off-screen instead of just
    // scrolling its own content. See app/live/page.tsx for the column.
    <Panel className="flex max-h-[55vh] w-[250px] flex-col overflow-hidden">
      <div className="overflow-y-auto p-4">
        <SectionLabel>Region</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {Object.values(REGIONS).map((r) => (
            <Toggle
              key={r.id}
              label={r.label}
              active={r.id === regionId}
              onClick={() => onRegionChange(r.id)}
            />
          ))}
        </div>

        <div className="mt-4">
          <SectionLabel>Layers</SectionLabel>
          <div className="flex flex-wrap gap-2">
            <Toggle label="Aircraft" active={showAircraft} onClick={onToggleAircraft} />
            <Toggle label="Corridors" active={showCorridors} onClick={onToggleCorridors} />
            <Toggle label="Density heatmap" active={showHeatmap} onClick={onToggleHeatmap} />
            <Toggle label="Proximity lines" active={showProximity} onClick={onToggleProximity} />
            <Toggle label="Anomalies only" active={anomaliesOnly} onClick={onToggleAnomaliesOnly} />
          </div>
          {showProximity && (
            <p className="mt-2.5 text-[10px] leading-relaxed text-ink-faint">
              Lines connect aircraft within ~3nm and ~1,000ft of each other right now — geometry
              only, not a real ATC-grade conflict alert.
            </p>
          )}
        </div>

        {showCorridors && (
          <div className="mt-4 border-t border-border pt-3">
            <div className="mb-1.5 flex items-center justify-between text-[10px] text-ink-faint">
              <span>Corridors shown</span>
              <span className="font-mono tabular-nums">
                {corridorLimit} / {totalCorridors}
              </span>
            </div>
            {mlPaused ? (
              <p className="text-[10px] leading-relaxed text-warn/80">
                ML is paused on this deployment — corridor discovery isn&apos;t running.
              </p>
            ) : (
              <input
                type="range"
                min={5}
                max={Math.max(totalCorridors, 5)}
                step={5}
                value={corridorLimit}
                onChange={(e) => onCorridorLimitChange(Number(e.target.value))}
                className="w-full accent-accent-cyan"
              />
            )}
          </div>
        )}

        <div className="mt-4 border-t border-border pt-3">
          <SectionLabel>Altitude</SectionLabel>
          <div className="flex flex-wrap gap-x-3 gap-y-1.5">
            {ALTITUDE_BANDS.map((b) => (
              <div key={b.label} className="flex items-center gap-1.5 text-[10px] text-ink-muted">
                <span className="h-2 w-2 flex-shrink-0 rounded-full" style={{ backgroundColor: b.color }} />
                <span>{b.label}</span>
              </div>
            ))}
            <div className="flex items-center gap-1.5 text-[10px] text-ink-muted">
              <span className="h-2 w-2 flex-shrink-0 rounded-full bg-danger" />
              <span>Anomaly</span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
