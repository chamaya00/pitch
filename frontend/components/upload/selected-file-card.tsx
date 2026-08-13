"use client";

import { Button } from "@/components/ui/button";
import { extensionOf } from "@/lib/audio-validation";
import { formatBytes } from "@/lib/format";

interface SelectedFileCardProps {
  file: File;
  onUpload: () => void;
  onClear: () => void;
}

/**
 * Confirmation step between choosing a file and sending it.
 *
 * Nothing uploads on selection: the user sees what they picked and decides.
 */
export function SelectedFileCard({
  file,
  onUpload,
  onClear,
}: SelectedFileCardProps) {
  const extension = extensionOf(file.name).replace(".", "").toUpperCase();

  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start gap-4">
        <span
          aria-hidden
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border font-mono text-[10px] tracking-wider text-muted"
        >
          {extension || "?"}
        </span>

        <div className="min-w-0 flex-1">
          {/* break-all keeps a long name inside the card on narrow screens. */}
          <p className="break-all text-sm font-medium">{file.name}</p>
          <p className="mt-1 text-xs text-muted">
            {formatBytes(file.size)}
            {extension ? ` · ${extension}` : ""}
          </p>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        <Button onClick={onUpload}>Upload for analysis</Button>
        <Button variant="secondary" onClick={onClear}>
          Choose another
        </Button>
      </div>
    </div>
  );
}
