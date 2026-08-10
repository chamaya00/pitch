"use client";

import { formatCents } from "@/lib/pitch";
import type { LiveSummary } from "@/lib/pitch-stream";

const NOT_MEASURED = "Not measured";

interface LiveSummaryCardProps {
  summary: LiveSummary;
}

/**
 * What the browser measured while you were recording.
 *
 * Labelled a *live recording estimate* everywhere, and deliberately kept apart
 * from anything the analysis produces. These numbers come from a lightweight
 * detector running on a live stream in a browser; the backend measures
 * different things by different methods. Presenting the two as comparable
 * would be a claim neither one supports.
 *
 * A note that was never confidently voiced reads "Not measured". It never
 * reads zero — the same rule the analysis metrics follow.
 */
export function LiveSummaryCard({ summary }: LiveSummaryCardProps) {
  const rows: { label: string; value: string; measured: boolean }[] = [
    {
      label: "Lowest note",
      value: summary.lowestNote ?? NOT_MEASURED,
      measured: summary.lowestNote !== null,
    },
    {
      label: "Highest note",
      value: summary.highestNote ?? NOT_MEASURED,
      measured: summary.highestNote !== null,
    },
    {
      label: "Most-held note",
      value: summary.mostFrequentNote ?? NOT_MEASURED,
      measured: summary.mostFrequentNote !== null,
    },
    {
      label: "Average deviation",
      value:
        summary.averageCents === null
          ? NOT_MEASURED
          : formatCents(summary.averageCents),
      measured: summary.averageCents !== null,
    },
    {
      label: "Frames with a pitch",
      value:
        summary.voicedFraction === null
          ? NOT_MEASURED
          : `${Math.round(summary.voicedFraction * 100)}%`,
      measured: summary.voicedFraction !== null,
    },
  ];

  return (
    <section
      aria-labelledby="live-summary-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <div className="border-b border-border p-5">
        <h3 id="live-summary-heading" className="text-sm font-medium">
          Live recording estimate
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Measured in your browser as you recorded, from the pitch of your
          voice. This is not the speech analysis, and the two are not
          comparable — analyse the recording for that.
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-px overflow-hidden bg-border sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.label} className="bg-surface px-5 py-4">
            <dt className="text-xs text-muted">{row.label}</dt>
            <dd
              className={`mt-1 font-mono text-sm tabular-nums ${
                row.measured ? "" : "text-muted"
              }`}
            >
              {row.value}
            </dd>
          </div>
        ))}
        {/* Five rows leave a hole in a two- or three-column grid, and the hole
            shows as a block of the divider colour. One filler cell closes it
            at both breakpoints. */}
        <div aria-hidden className="bg-surface" />
      </dl>

      <p className="border-t border-border px-5 py-4 text-xs leading-relaxed text-muted">
        Detected range is what this recording contained, not the limit of your
        voice.
      </p>
    </section>
  );
}
