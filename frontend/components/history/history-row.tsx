"use client";

import { useState } from "react";
import { AnalysisPanel } from "@/components/analysis/analysis-panel";
import {
  audioLabel,
  formatWhen,
  speechLabel,
  type StatusLabel,
} from "@/lib/history";
import { formatDuration } from "@/lib/format";
import type { RecordingHistoryItem } from "@/types/api";

interface HistoryRowProps {
  item: RecordingHistoryItem;
  /** Whether this row is one of the two picked for comparison. */
  selected: boolean;
  /** `false` when two others are already picked, so this one cannot be added. */
  selectable: boolean;
  onToggleSelected: (recordingId: string) => void;
}

const TONE_CLASSES: Record<StatusLabel["tone"], string> = {
  // "Never run" is not a warning and must not look like one. It is the most
  // common state for a fresh recording, and colouring it red would tell people
  // something failed when nobody ever asked for it.
  absent: "border-border text-muted",
  running: "border-accent/40 text-accent",
  good: "border-positive/40 text-positive",
  bad: "border-danger/40 text-danger",
};

/**
 * One recording in the history, expandable into its full results.
 *
 * Expanding renders the same `AnalysisPanel` a fresh upload renders — the same
 * transcript, the same measurements, the same AI card, all fetched by
 * recording id. There is deliberately no second set of result components for
 * "old" recordings: two implementations of the same view is two places for the
 * caveats to drift apart.
 *
 * Collapsed by default, and nothing is requested until it is opened. A history
 * of fifty recordings is fifty ids, not fifty analyses.
 *
 * The comparison checkbox is a real ``<input type="checkbox">`` rather than a
 * styled button: it gets keyboard operation, the checked state and the
 * disabled semantics from the platform, and a screen reader announces all three
 * without any ARIA of ours. It sits outside the expand button because a control
 * inside a button is not operable.
 */
export function HistoryRow({
  item,
  selected,
  selectable,
  onToggleSelected,
}: HistoryRowProps) {
  const [open, setOpen] = useState(false);
  const { recording } = item;
  const speech = speechLabel(item.speech_status);
  const audio = audioLabel(item.audio_status);
  const checkboxId = `compare-${recording.recording_id}`;

  return (
    <li className="rounded-xl border border-border bg-surface">
      <div className="flex items-start gap-3 p-5">
        <input
          id={checkboxId}
          type="checkbox"
          checked={selected}
          disabled={!selected && !selectable}
          onChange={() => onToggleSelected(recording.recording_id)}
          className="mt-1 h-4 w-4 shrink-0 accent-[var(--accent)] disabled:opacity-40"
        />
        <label htmlFor={checkboxId} className="sr-only">
          Compare {recording.original_filename}
        </label>

        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-3 text-left"
        >
          <span className="min-w-0">
            <span className="block truncate font-medium">
              {recording.original_filename}
            </span>
            <span className="mt-1 block text-xs text-muted">
              {formatWhen(recording.created_at)} ·{" "}
              {formatDuration(recording.duration_seconds)} ·{" "}
              {recording.format.toUpperCase()}
            </span>
          </span>

          <span className="flex shrink-0 flex-wrap items-center gap-2">
            <Chip label={speech} prefix="Speech" />
            <Chip label={audio} prefix="Audio" />
            <span aria-hidden className="text-muted">
              {open ? "−" : "+"}
            </span>
          </span>
        </button>
      </div>

      {open && (
        <div className="fade-in border-t border-border p-5">
          <AnalysisPanel
            recording={recording}
            // Nothing to clear: this recording was not just uploaded, so
            // "upload another" would be a button that undid nothing.
            onUploadAnother={() => setOpen(false)}
          />
        </div>
      )}
    </li>
  );
}

function Chip({ label, prefix }: { label: StatusLabel; prefix: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-1 text-xs ${TONE_CLASSES[label.tone]}`}
    >
      <span className="sr-only">{prefix}: </span>
      {label.text}
    </span>
  );
}
