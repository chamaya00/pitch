"use client";

import { Button } from "@/components/ui/button";

interface AnalysisErrorProps {
  /** Already-presentable copy. Components never map error codes themselves. */
  message: string;
  onRetry: () => void;
}

/**
 * A terminal analysis failure.
 *
 * The message arrives already translated by `lib/analysis-errors.ts`, so no
 * provider name, URL, exception class or request id can reach the screen
 * through here. Retrying is always offered: the recording is untouched by a
 * failed analysis, so trying again is free.
 */
export function AnalysisError({ message, onRetry }: AnalysisErrorProps) {
  return (
    <div
      role="alert"
      className="fade-in rounded-xl border border-danger/40 bg-danger/5 p-5"
    >
      <p className="text-sm font-medium text-danger">
        We couldn&apos;t analyse this recording
      </p>
      <p className="mt-1 text-sm leading-relaxed text-muted">{message}</p>
      <p className="mt-3 text-xs text-muted">
        Your recording is still stored and unchanged.
      </p>
      <Button className="mt-4" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
