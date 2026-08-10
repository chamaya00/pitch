"use client";

import { AnalysisProgress } from "@/components/analysis/analysis-progress";
import { AudioAnalysisResult } from "@/components/audio-analysis/audio-analysis-result";
import { Button } from "@/components/ui/button";
import { useAudioAnalysis } from "@/hooks/use-audio-analysis";
import { audioStageMessage, type AudioRunState } from "@/lib/analysis-runner";
import { isNotMeasurable } from "@/lib/audio-analysis-errors";

interface AudioAnalysisPanelProps {
  recordingId: string;
}

/**
 * The deterministic audio analysis of a stored recording.
 *
 * Kept as its own section, next to the speech analysis and never merged with
 * it. The two measure different things — one counts words, the other measures
 * the signal — and a reader who sees them under one heading will average them
 * into an impression that neither supports.
 *
 * It is also not the live estimate shown while recording. That runs a different
 * algorithm over a shorter window in the browser; this runs over the saved
 * file. They will not agree exactly, and the copy says so rather than hoping
 * nobody notices.
 */
export function AudioAnalysisPanel({ recordingId }: AudioAnalysisPanelProps) {
  const { state, start, timeline, timelineError } = useAudioAnalysis(recordingId);

  const notMeasurable =
    state.status === "failed" && isNotMeasurable(state.analysis.error_code);

  return (
    <section aria-labelledby="audio-analysis-heading" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3 border-t border-border pt-6">
        <div>
          <h3 id="audio-analysis-heading" className="text-sm font-medium">
            Audio analysis
          </h3>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            Pitch, detected range, pitch stability, loudness and spectral
            characteristics — measured from the saved audio. Separate from the
            speech analysis above, and not the same measurement as the live
            estimate shown while recording.
          </p>
        </div>
        {state.status === "idle" && (
          <Button variant="secondary" onClick={start}>
            Measure the audio
          </Button>
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement(state)}
      </p>

      {state.status === "starting" && <AnalysisProgress status={null} />}

      {state.status === "running" && (
        <p className="rounded-xl border border-border bg-surface px-5 py-4 text-sm text-muted">
          {audioStageMessage(state.analysis.status)}
        </p>
      )}

      {state.status === "completed" && state.analysis.summary && (
        <AudioAnalysisResult
          summary={state.analysis.summary}
          timeline={timeline}
          timelineError={timelineError}
        />
      )}

      {notMeasurable && (
        <div className="rounded-xl border border-border bg-surface p-5">
          <p className="text-sm font-medium">Not measured</p>
          <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
            {state.status === "failed" ? state.message : ""}
          </p>
          <div className="mt-4">
            <Button variant="secondary" onClick={start}>
              Try again
            </Button>
          </div>
        </div>
      )}

      {(state.status === "error" || (state.status === "failed" && !notMeasurable)) && (
        <div
          role="alert"
          className="rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">
            Could not measure this recording
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
          <div className="mt-4">
            <Button variant="secondary" onClick={start}>
              Try again
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function announcement(state: AudioRunState): string {
  switch (state.status) {
    case "idle":
      return "Audio analysis has not been run.";
    case "starting":
      return audioStageMessage("pending");
    case "running":
      return audioStageMessage(state.analysis.status);
    case "completed":
      return "Audio analysis complete. Measurements are below.";
    case "failed":
    case "error":
      return state.message;
  }
}
