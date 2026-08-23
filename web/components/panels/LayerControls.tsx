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
      className={`rounded px-2.5 py-1 text-[11px] transition-colors ${
        active
          ? "bg-accent-cyan/15 text-accent-cyan"
          : "text-ink-muted hover:bg-white/[0.05] hover:text-ink"
      }`}
    >
      {label}
    </button>
  );
}

export function LayerControls({
  regionId,
  onRegionChange,
  showAircraft,
  onToggleAircraft,
  showCorridors,
  onToggleCorridors,
  anomaliesOnly,
  onToggleAnomaliesOnly,
  corridorLimit,
  onCorridorLimitChange,
  totalCorridors,
  mlPaused,
}: Props) {
  return (
    <Panel className="w-[240px] p-3">
      <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-muted">Region</p>
      <div className="flex flex-col items-start gap-1">
        {Object.values(REGIONS).map((r) => (
          <Toggle
            key={r.id}
            label={r.label}
            active={r.id === regionId}
            onClick={() => onRegionChange(r.id)}
          />
        ))}
      </div>

      <p className="mb-2 mt-3 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
        Layers
      </p>
      <div className="flex flex-col items-start gap-1">
        <Toggle label="Aircraft" active={showAircraft} onClick={onToggleAircraft} />
        <Toggle label="Corridors" active={showCorridors} onClick={onToggleCorridors} />
        <Toggle label="Anomalies only" active={anomaliesOnly} onClick={onToggleAnomaliesOnly} />
      </div>

      {showCorridors && (
        <div className="mt-3">
          <div className="flex items-center justify-between text-[10px] text-ink-faint">
            <span>Corridors shown</span>
            <span className="font-mono tabular-nums">
              {corridorLimit} / {totalCorridors}
            </span>
          </div>
          {mlPaused ? (
            <p className="mt-1.5 text-[10px] leading-relaxed text-warn/80">
              ML is currently paused on this deployment — corridor discovery and anomaly detection aren&apos;t running. This build focuses on live flight data and dashboards only.
            </p>
          ) : (
            <input
              type="range"
              min={5}
              max={Math.max(totalCorridors, 5)}
              step={5}
              value={corridorLimit}
              onChange={(e) => onCorridorLimitChange(Number(e.target.value))}
              className="mt-1 w-full accent-accent-cyan"
            />
          )}
        </div>
      )}

      <div className="mt-3 border-t border-border pt-2">
        <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
          Altitude
        </p>
        <div className="space-y-1">
          {ALTITUDE_BANDS.map((b) => (
            <div key={b.label} className="flex items-center gap-1.5 text-[10px] text-ink-muted">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: b.color }} />
              <span>{b.label}</span>
            </div>
          ))}
          <div className="flex items-center gap-1.5 text-[10px] text-ink-muted">
            <span className="h-2 w-2 rounded-full bg-danger" />
            <span>Anomaly</span>
          </div>
        </div>
      </div>
    </Panel>
  );
}
