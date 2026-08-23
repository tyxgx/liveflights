"use client";

import { Reveal } from "@/components/landing/Reveal";

const FEATURES: { title: string; detail: string }[] = [
  {
    title: "Interactive live map",
    detail: "Every tracked aircraft, real position, real heading — colored by altitude band, with dead-reckoned motion between polls.",
  },
  {
    title: "KPI overview",
    detail: "Active flights, countries in view, and average altitude — computed live from the current snapshot, not cached.",
  },
  {
    title: "Top countries & airlines",
    detail: "Ranked breakdowns of who's actually in the air right now, derived from live registration and callsign data.",
  },
  {
    title: "Altitude distribution",
    detail: "Where the current traffic sits across altitude bands, from ground level to 45,000ft+.",
  },
  {
    title: "Hourly traffic trend",
    detail: "A rolling 48-hour view of traffic volume, built from a small aggregate the ingest Lambda maintains every poll.",
  },
  {
    title: "Honest empty states",
    detail: "Paused features say so explicitly — a dashboard that goes quiet never silently reads as \"all clear\" when it means \"not running\".",
  },
];

export function Features() {
  return (
    <section id="features" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">On the dashboard</p>
        <h2 className="mb-14 text-3xl font-bold text-ink sm:text-4xl">What's actually there to look at.</h2>
      </Reveal>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f, i) => (
          <Reveal key={f.title} delay={i * 50}>
            <div className="glass-panel h-full rounded-lg p-5 transition-colors hover:border-accent-cyan/30">
              <h3 className="mb-2 text-[14px] font-semibold text-ink">{f.title}</h3>
              <p className="text-[13px] leading-relaxed text-ink-muted">{f.detail}</p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
