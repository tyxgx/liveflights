"use client";

import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { usePolledData } from "@/hooks/usePolledData";
import { RadarField } from "@/components/landing/RadarField";

function LiveStat({ label, value, loading }: { label: string; value: string; loading: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 px-4 py-2 first:pl-0 last:pr-0">
      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">{label}</span>
      <span className="font-mono text-xl font-semibold tabular-nums text-ink sm:text-2xl">
        {loading ? "—" : value}
      </span>
    </div>
  );
}

export function Hero() {
  // Same live snapshot the dashboard itself polls — this strip is not a
  // mockup, it's the actual /api/stats/overview response, refreshed every
  // 15s exactly like the dashboard's own KpiPanel.
  const { data, loading } = usePolledData(api.overview, 15000);

  return (
    <section className="relative flex min-h-screen items-center overflow-hidden px-6 pt-24 sm:px-10">
      <RadarField />
      <div className="relative z-10 mx-auto flex w-full max-w-5xl flex-col items-start gap-8">
        <div className="flex items-center gap-2 rounded-full border border-accent-teal/30 bg-accent-teal/10 px-3 py-1">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-teal opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-teal" />
          </span>
          <span className="text-[11px] font-medium uppercase tracking-wider text-accent-teal">
            Live now — real ADS-B data, not a demo
          </span>
        </div>

        <h1 className="max-w-3xl text-4xl font-bold leading-[1.05] tracking-tight text-ink sm:text-6xl">
          Real-time air traffic,
          <br />
          <span className="bg-gradient-to-r from-accent-cyan to-accent-teal bg-clip-text text-transparent">
            built end-to-end on AWS.
          </span>
        </h1>

        <p className="max-w-2xl text-base leading-relaxed text-ink-muted sm:text-lg">
          A live-data pipeline that pulls real aircraft transponder broadcasts, ships them through a
          serverless AWS pipeline every minute, and renders them on an interactive map — with real
          dashboards computed on the fly, not canned. This page shows what was actually built and how
          it actually works.
        </p>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <a
            href="/live.html"
            className="rounded-lg bg-accent-cyan px-5 py-3 text-sm font-semibold text-base shadow-[0_0_24px_rgba(34,211,238,0.35)] transition-transform hover:scale-[1.03]"
          >
            Open the Live Dashboard →
          </a>
          <a
            href="https://github.com/tyxgx/liveflights"
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-border-strong px-5 py-3 text-sm font-medium text-ink transition-colors hover:bg-white/[0.04]"
          >
            View source on GitHub
          </a>
        </div>

        <div className="glass-panel mt-4 flex w-full flex-wrap divide-x divide-border rounded-lg px-4 py-3 sm:w-auto">
          <LiveStat label="Aircraft tracked" value={formatNumber(data?.active_flights)} loading={loading} />
          <LiveStat label="Countries" value={formatNumber(data?.countries)} loading={loading} />
          <LiveStat
            label="Avg altitude"
            value={data?.avg_altitude_ft != null ? `${formatNumber(data.avg_altitude_ft)} ft` : "—"}
            loading={loading}
          />
          <LiveStat label="Refresh interval" value="60s" loading={false} />
        </div>
      </div>
    </section>
  );
}
