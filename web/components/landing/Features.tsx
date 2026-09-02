"use client";

import { Reveal } from "@/components/landing/Reveal";

interface Feature {
  title: string;
  detail: string;
  /** Bento span — one item gets real emphasis instead of six identical boxes;
   * the last closes the grid full-width instead of leaving orphaned cells. */
  span?: "wide" | "full";
}

const FEATURES: Feature[] = [
  {
    title: "Interactive live map",
    detail:
      "Every tracked aircraft, real position, real heading — colored by altitude band, with dead-reckoned motion between polls. This is the centerpiece: everything else on the dashboard is a lens on the same live snapshot.",
    span: "wide",
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
    detail:
      "Paused features say so explicitly — a dashboard that goes quiet never silently reads as \"all clear\" when it means \"not running\".",
    span: "full",
  },
];

export function Features() {
  return (
    <section id="features" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">On the dashboard</p>
        <h2 className="mb-14 text-3xl font-bold text-ink sm:text-4xl">What's actually there to look at.</h2>
      </Reveal>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map((f, i) => (
          <Reveal
            key={f.title}
            delay={i * 50}
            className={
              f.span === "wide"
                ? "sm:col-span-2 lg:col-span-2 lg:row-span-2"
                : f.span === "full"
                  ? "sm:col-span-2 lg:col-span-4"
                  : ""
            }
          >
            <div
              className={`glass-panel flex h-full rounded-lg p-5 transition-colors hover:border-accent-cyan/30 ${
                f.span === "wide"
                  ? "flex-col justify-center py-8 lg:py-10"
                  : f.span === "full"
                    ? "flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-6"
                    : "flex-col"
              }`}
            >
              <h3
                className={`font-semibold text-ink ${
                  f.span === "wide" ? "mb-2 text-xl sm:text-2xl" : f.span === "full" ? "text-[14px] sm:w-56 sm:flex-shrink-0" : "mb-2 text-[14px]"
                }`}
              >
                {f.title}
              </h3>
              <p
                className={`leading-relaxed text-ink-muted ${
                  f.span === "wide" ? "max-w-md text-[14px]" : f.span === "full" ? "text-[13px]" : "text-[13px]"
                }`}
              >
                {f.detail}
              </p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
