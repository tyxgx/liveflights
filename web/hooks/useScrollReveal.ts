"use client";

import { useEffect, useRef, useState } from "react";

/**
 * IntersectionObserver-based scroll reveal — deliberately not a GSAP/Framer
 * dependency (this whole site is a static `next export` bundle; every KB
 * here ships to first paint). Fires once per element, then disconnects.
 * Respects prefers-reduced-motion by reporting "already visible" instantly,
 * per the UX guidance this project follows elsewhere (see AGENTS/skills).
 */
export function useScrollReveal<T extends HTMLElement>(threshold = 0.15) {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold, rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return { ref, visible };
}
