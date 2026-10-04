"use client";

import { Reveal } from "@/components/landing/Reveal";

// One diagram, as few words as possible. Everything here is deployed today.
interface Node {
  name: string;
  note?: string;
  accent?: boolean;
}

interface Lane {
  n: string;
  title: string;
  sub: string;
  nodes: Node[];
}

const LANES: Lane[] = [
  {
    n: "1",
    title: "Data comes from",
    sub: "outside AWS",
    nodes: [
      { name: "Aircraft", note: "broadcast position (ADS-B)" },
      { name: "adsb.lol", note: "volunteer receivers · 8 points over Europe", accent: true },
    ],
  },
  {
    n: "2",
    title: "Processed",
    sub: "AWS · every minute",
    nodes: [
      { name: "Scheduler", note: "1 / min" },
      { name: "Ingest Lambda", note: "fetch · merge · save", accent: true },
      { name: "S3", note: "snapshot · map file · raw archive" },
      { name: "Predict Lambda", note: "GRU model → position in 5 min", accent: true },
    ],
  },
  {
    n: "3",
    title: "Shown",
    sub: "your browser",
    nodes: [
      { name: "Map file", note: "140 KB, straight from S3" },
      { name: "Web page", note: "static · polls every 15 s", accent: true },
      { name: "API Lambda", note: "panel stats" },
    ],
  },
];

function Arrow({ className = "" }: { className?: string }) {
  return (
    <span aria-hidden className={`select-none text-accent-cyan/60 ${className}`}>
      ↓
    </span>
  );
}

export function Pipeline() {
  return (
    <section id="pipeline" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">How it works</p>
        <h2 className="mb-10 text-3xl font-bold text-ink sm:text-4xl">From a radio signal to a dot on the map.</h2>
      </Reveal>

      <div className="grid gap-4 md:grid-cols-3 md:gap-6">
        {LANES.map((lane, i) => (
          <Reveal key={lane.n} delay={i * 80}>
            <div className="glass-panel relative h-full rounded-lg p-5">
              <div className="mb-4 flex items-baseline gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-accent-cyan/40 font-mono text-[11px] text-accent-cyan">
                  {lane.n}
                </span>
                <h3 className="text-[15px] font-semibold text-ink">{lane.title}</h3>
                <span className="font-mono text-[10px] uppercase tracking-wider text-ink-faint">{lane.sub}</span>
              </div>
              <div className="flex flex-col items-stretch gap-1.5">
                {lane.nodes.map((node, j) => (
                  <div key={node.name} className="flex flex-col items-center gap-1.5">
                    <div
                      className={`w-full rounded-md border px-3 py-2 ${
                        node.accent ? "border-accent-cyan/40 bg-accent-cyan/[0.06]" : "border-border bg-white/[0.02]"
                      }`}
                    >
                      <div className="text-[13px] font-medium text-ink">{node.name}</div>
                      {node.note && <div className="text-[11px] text-ink-muted">{node.note}</div>}
                    </div>
                    {j < lane.nodes.length - 1 && <Arrow className="text-xs leading-none" />}
                  </div>
                ))}
              </div>
              {i < LANES.length - 1 && (
                <span
                  aria-hidden
                  className="absolute -bottom-3.5 left-1/2 -translate-x-1/2 text-accent-cyan/60 md:-right-4 md:bottom-auto md:left-auto md:top-1/2 md:-translate-y-1/2 md:translate-x-0 md:rotate-[-90deg]"
                >
                  ↓
                </span>
              )}
            </div>
          </Reveal>
        ))}
      </div>

      <Reveal delay={240}>
        <p className="mt-8 text-[12px] text-ink-faint">
          Real traffic, Europe only, not every aircraft. Predictions are an experiment: the dashboard shows their live error.{" "}
          <a className="text-accent-cyan hover:underline" href="https://github.com/tyxgx/liveflights" target="_blank" rel="noreferrer">
            Source
          </a>
        </p>
      </Reveal>
    </section>
  );
}
