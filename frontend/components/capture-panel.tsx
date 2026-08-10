"use client";

import { useState } from "react";
import { AnalysisPanel } from "@/components/analysis/analysis-panel";
import { RecordPanel } from "@/components/record/record-panel";
import { FileDropzone } from "@/components/upload/file-dropzone";
import { SelectedFileCard } from "@/components/upload/selected-file-card";
import { UploadProgressCard } from "@/components/upload/upload-progress-card";
import { Button } from "@/components/ui/button";
import { useMicrophoneRecorder } from "@/hooks/use-microphone-recorder";
import { usePublicConfig } from "@/hooks/use-public-config";
import { useUpload, type UploadState } from "@/hooks/use-upload";
import { formatDurationLimit } from "@/lib/format";

type Source = "upload" | "record";

/**
 * Where a recording comes from, and everything that happens to it after.
 *
 * A file can arrive two ways — chosen from disk, or recorded here — and from
 * the moment it exists both paths are identical: the same upload, the same
 * analysis, the same results. Only the first step differs, so only the first
 * step is switched.
 *
 * Limits come from the API so the copy cannot drift from what the server
 * enforces; when they are unavailable the wording stays general rather than
 * asserting a number we cannot verify.
 */
export function CapturePanel() {
  const config = usePublicConfig();
  const { state, selectFile, clear, startUpload, uploadFile, cancelUpload } =
    useUpload(config);
  const [source, setSource] = useState<Source>("upload");
  const recorder = useMicrophoneRecorder(config?.max_audio_duration_seconds);
  /** Set when a switch would destroy a take and is waiting to be confirmed. */
  const [pendingSource, setPendingSource] = useState<Source | null>(null);

  // A recording in progress, or one finished and not yet uploaded, is the only
  // copy that exists — it lives in this tab and nowhere else.
  const hasUnsavedTake = recorder.state !== "idle" || recorder.recording !== null;

  const chooseSource = (next: Source) => {
    if (next === source) return;
    if (source === "record" && hasUnsavedTake) {
      setPendingSource(next);
      return;
    }
    setSource(next);
  };

  const discardAndSwitch = () => {
    recorder.discard();
    if (pendingSource) setSource(pendingSource);
    setPendingSource(null);
  };

  const formats = (config?.supported_extensions ?? [".mp3", ".wav"])
    .map((extension) => extension.replace(".", "").toUpperCase())
    .join(" or ");

  const hint = config
    ? `${formats} · up to ${config.max_audio_size_mb} MB · up to ${formatDurationLimit(
        config.max_audio_duration_seconds,
      )}`
    : `${formats} audio files`;

  const choosing =
    state.status === "idle" ||
    (state.status === "error" && state.errorKind === "validation");

  return (
    <section aria-labelledby="capture-heading" className="space-y-4">
      <h2 id="capture-heading" className="sr-only">
        Add a recording
      </h2>

      {/* One polite live region for the whole flow, so a screen reader hears
          state changes without the DOM churn of per-card regions. */}
      <p aria-live="polite" className="sr-only">
        {statusAnnouncement(state)}
      </p>

      {choosing && (
        <div
          role="group"
          aria-label="Choose how to add a recording"
          className="inline-flex rounded-lg border border-border bg-surface p-1"
        >
          {(
            [
              ["upload", "Upload audio"],
              ["record", "Record voice"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={source === value}
              onClick={() => chooseSource(value)}
              className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${
                source === value
                  ? "bg-accent text-accent-foreground"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {choosing && pendingSource !== null && (
        <div
          role="alert"
          className="rounded-xl border border-warning/40 bg-warning/5 p-5"
        >
          <p className="text-sm font-medium">Discard this recording?</p>
          <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
            {recorder.recording === null
              ? "You are still recording. Switching stops it and throws the audio away."
              : "This take only exists in your browser. It has not been uploaded, and switching will throw it away."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setPendingSource(null)}>
              Keep recording
            </Button>
            <Button onClick={discardAndSwitch}>Discard and switch</Button>
          </div>
        </div>
      )}

      {choosing && source === "upload" && (
        <FileDropzone
          onFileSelected={selectFile}
          supportedExtensions={config?.supported_extensions}
          hint={hint}
        />
      )}

      {choosing && source === "record" && (
        <RecordPanel
          recorder={recorder}
          onAnalyse={(file) => {
            uploadFile(file);
            // The take is now the upload's problem. Releasing it here stops a
            // stale recording reappearing if the person starts over.
            recorder.discard();
          }}
          maxDurationSeconds={config?.max_audio_duration_seconds}
        />
      )}

      {state.status === "selected" && (
        <SelectedFileCard
          file={state.file}
          onUpload={startUpload}
          onClear={clear}
        />
      )}

      {state.status === "uploading" && (
        <UploadProgressCard
          file={state.file}
          progress={state.progress}
          onCancel={cancelUpload}
        />
      )}

      {state.status === "success" && (
        <AnalysisPanel
          // A new recording gets a new panel, so no analysis state survives
          // from the previous one.
          key={state.recording.recording_id}
          recording={state.recording}
          onUploadAnother={clear}
        />
      )}

      {state.status === "error" && state.errorKind === "server" && (
        <div
          role="alert"
          className="fade-in rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">Upload failed</p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>

          {state.file && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={startUpload}>Try again</Button>
              <Button variant="secondary" onClick={clear}>
                Start over
              </Button>
            </div>
          )}
        </div>
      )}

      {state.status === "error" && state.errorKind === "validation" && (
        <div
          role="alert"
          className="fade-in rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">
            That file can&apos;t be uploaded
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
        </div>
      )}
    </section>
  );
}

function statusAnnouncement(state: UploadState): string {
  switch (state.status) {
    case "idle":
      return "No recording selected.";
    case "selected":
      return `${state.file.name} selected. Ready to upload.`;
    case "uploading":
      return state.progress === null
        ? "Uploading."
        : `Uploading, ${Math.round(state.progress * 100)} percent.`;
    case "success":
      return `Upload complete. ${state.recording.original_filename} is ready for analysis.`;
    case "error":
      return `Upload failed. ${state.message}`;
  }
}
