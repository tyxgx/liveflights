"use client";

// Purely decorative — a CSS-only radar sweep + a scatter of pulsing dots
// standing in for aircraft. No canvas, no animation library: a conic
// gradient rotated via CSS keyframes and a handful of absolutely
// positioned divs. Respects prefers-reduced-motion by freezing the sweep.
const DOTS = [
  { top: "22%", left: "18%", delay: "0s" },
  { top: "35%", left: "62%", delay: "0.6s" },
  { top: "58%", left: "40%", delay: "1.2s" },
  { top: "70%", left: "75%", delay: "0.3s" },
  { top: "15%", left: "48%", delay: "1.8s" },
  { top: "48%", left: "22%", delay: "0.9s" },
  { top: "80%", left: "30%", delay: "1.5s" },
  { top: "30%", left: "85%", delay: "2.1s" },
];

export function RadarField() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="absolute left-1/2 top-1/2 h-[140vmax] w-[140vmax] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-[0.07] motion-reduce:animate-none [animation:radar-spin_22s_linear_infinite]"
        style={{
          background:
            "conic-gradient(from 0deg, rgba(34,211,238,0.9) 0deg, rgba(34,211,238,0) 60deg, rgba(34,211,238,0) 360deg)",
        }}
      />
      {[1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-accent-cyan/[0.06]"
          style={{ width: `${i * 22}vmax`, height: `${i * 22}vmax` }}
        />
      ))}
      {DOTS.map((d, i) => (
        <span
          key={i}
          className="absolute h-1.5 w-1.5 rounded-full bg-accent-teal/70 motion-reduce:animate-none [animation:radar-blip_3s_ease-in-out_infinite]"
          style={{ top: d.top, left: d.left, animationDelay: d.delay }}
        />
      ))}
      <div
        className="absolute inset-0"
        style={{ background: "radial-gradient(ellipse at 50% 30%, transparent 0%, #0a0e16 75%)" }}
      />
      <style>{`
        @keyframes radar-spin { to { transform: translate(-50%, -50%) rotate(360deg); } }
        @keyframes radar-blip {
          0%, 100% { opacity: 0.25; transform: scale(1); }
          50% { opacity: 1; transform: scale(1.6); }
        }
      `}</style>
    </div>
  );
}
