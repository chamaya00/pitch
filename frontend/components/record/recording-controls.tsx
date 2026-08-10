"use client";

import { Button } from "@/components/ui/button";
import { formatDuration } from "@/lib/format";
import type { RecorderState } from "@/lib/live-pitch-engine";

interface RecordingControlsProps {
  state: RecorderState;
  elapsedSeconds: number;
  maxDurationSeconds?: number;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
}

/**
 * Start, pause, resume, stop — and an honest clock.
 *
 * The elapsed time is counted from samples actually captured, not from a
 * wall-clock timer, so a pause does not quietly advance it and the number
 * always matches the length of the file you end up with.
 */
export function RecordingControls({
  state,
  elapsedSeconds,
  maxDurationSeconds,
  onStart,
  onPause,
  onResume,
  onStop,
}: RecordingControlsProps) {
  const live = state === "recording" || state === "paused";

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className={`h-2.5 w-2.5 rounded-full ${
            state === "recording"
              ? "bg-danger motion-safe:animate-pulse"
              : state === "paused"
                ? "bg-warning"
                : "bg-border"
          }`}
        />
        <p className="font-mono text-lg tabular-nums">
          {formatDuration(elapsedSeconds)}
        </p>
        {maxDurationSeconds !== undefined && live && (
          <p className="text-xs text-muted">
            of {formatDuration(maxDurationSeconds)}
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {state === "idle" && <Button onClick={onStart}>Start recording</Button>}

        {state === "requesting" && (
          <Button disabled>Waiting for microphone…</Button>
        )}

        {state === "recording" && (
          <>
            <Button variant="secondary" onClick={onPause}>
              Pause
            </Button>
            <Button onClick={onStop}>Stop</Button>
          </>
        )}

        {state === "paused" && (
          <>
            <Button variant="secondary" onClick={onResume}>
              Resume
            </Button>
            <Button onClick={onStop}>Stop</Button>
          </>
        )}
      </div>
    </div>
  );
}
