"use client";

import { useCallback, useId, useRef, useState } from "react";
import { acceptAttribute } from "@/lib/audio-validation";

interface FileDropzoneProps {
  onFileSelected: (file: File) => void;
  supportedExtensions?: readonly string[];
  hint: string;
  disabled?: boolean;
}

/**
 * The primary upload affordance.
 *
 * Built around a real `<label>` + `<input type="file">` so the browser's own
 * keyboard and screen-reader behaviour is preserved. The input is visually
 * hidden but remains focusable and in the tab order — never `display: none`,
 * which would take it out of both. Drag and drop is layered on top as an
 * enhancement; every path it offers is reachable without a pointer.
 */
export function FileDropzone({
  onFileSelected,
  supportedExtensions,
  hint,
  disabled = false,
}: FileDropzoneProps) {
  const inputId = useId();
  const hintId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepth = useRef(0);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (file) onFileSelected(file);
    },
    [onFileSelected],
  );

  const onDragEnter = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      if (disabled) return;
      // Nested children fire enter/leave too; depth-counting avoids flicker.
      dragDepth.current += 1;
      setIsDragOver(true);
    },
    [disabled],
  );

  const onDragLeave = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragOver(false);
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      dragDepth.current = 0;
      setIsDragOver(false);
      if (disabled) return;
      handleFiles(event.dataTransfer?.files ?? null);
    },
    [disabled, handleFiles],
  );

  return (
    <div
      onDragEnter={onDragEnter}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      data-drag-over={isDragOver || undefined}
      className={[
        "group relative rounded-xl border border-dashed transition-colors duration-200 motion-reduce:transition-none",
        // The input is visually hidden but still focusable, so the focus ring
        // has to be rendered by the container it lives in.
        "focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/40",
        isDragOver
          ? "border-accent bg-accent/5"
          : "border-border bg-surface hover:border-muted",
        disabled ? "pointer-events-none opacity-60" : "",
      ].join(" ")}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={acceptAttribute(supportedExtensions)}
        disabled={disabled}
        aria-describedby={hintId}
        onChange={(event) => {
          handleFiles(event.target.files);
          // Reset so choosing the same file twice still fires a change event.
          event.target.value = "";
        }}
        className="sr-only"
      />
      <label
        htmlFor={inputId}
        className="flex cursor-pointer flex-col items-center gap-3 px-6 py-12 text-center sm:py-16"
      >
        <span
          aria-hidden
          className={[
            "flex h-11 w-11 items-center justify-center rounded-full border transition-transform duration-200 motion-reduce:transition-none",
            isDragOver
              ? "border-accent text-accent scale-110"
              : "border-border text-muted group-hover:text-foreground",
          ].join(" ")}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 16V4" />
            <path d="m6 10 6-6 6 6" />
            <path d="M4 20h16" />
          </svg>
        </span>

        <span className="text-base font-medium">
          {isDragOver ? "Drop to select" : "Drop an audio file here"}
        </span>
        <span className="text-sm text-muted">
          or{" "}
          <span className="text-foreground underline underline-offset-4">
            browse your files
          </span>
        </span>
      </label>

      <p id={hintId} className="px-6 pb-6 text-center text-xs text-muted">
        {hint}
      </p>
    </div>
  );
}
