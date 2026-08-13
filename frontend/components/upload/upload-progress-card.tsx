"use client";

import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/format";

interface UploadProgressCardProps {
  file: File;
  /** Fraction 0–1, or `null` when the browser cannot report real progress. */
  progress: number | null;
  onCancel: () => void;
}

/**
 * Live upload state.
 *
 * The bar reflects bytes actually sent. When the browser cannot report
 * progress, it falls back to an indeterminate animation rather than inventing
 * a percentage — a fake number that stalls at 99% is worse than no number.
 */
export function UploadProgressCard({
  file,
  progress,
  onCancel,
}: UploadProgressCardProps) {
  const determinate = progress !== null;
  const percent = determinate ? Math.round(progress * 100) : null;

  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-baseline justify-between gap-4">
        <p className="min-w-0 break-all text-sm font-medium">{file.name}</p>
        <p className="shrink-0 font-mono text-xs text-muted tabular-nums">
          {determinate ? `${percent}%` : "Uploading…"}
        </p>
      </div>

      <div
        role="progressbar"
        aria-label={`Uploading ${file.name}`}
        aria-valuemin={determinate ? 0 : undefined}
        aria-valuemax={determinate ? 100 : undefined}
        aria-valuenow={determinate ? (percent ?? undefined) : undefined}
        aria-valuetext={determinate ? `${percent}%` : "Uploading"}
        className="relative mt-4 h-1.5 overflow-hidden rounded-full bg-surface-raised"
      >
        {determinate ? (
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-200 ease-out motion-reduce:transition-none"
            style={{ width: `${percent}%` }}
          />
        ) : (
          <div className="indeterminate-bar h-full w-1/3 rounded-full bg-accent" />
        )}
      </div>

      <div className="mt-4 flex items-center justify-between gap-4">
        <p className="text-xs text-muted">{formatBytes(file.size)}</p>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
