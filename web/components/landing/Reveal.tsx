"use client";

import type { ReactNode } from "react";
import { useScrollReveal } from "@/hooks/useScrollReveal";

/** Fade+rise on scroll into view — matches the "Scroll Reveal / Subtle"
 * spec (12px rise, ~350ms, power1.out-like easing) rather than anything
 * flashier; this is a technical portfolio piece, not an ad. */
export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const { ref, visible } = useScrollReveal<HTMLDivElement>();
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(12px)",
        transition: `opacity 400ms cubic-bezier(0.22,1,0.36,1) ${delay}ms, transform 400ms cubic-bezier(0.22,1,0.36,1) ${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}
