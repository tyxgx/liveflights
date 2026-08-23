"use client";

import { Reveal } from "@/components/landing/Reveal";

interface Note {
  title: string;
  problem: string;
  fix: string;
}

const NOTES: Note[] = [
  {
    title: "A ~$155/month cost bug, found before it became a bill",
    problem:
      "The live-state store was a DynamoDB table rewritten item-by-item on every 1-minute poll — fine at a few hundred items, but once coverage expanded to ~4,600 concurrent aircraft that's ~276,000 item writes an hour. A CloudWatch audit measured 170,571 consumed write-capacity units/hour, not a guess.",
    fix:
      "Replaced the table with a single S3 object overwritten each poll (one PUT/min) plus a small rolling aggregate for hourly stats. Same data, computed on demand instead of pre-written per item — DynamoDB, Athena, Glue, and Step Functions all came out of the stack in the same pass.",
  },
  {
    title: "An IAM permission that only breaks on a cold path",
    problem:
      "S3's GetObject on a key that doesn't exist yet returns an opaque AccessDenied instead of NoSuchKey when the caller lacks s3:ListBucket — S3 won't confirm or deny that a key exists to a caller with no listing rights. Both Lambdas' \"if missing, use a default\" fallback silently broke on the very first cold write.",
    fix:
      "Traced through CloudWatch logs to the real AccessDenied cause (not the destination bug it looked like), then found that scoping ListBucket with an s3:prefix condition doesn't work either — S3's internal exists-check doesn't populate that condition key, so it silently never applies. Fixed with an unconditional (but still read-only, metadata-only) grant.",
  },
  {
    title: "Rate-limited by the data source itself",
    problem:
      "Fanning out to 8 geographic points concurrently against adsb.lol tripped its rate limiter (HTTP 420/429) under real load — a data-source constraint no amount of code review would surface without actually running it against live traffic.",
    fix:
      "Bounded concurrency, a small stagger between request submissions, and a retry-with-backoff budget per point — tuned against the actual failure rate observed in CloudWatch, not a guess at safe numbers.",
  },
  {
    title: "A clustering bug that only showed up at scale",
    problem:
      "An earlier ML pass (route-corridor discovery via DBSCAN) fit one model across the whole coverage area — spanning thousands of kilometers once expanded past a single small region. One cluster absorbed over 90% of all points; the algorithm wasn't wrong, the input geography was too spread out for one fit to mean anything.",
    fix:
      "Split the fit per geographic grid cell instead of one global pass — the distribution flattened out to a plausible spread with no single corridor holding more than ~20% of points. (This ML layer is currently paused — see the note below — but the fix is intact in the codebase.)",
  },
];

export function EngineeringNotes() {
  return (
    <section id="engineering" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">Real problems, real fixes</p>
        <h2 className="mb-4 text-3xl font-bold text-ink sm:text-4xl">Things that broke, and how they got fixed.</h2>
        <p className="mb-14 max-w-2xl text-ink-muted">
          Building this against live traffic surfaced problems no amount of local testing would have —
          a cost bug measured in real dollars, an IAM edge case that only bites on a cold path, a rate
          limit from the data source itself. Here's what actually happened.
        </p>
      </Reveal>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        {NOTES.map((note, i) => (
          <Reveal key={note.title} delay={i * 70}>
            <div className="glass-panel h-full rounded-lg p-5">
              <h3 className="mb-3 text-[15px] font-semibold leading-snug text-ink">{note.title}</h3>
              <div className="mb-3">
                <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-danger/80">Problem</p>
                <p className="text-[13px] leading-relaxed text-ink-muted">{note.problem}</p>
              </div>
              <div>
                <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-accent-teal">Fix</p>
                <p className="text-[13px] leading-relaxed text-ink-muted">{note.fix}</p>
              </div>
            </div>
          </Reveal>
        ))}
      </div>

      <Reveal delay={300}>
        <p className="mt-10 max-w-2xl text-[12px] leading-relaxed text-ink-faint">
          <span className="font-medium text-warn/80">Currently paused:</span> corridor discovery,
          anomaly detection, and per-aircraft trajectory prediction were all built and verified working
          against live data, but are switched off in the deployed stack right now — this build focuses
          on the live-data pipeline and dashboards. The code and the cost-bug fix above both remain in
          the repository.
        </p>
      </Reveal>
    </section>
  );
}
