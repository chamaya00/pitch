"use client";

import { noteRows } from "@/lib/audio-analysis-metrics";
import type { NoteBreakdown as NoteBreakdownData } from "@/types/api";

interface NoteBreakdownProps {
  breakdown: NoteBreakdownData | null;
  error: string | null;
}

/**
 * Where the pitched time went, note by note.
 *
 * A real `<table>`, because that is what this is: rows of a shared shape with
 * column headers, navigable by a screen reader as a table rather than as a
 * heap of divs. The bar behind each row is a background gradient and therefore
 * invisible to assistive technology by construction — every number it encodes
 * is in a cell of the same row, so the table is complete without it.
 *
 * The aggregation happens on the server. The timeline behind this runs to
 * thousands of points; downloading them to build a table of a few rows would be
 * a megabyte spent on arithmetic that was already done, and doing it in a
 * component would redo it on every render.
 *
 * An empty breakdown is not an empty table. "No notes were detected" is a
 * statement about the recording, and it is rendered as one.
 */
export function NoteBreakdown({ breakdown, error }: NoteBreakdownProps) {
  return (
    <section
      aria-labelledby="note-breakdown-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <div className="border-b border-border px-5 py-4">
        <h4 id="note-breakdown-heading" className="text-sm font-medium">
          Note breakdown
        </h4>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          How the recording&apos;s pitched time was divided between notes.
          Percentages are shares of the time that carried a pitch — silence and
          unvoiced audio are excluded, so they add up to 100%.
        </p>
      </div>

      {breakdown === null ? (
        <p className="px-5 py-4 text-sm text-muted">
          {error ?? "Loading the note breakdown…"}
        </p>
      ) : breakdown.notes.length === 0 ? (
        <p className="px-5 py-4 max-w-prose text-sm leading-relaxed text-muted">
          Not enough reliable pitch information was detected to break this
          recording into musical notes.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Notes detected in this recording, longest first. Each row gives
                the note, how long it was held, its share of the pitched time,
                the share of its frames within {breakdown.in_tune_cents} cents of
                the note, and its average deviation from the note.
              </caption>
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th scope="col" className="px-5 py-2 font-normal">
                    Note
                  </th>
                  <th scope="col" className="px-3 py-2 font-normal">
                    Time
                  </th>
                  <th scope="col" className="px-3 py-2 font-normal">
                    Share
                  </th>
                  <th scope="col" className="px-3 py-2 font-normal">
                    In tune
                  </th>
                  <th scope="col" className="px-5 py-2 font-normal">
                    Deviation
                  </th>
                </tr>
              </thead>
              <tbody>
                {noteRows(breakdown.notes).map((row) => (
                  <tr
                    key={row.midi}
                    className="border-b border-border/60"
                    // The bar is a row background rather than a positioned
                    // element: absolute positioning inside a table cell anchors
                    // to the cell, which would size the bar to the Note column
                    // instead of the row. Decoration only — every number it
                    // encodes is in the cells beside it.
                    style={{
                      backgroundImage:
                        `linear-gradient(to right, ` +
                        `color-mix(in srgb, var(--accent) 14%, transparent) ${row.sharePercent}%, ` +
                        `transparent ${row.sharePercent}%)`,
                    }}
                  >
                    <th
                      scope="row"
                      className="px-5 py-2.5 text-left font-mono font-medium tabular-nums"
                    >
                      {row.note}
                    </th>
                    <td className="px-3 py-2.5 font-mono tabular-nums">{row.duration}</td>
                    <td className="px-3 py-2.5 font-mono tabular-nums">{row.share}</td>
                    <td className="px-3 py-2.5 font-mono tabular-nums">{row.inTune}</td>
                    <td className="px-5 py-2.5 font-mono tabular-nums text-muted">
                      {row.deviation}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="px-5 py-4 text-xs leading-relaxed text-muted">
            <strong className="font-medium">In tune</strong> is the share of that
            note&apos;s frames that sat within {breakdown.in_tune_cents} cents of
            it — a measurement against a stated threshold, not a mark. A note
            held only briefly still appears; a small share means exactly that,
            and a slide between two notes leaves a trace on the notes it passed
            through.
          </p>
        </>
      )}
    </section>
  );
}
