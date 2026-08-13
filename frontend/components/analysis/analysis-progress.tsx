"use client";

import { stageMessage } from "@/lib/analysis-runner";
import type { AnalysisStatus } from "@/types/api";

interface AnalysisProgressProps {
  /** `null` while the request that creates the analysis is still in flight. */
  status: AnalysisStatus | null;
}

const STAGES: { key: AnalysisStatus; label: string }[] = [
  { key: "pending", label: "Queued" },
  { key: "transcribing", label: "Transcribing" },
  { key: "analyzing", label: "Feedback" },
];

/**
 * Indeterminate by design.
 *
 * The server reports which stage it is on, never how far through it is, so
 * there is no honest percentage to show. The bar animates without claiming a
 * value, and the stage list shows what has been reached — real information
 * instead of an invented number that would sit at 90% for a minute.
 */
export function AnalysisProgress({ status }: AnalysisProgressProps) {
  const active = status ?? "pending";
  const reached = STAGES.findIndex((stage) => stage.key === active);

  return (
    <div className="fade-in rounded-xl border border-border bg-surface p-5">
      <p className="text-sm font-medium">{stageMessage(active)}</p>

      <div
        role="progressbar"
        aria-label="Analysing your recording"
        // No `aria-valuenow`: an indeterminate progressbar omits it, which is
        // how assistive technology is told the work has no measurable extent.
        aria-valuetext={stageMessage(active)}
        className="relative mt-4 h-1.5 overflow-hidden rounded-full bg-surface-raised"
      >
        <div className="indeterminate-bar h-full w-1/3 rounded-full bg-accent" />
      </div>

      <ol className="mt-4 flex flex-wrap gap-x-5 gap-y-2">
        {STAGES.map((stage, index) => {
          const done = reached > index;
          const current = reached === index;
          return (
            <li
              key={stage.key}
              className={`flex items-center gap-2 text-xs ${
                current ? "text-foreground" : "text-muted"
              }`}
            >
              <span
                aria-hidden
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  done || current ? "bg-accent" : "bg-border"
                }`}
              />
              {stage.label}
              {done && <span className="sr-only">, done</span>}
              {current && <span className="sr-only">, in progress</span>}
            </li>
          );
        })}
      </ol>

      <p className="mt-4 text-xs leading-relaxed text-muted">
        This runs in the background — the page keeps checking for you.
      </p>
    </div>
  );
}
