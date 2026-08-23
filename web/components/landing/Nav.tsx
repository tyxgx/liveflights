"use client";

import { useEffect, useState } from "react";

export function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <nav
      className={`fixed inset-x-0 top-0 z-50 flex items-center justify-between px-6 py-4 transition-colors duration-300 sm:px-10 ${
        scrolled ? "glass-panel border-x-0 border-t-0" : "border-b border-transparent bg-transparent"
      }`}
    >
      <a href="/index.html" className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 rounded-full bg-accent-cyan shadow-[0_0_10px_2px_rgba(34,211,238,0.6)]" />
        <span className="font-mono text-sm font-semibold tracking-wide text-ink">liveflights</span>
      </a>
      <div className="flex items-center gap-2 sm:gap-4">
        <a
          href="https://github.com/tyxgx/liveflights"
          target="_blank"
          rel="noreferrer"
          className="hidden text-[13px] text-ink-muted transition-colors hover:text-ink sm:inline"
        >
          GitHub
        </a>
        <a
          href="/live.html"
          className="rounded-md bg-accent-cyan/15 px-3.5 py-1.5 text-[13px] font-medium text-accent-cyan transition-colors hover:bg-accent-cyan/25"
        >
          Live Dashboard →
        </a>
      </div>
    </nav>
  );
}
