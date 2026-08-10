"use client";

import { Button } from "@/components/ui/button";
import {
  formatBytes,
  formatChannels,
  formatDuration,
  formatSampleRate,
} from "@/lib/format";
import type { Recording } from "@/types/api";

interface RecordingSummaryCardProps {
  recording: Recording;
  onUploadAnother: () => void;
  /** Starts the speech analysis. Omitted when analysis is unavailable. */
  onAnalyse?: () => void;
  /** Hides the action once an analysis is under way or finished. */
  analysisStarted?: boolean;
}

/**
 * What the server actually measured about the file, and nothing else.
 *
 * Every value here comes from the upload response — container facts, not
 * speech. Nothing about how the person spoke appears until the analysis has
 * actually run.
 */
export function RecordingSummaryCard({
  recording,
  onUploadAnother,
  onAnalyse,
  analysisStarted = false,
}: RecordingSummaryCardProps) {
  const facts: { label: string; value: string }[] = [
    { label: "Duration", value: formatDuration(recording.duration_seconds) },
    { label: "Sample rate", value: formatSampleRate(recording.sample_rate) },
    { label: "Channels", value: formatChannels(recording.channels) },
    { label: "Format", value: recording.format.toUpperCase() },
    { label: "Size", value: formatBytes(recording.size_bytes) },
    ...(recording.bits_per_sample
      ? [{ label: "Bit depth", value: `${recording.bits_per_sample}-bit` }]
      : []),
  ];

  return (
    <div className="fade-in rounded-xl border border-border bg-surface">
      <div className="flex items-start gap-3 border-b border-border p-5">
        <span
          aria-hidden
          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-positive/15 text-positive"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m5 13 4 4L19 7" />
          </svg>
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium">Ready for analysis</p>
          <p className="mt-1 break-all text-xs text-muted">
            {recording.original_filename}
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-px overflow-hidden bg-border sm:grid-cols-3">
        {facts.map((fact) => (
          <div key={fact.label} className="bg-surface px-5 py-4">
            <dt className="text-xs text-muted">{fact.label}</dt>
            <dd className="mt-1 font-mono text-sm tabular-nums">
              {fact.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border p-5">
        {onAnalyse && !analysisStarted ? (
          <>
            <p className="text-xs text-muted">
              Transcribe this recording and measure how you spoke.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={onAnalyse}>Analyse this recording</Button>
              <Button variant="secondary" onClick={onUploadAnother}>
                Upload another
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted">
              This recording is stored and ready.
            </p>
            <Button variant="secondary" onClick={onUploadAnother}>
              Upload another
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
