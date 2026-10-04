"use client";

import { Reveal } from "@/components/landing/Reveal";

interface Block {
  n: string;
  title: string;
  lead: string;
  points: string[];
}

// Three plain questions a visitor has. Everything below describes what is deployed today.
const BLOCKS: Block[] = [
  {
    n: "1",
    title: "Where the data comes from",
    lead: "adsb.lol, a community network of volunteer radio receivers that pick up the position signal every aircraft broadcasts (ADS-B).",
    points: [
      "8 points across Europe are asked for their aircraft (each covers a 250 nm circle, the most the service allows), then merged and de-duplicated by aircraft address.",
      "It is real traffic, not a simulation. It is also not every aircraft: it depends on where volunteers have receivers.",
    ],
  },
  {
    n: "2",
    title: "How it is processed",
    lead: "Once a minute a scheduled AWS Lambda does the work. Nothing runs between polls.",
    points: [
      "It writes the current snapshot to S3, a small compressed copy for the map, a raw archive of every reading, and an hourly traffic summary.",
      "A second Lambda runs a PyTorch model (GRU, exported to ONNX) that predicts where each aircraft will be in 5 minutes. Each prediction is later compared with the real position, and the median error is shown on the dashboard.",
      "Corridors (busy routes) and unusual flights are found by models trained on 28 days of past data.",
    ],
  },
  {
    n: "3",
    title: "How it is shown",
    lead: "A static web page on S3. There is no server behind it while you browse.",
    points: [
      "Every 15 seconds the page fetches the small snapshot (about 140 KB) and moves each aircraft along its real heading and speed between updates.",
      "The side panels (countries, altitude, traffic over time) are calculated from the same snapshot by a small API.",
      "Click an aircraft to see its details and where the model expects it to be next.",
    ],
  },
];

export function Pipeline() {
  return (
    <section id="pipeline" className="mx-auto max-w-4xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">How it works</p>
        <h2 className="mb-4 text-3xl font-bold text-ink sm:text-4xl">From a radio signal to a dot on the map.</h2>
        <p className="mb-12 max-w-2xl text-ink-muted">
          This is a working demonstration, not a product. Each step below runs on AWS right now; the numbers above come from it.
        </p>
      </Reveal>

      <div className="flex flex-col gap-6">
        {BLOCKS.map((b, i) => (
          <Reveal key={b.n} delay={i * 60}>
            <div className="glass-panel rounded-lg p-6">
              <div className="mb-3 flex items-center gap-3">
                <span className="flex h-7 w-7 items-center justify-center rounded-full border border-accent-cyan/40 font-mono text-[11px] text-accent-cyan">
                  {b.n}
                </span>
                <h3 className="text-lg font-semibold text-ink">{b.title}</h3>
              </div>
              <p className="mb-3 text-[14px] leading-relaxed text-ink">{b.lead}</p>
              <ul className="flex flex-col gap-2">
                {b.points.map((p) => (
                  <li key={p} className="text-[13px] leading-relaxed text-ink-muted">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>
        ))}
      </div>

      <Reveal delay={200}>
        <p className="mt-6 text-[12px] leading-relaxed text-ink-faint">
          Limits: coverage is Europe only and depends on volunteer receivers; predictions are an experiment, so the accuracy is
          shown live rather than claimed. <a className="text-accent-cyan hover:underline" href="https://github.com/tyxgx/liveflights" target="_blank" rel="noreferrer">Source and docs on GitHub</a>.
        </p>
      </Reveal>
    </section>
  );
}
