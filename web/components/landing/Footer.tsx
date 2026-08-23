"use client";

export function Footer() {
  return (
    <footer className="border-t border-border px-6 py-12 sm:px-10">
      <div className="mx-auto flex max-w-5xl flex-col items-start justify-between gap-8 sm:flex-row sm:items-center">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent-cyan" />
            <span className="font-mono text-sm font-semibold text-ink">liveflights</span>
          </div>
          <p className="max-w-sm text-[12px] leading-relaxed text-ink-faint">
            A live AWS data pipeline and dashboard, built and engineered by{" "}
            <a
              href="https://github.com/tyxgx"
              target="_blank"
              rel="noreferrer"
              className="text-ink-muted underline decoration-border underline-offset-2 hover:text-accent-cyan"
            >
              Uttkarsh Tyagi
            </a>
            .
          </p>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-[13px] text-ink-muted">
          <a href="/live.html" className="hover:text-ink">Live dashboard</a>
          <a href="https://github.com/tyxgx/liveflights" target="_blank" rel="noreferrer" className="hover:text-ink">
            GitHub repo
          </a>
          <a
            href="https://github.com/tyxgx/liveflights/tree/main/docs"
            target="_blank"
            rel="noreferrer"
            className="hover:text-ink"
          >
            Docs
          </a>
          <a href="https://tyxgx.github.io/portfolio" target="_blank" rel="noreferrer" className="hover:text-ink">
            Portfolio
          </a>
        </div>
      </div>
    </footer>
  );
}
