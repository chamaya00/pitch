"use client";

import { AnalysisPanel } from "@/components/analysis/analysis-panel";
import { FileDropzone } from "@/components/upload/file-dropzone";
import { SelectedFileCard } from "@/components/upload/selected-file-card";
import { UploadProgressCard } from "@/components/upload/upload-progress-card";
import { Button } from "@/components/ui/button";
import { usePublicConfig } from "@/hooks/use-public-config";
import { useUpload } from "@/hooks/use-upload";
import { formatDurationLimit } from "@/lib/format";

/**
 * Owns the upload state machine and renders exactly one state at a time.
 *
 * Limits come from the API so the copy cannot drift from what the server
 * enforces; when they are unavailable the wording stays general rather than
 * asserting a number we cannot verify.
 */
export function UploadPanel() {
  const config = usePublicConfig();
  const { state, selectFile, clear, startUpload, cancelUpload } =
    useUpload(config);

  const formats = (config?.supported_extensions ?? [".mp3", ".wav"])
    .map((extension) => extension.replace(".", "").toUpperCase())
    .join(" or ");

  const hint = config
    ? `${formats} · up to ${config.max_audio_size_mb} MB · up to ${formatDurationLimit(
        config.max_audio_duration_seconds,
      )}`
    : `${formats} audio files`;

  return (
    <section aria-labelledby="upload-heading" className="space-y-4">
      <h2 id="upload-heading" className="sr-only">
        Upload a recording
      </h2>

      {/* One polite live region for the whole flow, so a screen reader hears
          state changes without the DOM churn of per-card regions. */}
      <p aria-live="polite" className="sr-only">
        {statusAnnouncement(state)}
      </p>

      {(state.status === "idle" ||
        (state.status === "error" && state.errorKind === "validation")) && (
        <FileDropzone
          onFileSelected={selectFile}
          supportedExtensions={config?.supported_extensions}
          hint={hint}
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

      {state.status === "error" && (
        <div
          role="alert"
          className="fade-in rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">
            {state.errorKind === "validation"
              ? "That file can't be uploaded"
              : "Upload failed"}
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>

          {state.file && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={startUpload}>Try again</Button>
              <Button variant="secondary" onClick={clear}>
                Choose another file
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function statusAnnouncement(
  state: ReturnType<typeof useUpload>["state"],
): string {
  switch (state.status) {
    case "idle":
      return "No file selected.";
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
