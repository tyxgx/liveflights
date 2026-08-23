"use client";

import { Reveal } from "@/components/landing/Reveal";

const GROUPS: { label: string; items: string[] }[] = [
  { label: "AWS", items: ["Lambda", "S3", "API Gateway", "EventBridge Scheduler", "IAM", "CloudWatch", "ECR", "Kinesis Firehose"] },
  { label: "Infra", items: ["Terraform", "Docker", "GitHub Actions"] },
  { label: "Backend", items: ["Python", "FastAPI", "Mangum", "boto3"] },
  { label: "Frontend", items: ["Next.js", "TypeScript", "React", "Tailwind CSS", "Leaflet", "Recharts"] },
];

export function TechStack() {
  return (
    <section id="stack" className="mx-auto max-w-5xl px-6 py-24 sm:px-10">
      <Reveal>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-accent-cyan">Under the hood</p>
        <h2 className="mb-12 text-3xl font-bold text-ink sm:text-4xl">Tech stack</h2>
      </Reveal>
      <div className="grid grid-cols-1 gap-8 sm:grid-cols-2">
        {GROUPS.map((g, gi) => (
          <Reveal key={g.label} delay={gi * 80}>
            <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-ink-faint">{g.label}</p>
            <div className="flex flex-wrap gap-2">
              {g.items.map((item) => (
                <span
                  key={item}
                  className="rounded-md border border-border bg-base-panel px-3 py-1.5 text-[12px] font-medium text-ink-muted transition-colors hover:border-accent-cyan/40 hover:text-ink"
                >
                  {item}
                </span>
              ))}
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
