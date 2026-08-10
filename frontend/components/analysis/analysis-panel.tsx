"use client";

import { AnalysisError } from "@/components/analysis/analysis-error";
import { AnalysisProgress } from "@/components/analysis/analysis-progress";
import { AnalysisResult } from "@/components/analysis/analysis-result";
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
 * mirroring how `UploadPanel` works — including the single polite live region,
 * so a screen reader hears each stage change once rather than on every poll.
 *
 * The action lives on the recording card because that is where the user
 * already is when the upload finishes; this component only supplies the
 * behaviour behind it.
 */
export function AnalysisPanel({
  recording,
  onUploadAnother,
}: AnalysisPanelProps) {
  const { state, start } = useAnalysis(recording.recording_id);

  return (
    <section aria-labelledby="analysis-heading" className="space-y-4">
      <h2 id="analysis-heading" className="sr-only">
        Recording and speech analysis
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
