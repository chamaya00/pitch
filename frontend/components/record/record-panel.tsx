"use client";

import { LivePitchDisplay } from "@/components/record/live-pitch-display";
import { LiveSummaryCard } from "@/components/record/live-summary-card";
import { MicrophoneError } from "@/components/record/microphone-error";
import { PitchTrace } from "@/components/record/pitch-trace";
import { RecordingControls } from "@/components/record/recording-controls";
import { Button } from "@/components/ui/button";
import { useMicrophoneRecorder } from "@/hooks/use-microphone-recorder";
import { formatBytes, formatDuration } from "@/lib/format";
import { WAV_MIME_TYPE } from "@/lib/wav";

interface RecordPanelProps {
  /** Hand the finished recording to the upload flow. */
  onAnalyse: (file: File) => void;
  maxDurationSeconds?: number;
}

/**
 * Record from the microphone, watch the pitch live, then decide what happens.
 *
 * Two things stay separate here on purpose.
 *
 * **The live readout is local.** Audio goes from the microphone into an audio
 * graph in this tab and nowhere else. No speech-to-text provider and no model
 * sees it; there is no request of any kind while recording.
 *
 * **Uploading is a decision, not a consequence.** The finished WAV sits in the
 * page until the person presses the button. Recording and analysing are two
 * separate acts, and nothing is sent because a recording happened to end.
 */
export function RecordPanel({ onAnalyse, maxDurationSeconds }: RecordPanelProps) {
  const recorder = useMicrophoneRecorder(maxDurationSeconds);
  const {
    state,
    errorCode,
    elapsedSeconds,
    recording,
    stoppedAtLimit,
    subscribe,
  } = recorder;

  const live = state === "recording" || state === "paused" || state === "requesting";

  return (
    <div className="space-y-4">
      <p aria-live="polite" className="sr-only">
        {announcement(state, recording !== null)}
      </p>

      {errorCode !== null && (
        <MicrophoneError code={errorCode} onRetry={recorder.start} />
      )}

      {state === "idle" && recording === null && errorCode === null && (
        <div className="rounded-xl border border-border bg-surface p-6">
          <h3 className="text-sm font-medium">Record with your microphone</h3>
          <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted">
            See the pitch of your voice as you speak or sing. The detection runs
            entirely in this browser tab — nothing is uploaded, transcribed or
            sent to any model while you record. When you stop, you can listen
            back and then choose whether to upload it for analysis.
          </p>
          <div className="mt-5">
            <Button onClick={recorder.start}>Start recording</Button>
          </div>
        </div>
      )}

      {live && (
        <>
          <LivePitchDisplay
            subscribe={subscribe}
            active={state === "recording"}
          />
          <PitchTrace subscribe={subscribe} active={state === "recording"} />
        </>
      )}

      {live && (
        <RecordingControls
          state={state}
          elapsedSeconds={elapsedSeconds}
          maxDurationSeconds={maxDurationSeconds}
          onStart={recorder.start}
          onPause={recorder.pause}
          onResume={recorder.resume}
          onStop={recorder.stop}
        />
      )}

      {state === "stopped" && recording === null && (
        <div className="rounded-xl border border-border bg-surface p-5">
          <p className="text-sm font-medium">Nothing was recorded</p>
          <p className="mt-1 text-sm text-muted">
            The recording stopped before any audio was captured. Try again.
          </p>
          <div className="mt-4">
            <Button onClick={recorder.start}>Start recording</Button>
          </div>
        </div>
      )}

      {recording !== null && (
        <>
          {stoppedAtLimit && maxDurationSeconds !== undefined && (
            <p className="rounded-xl border border-warning/40 bg-warning/5 px-5 py-3 text-sm text-muted">
              Recording stopped at the{" "}
              {formatDuration(maxDurationSeconds)} limit.
            </p>
          )}

          <div className="fade-in rounded-xl border border-border bg-surface">
            <div className="border-b border-border p-5">
              <p className="text-sm font-medium">Your recording</p>
              <p className="mt-1 font-mono text-xs text-muted">
                {formatDuration(recording.durationSeconds)} ·{" "}
                {formatBytes(recording.sizeBytes)} · WAV
              </p>
              <audio
                controls
                className="mt-4 w-full"
                src={recording.playbackUrl}
              />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 p-5">
              <p className="text-xs text-muted">
                Uploading sends this recording to VocalLens. Nothing has left
                your browser yet.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() =>
                    onAnalyse(
                      new File([recording.blob], recording.filename, {
                        type: WAV_MIME_TYPE,
                      }),
                    )
                  }
                >
                  Upload for analysis
                </Button>
                <Button variant="secondary" onClick={recorder.discard}>
                  Record again
                </Button>
              </div>
            </div>
          </div>

          <LiveSummaryCard summary={recording.summary} />
        </>
      )}
    </div>
  );
}

function announcement(state: string, hasRecording: boolean): string {
  switch (state) {
    case "requesting":
      return "Waiting for microphone permission.";
    case "recording":
      return "Recording.";
    case "paused":
      return "Recording paused.";
    case "stopped":
      return hasRecording
        ? "Recording finished. Listen back, or analyse it."
        : "Recording stopped. Nothing was captured.";
    default:
      return "Not recording.";
  }
}
