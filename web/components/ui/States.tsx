export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-white/[0.06] ${className}`} />;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-6 text-xs text-ink-muted">
      <p className="text-danger">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded border border-border-strong px-2.5 py-1 text-[11px] text-ink hover:border-accent-cyan/50 hover:text-accent-cyan"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <div className="px-4 py-6 text-xs text-ink-faint">{message}</div>;
}
