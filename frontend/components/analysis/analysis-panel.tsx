"use client";

import { AnalysisError } from "@/components/analysis/analysis-error";
import { AnalysisProgress } from "@/components/analysis/analysis-progress";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import { AudioAnalysisPanel } from "@/components/audio-analysis/audio-analysis-panel";
import { RecordingSummaryCard } from "@/components/upload/recording-summary-card";
import { useAnalysis } from "@/hooks/use-analysis";
import { stageMessage, type AnalysisRunState } from "@/lib/analysis-runner";
import type { Recording } from "@/types/api";

interface AnalysisPanelProps {
  recording: Recording;
  onUploadAnother: () => void;
}

/**
 * Everything that happens after a successful upload.
 *
 * Owns the analysis state machine and renders exactly one state at a time,
 * mirroring how `CapturePanel` works — including the single polite live region,
 * so a screen reader hears each stage change once rather than on every poll.
 *
 * The action lives on the recording card because that is where the user
 * already is when the upload finishes; this component only supplies the
 * behaviour behind it.
 *
 * A recording has two independent analyses and this panel shows both, one under
 * the other, each with its own heading and its own start button. Speech
 * analysis transcribes and counts; audio analysis measures the signal. They are
 * never merged, never averaged, and never presented as two halves of a score —
 * see `docs/architecture.md`.
 */
export function AnalysisPanel({
  recording,
  onUploadAnother,
}: AnalysisPanelProps) {
  const { state, start } = useAnalysis(recording.recording_id);

  return (
    <section aria-labelledby="analysis-heading" className="space-y-4">
      <h2 id="analysis-heading" className="sr-only">
        Recording analysis
      </h2>

      <p aria-live="polite" className="sr-only">
        {announcement(state)}
      </p>

      <RecordingSummaryCard
        recording={recording}
        onUploadAnother={onUploadAnother}
        onAnalyse={start}
        analysisStarted={state.status !== "idle"}
      />

      {state.status === "starting" && <AnalysisProgress status={null} />}

      {state.status === "running" && (
        <AnalysisProgress status={state.analysis.status} />
      )}

      {state.status === "completed" && (
        <AnalysisResult analysis={state.analysis} />
      )}

      {(state.status === "failed" || state.status === "error") && (
        <AnalysisError message={state.message} onRetry={start} />
      )}

      <AudioAnalysisPanel recordingId={recording.recording_id} />
    </section>
  );
}

function announcement(state: AnalysisRunState): string {
  switch (state.status) {
    case "idle":
      return "Recording ready. You can analyse it.";
    case "starting":
      return stageMessage("pending");
    case "running":
      return stageMessage(state.analysis.status);
    case "completed":
      return state.analysis.provenance.is_mock
        ? "Analysis complete. Showing demo data, not real analysis."
        : "Analysis complete. Results are below.";
    case "failed":
    case "error":
      return `Analysis failed. ${state.message}`;
  }
}
