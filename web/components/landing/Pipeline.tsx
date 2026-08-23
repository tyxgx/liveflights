"use client";

import { Reveal } from "@/components/landing/Reveal";

interface Step {
  n: string;
  title: string;
  detail: string;
  tag: string;
}

const STEPS: Step[] = [
  {
    n: "01",
    title: "adsb.lol — real transponder broadcasts",
    detail:
      "8 hub points across Europe (250nm radius each — adsb.lol's hard cap, one point can't cover a continent), fetched concurrently with staggered timing and retry-on-429 to stay under its rate limit, then merged and deduped by icao24.",
    tag: "Data source",
  },
  {
    n: "02",
    title: "EventBridge Scheduler — every 1 minute",
    detail:
      "Invokes the ingest Lambda on a fixed schedule. No queue, no always-on worker — the whole ingestion side only runs for the ~seconds it takes to fetch and write, once a minute.",
    tag: "AWS EventBridge",
  },
  {
    n: "03",
    title: "Ingest Lambda — fetch, merge, write",
    detail:
      "Writes live/latest.json (the full current snapshot, fully overwritten — one S3 PUT per poll) and stats/hourly.json (a small rolling 48h aggregate). Also streams a raw archival copy to S3 via Kinesis Firehose for later analysis.",
    tag: "AWS Lambda · Python",
  },
  {
    n: "04",
    title: "API Lambda — stats computed on the fly",
    detail:
      "A FastAPI app behind API Gateway reads those two small S3 objects and computes every stat in plain Python — active flights, top countries, airline activity, altitude distribution, hourly traffic. No database, no query engine, no warehouse in the loop.",
    tag: "API Gateway · FastAPI",
  },
  {
    n: "05",
    title: "Dashboard — polls every 15 seconds",
    detail:
      "A static Next.js app on S3 polls the live endpoint and dead-reckons each aircraft's position between polls using its real heading and velocity, so movement on the map reads smooth, not stepped.",
    tag: "Next.js · Leaflet",
  },
];

export function Pipeline() {
  return (
    <section id="pipeline" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">
          How it actually works
        </p>
        <h2 className="mb-4 text-3xl font-bold text-ink sm:text-4xl">One request, five hops, no idle cost.</h2>
        <p className="mb-14 max-w-2xl text-ink-muted">
          Every hop below is a real, currently-deployed AWS resource — not a diagram of a plan. The
          whole pipeline scales to zero between polls: nothing runs continuously except the schedule
          itself.
        </p>
      </Reveal>

      <div className="relative">
        <div className="absolute bottom-0 left-[15px] top-2 hidden w-px bg-gradient-to-b from-accent-cyan/40 via-border to-transparent sm:block" />
        <div className="flex flex-col gap-8">
          {STEPS.map((s, i) => (
            <Reveal key={s.n} delay={i * 60}>
              <div className="relative flex gap-5 pl-0 sm:pl-10">
                <div className="absolute left-0 top-1 hidden h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border border-accent-cyan/40 bg-base font-mono text-[11px] text-accent-cyan sm:flex">
                  {s.n}
                </div>
                <div className="glass-panel w-full rounded-lg p-5">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-accent-teal sm:hidden">
                      {s.n}
                    </span>
                    <h3 className="text-base font-semibold text-ink">{s.title}</h3>
                    <span className="rounded bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium text-ink-faint">
                      {s.tag}
                    </span>
                  </div>
                  <p className="text-[13px] leading-relaxed text-ink-muted">{s.detail}</p>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
