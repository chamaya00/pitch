"use client";

import { targetOptions } from "@/lib/live-practice";

interface TargetNotePickerProps {
  value: number | null;
  onChange: (midi: number | null) => void;
}

/**
 * Pick a note to practise against, or none.
 *
 * A plain `<select>`: it is keyboard-operable, screen-reader-labelled and
 * mobile-native for free, and a bespoke listbox would be a lot of ARIA to
 * reimplement worse.
 *
 * Choosing a target changes what the *deviation* is measured from. It does not
 * touch the detector, and it never makes a wrong note read as the right one —
 * see `compareToTarget`.
 */
export function TargetNotePicker({ value, onChange }: TargetNotePickerProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="target-note" className="text-xs text-muted">
        Target note
      </label>
      <select
        id="target-note"
        value={value ?? ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? null : Number(event.target.value))
        }
        className="rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-sm"
      >
        <option value="">None — nearest note</option>
        {targetOptions().map((option) => (
          <option key={option.midi} value={option.midi}>
            {option.note}
          </option>
        ))}
      </select>
    </div>
  );
}
