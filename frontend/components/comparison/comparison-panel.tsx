"use client";

import { Button } from "@/components/ui/button";
import { useComparison } from "@/hooks/use-comparison";
import {
  NOT_MEASURED,
  caveatMessage,
  describeDelta,
  formatDelta,
  formatNoteDelta,
  formatNoteShare,
  formatValue,
  sideStatusLabel,
  sideStatusMessage,
} from "@/lib/comparison";
import { formatWhen } from "@/lib/history";
import type { ComparisonSide, RecordingComparison } from "@/types/api";

interface ComparisonPanelProps {
  leftId: string;
  rightId: string;
  onClose: () => void;
}

/**
 * Two recordings' measurements, side by side.
 *
 * **A comparison of measurements, not a verdict.** There is no overall figure
 * here, nothing is coloured green or red for being higher, and the four neutral
 * metrics are stated as plain differences. A wider detected range is not an
 * achievement — it is bounded by what was performed and by the microphone — and
 * nothing on this page suggests otherwise.
 *
 * A table is the primary representation and the only one. A chart of seven
 * metrics in different units would be a composite picture of things that cannot
 * be composed, and a radar chart of them would look exactly like the ability
 * score this product does not have.
 *
 * The caveat sits above the numbers rather than under them, because it changes
 * how they should be read.
 */
export function ComparisonPanel({ leftId, rightId, onClose }: ComparisonPanelProps) {
  const state = useComparison(leftId, rightId);

  return (
    <section
      aria-labelledby="comparison-heading"
      className="fade-in rounded-xl border border-border bg-surface"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-5">
        <div>
          <h3 id="comparison-heading" className="text-sm font-medium">
            Comparing two recordings
          </h3>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            The measurements from each recording, side by side. This is a
            comparison of what was measured — not a score, a ranking, or a
            judgement about which recording is better.
          </p>
        </div>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </header>

      <p aria-live="polite" className="sr-only">
        {announcement(state)}
      </p>

      {state.status === "loading" && (
        <p className="p-5 text-sm text-muted">Comparing…</p>
      )}

      {state.status === "error" && (
        <div role="alert" className="p-5">
          <p className="text-sm font-medium text-danger">
            These recordings couldn&apos;t be compared
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
        </div>
      )}

      {state.status === "ready" && <Result comparison={state.comparison} />}
    </section>
  );
}

function Result({ comparison }: { comparison: RecordingComparison }) {
  const { left, right } = comparison;

  return (
    <div className="space-y-6 p-5">
      <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
        <SideCard side={left} position="First recording" />
        <SideCard side={right} position="Second recording" />
      </div>

      {/* Above the numbers, because it changes how they should be read. */}
      <div
        role="note"
        className="rounded-xl border border-border bg-surface-raised p-4"
      >
        <p className="text-xs font-medium">Before you read these</p>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          Two recordings are only meaningfully comparable when captured under
          reasonably similar conditions. VocalLens cannot measure microphone
          quality, room acoustics, how hard you were trying or how you felt, and
          it does not try to correct for them.
        </p>
        {comparison.caveats.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {comparison.caveats.map((caveat) => (
              <li
                key={caveat}
                className="max-w-prose text-xs leading-relaxed text-muted before:mr-2 before:content-['—']"
              >
                {caveatMessage(caveat)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {!comparison.comparable ? (
        <div className="rounded-xl border border-border p-5">
          <p className="text-sm font-medium">Nothing to compare yet</p>
          <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
            Both recordings need a completed audio analysis before their
            measurements can be placed side by side.
          </p>
          <ul className="mt-3 space-y-1.5">
            {[left, right]
              .filter((side) => side.status !== "ready")
              .map((side) => (
                <li key={side.recording_id} className="max-w-prose text-sm text-muted">
                  <span className="font-medium text-foreground">
                    {side.original_filename ?? "That recording"}:
                  </span>{" "}
                  {sideStatusMessage(side)}
                </li>
              ))}
          </ul>
        </div>
      ) : (
        <>
          <MetricTable comparison={comparison} />
          <NoteTable comparison={comparison} />
          <p className="text-xs leading-relaxed text-muted">
            Every figure above was measured from the saved audio. Nothing here is
            a score, and no difference means one recording is better than the
            other — four of these measurements have no better direction at all.
          </p>
        </>
      )}
    </div>
  );
}

function SideCard({ side, position }: { side: ComparisonSide; position: string }) {
  return (
    <div className="bg-surface px-5 py-4">
      <p className="text-xs text-muted">{position}</p>
      <p className="mt-1 truncate font-medium">
        {side.original_filename ?? "Not available"}
      </p>
      <p className="mt-1 text-xs text-muted">
        {side.created_at ? formatWhen(side.created_at) : "—"}
        {" · "}
        {sideStatusLabel(side.status)}
      </p>
      <p className="mt-2 text-xs text-muted">
        Detected range:{" "}
        {side.lowest_note && side.highest_note ? (
          <span className="font-mono">
            {side.lowest_note}–{side.highest_note}
          </span>
        ) : (
          <span>{NOT_MEASURED}</span>
        )}
      </p>
    </div>
  );
}

function MetricTable({ comparison }: { comparison: RecordingComparison }) {
  return (
    <section aria-labelledby="comparison-metrics-heading">
      <h4 id="comparison-metrics-heading" className="text-sm font-medium">
        Pitch and tuning
      </h4>
      {/* The table scrolls inside its own container so the page never does. */}
      <div className="mt-3 overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[36rem] border-collapse text-sm">
          <caption className="sr-only">
            Each measurement from both recordings, with the difference between
            them. A difference is the second recording minus the first.
          </caption>
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th scope="col" className="px-4 py-3 font-medium">
                Measurement
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                First
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Second
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Difference
              </th>
            </tr>
          </thead>
          <tbody>
            {comparison.metrics.map((metric) => {
              const delta = formatDelta(metric);
              return (
                <tr key={metric.key} className="border-b border-border last:border-0 align-top">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    {metric.label}
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {describeDelta(metric)}
                    </span>
                  </th>
                  <td
                    className={`px-4 py-3 font-mono tabular-nums ${
                      metric.left === null ? "text-muted" : ""
                    }`}
                  >
                    {formatValue(metric, "left")}
                  </td>
                  <td
                    className={`px-4 py-3 font-mono tabular-nums ${
                      metric.right === null ? "text-muted" : ""
                    }`}
                  >
                    {formatValue(metric, "right")}
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums">
                    {/* No colour on a difference: three of these metrics have
                        no better direction, and colouring only the others would
                        imply the rest are neutral by omission rather than by
                        definition. */}
                    {delta ?? <span className="text-muted">{NOT_MEASURED}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function NoteTable({ comparison }: { comparison: RecordingComparison }) {
  if (comparison.notes.length === 0) {
    return (
      <section aria-labelledby="comparison-notes-heading">
        <h4 id="comparison-notes-heading" className="text-sm font-medium">
          Notes
        </h4>
        <p className="mt-2 max-w-prose text-sm text-muted">
          Neither recording broke down into notes.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="comparison-notes-heading">
      <h4 id="comparison-notes-heading" className="text-sm font-medium">
        Notes
      </h4>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
        How each recording&apos;s pitched time was divided between notes, lowest
        first. A note only one recording contains reads &ldquo;Not sung&rdquo; in
        the other — it was never sung there, which is not the same as having been
        sung for no time.
      </p>
      <div className="mt-3 overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[32rem] border-collapse text-sm">
          <caption className="sr-only">
            Each note detected in either recording, with its share of that
            recording&apos;s pitched time and the difference in percentage points.
          </caption>
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th scope="col" className="px-4 py-3 font-medium">
                Note
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                First
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Second
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Difference
              </th>
            </tr>
          </thead>
          <tbody>
            {comparison.notes.map((note) => {
              const delta = formatNoteDelta(note);
              return (
                <tr
                  key={note.midi_note}
                  className="border-b border-border last:border-0"
                >
                  <th scope="row" className="px-4 py-3 text-left font-mono font-normal">
                    {note.note_name}
                  </th>
                  <td
                    className={`px-4 py-3 font-mono tabular-nums ${
                      note.left === null ? "text-muted" : ""
                    }`}
                  >
                    {formatNoteShare(note, "left")}
                  </td>
                  <td
                    className={`px-4 py-3 font-mono tabular-nums ${
                      note.right === null ? "text-muted" : ""
                    }`}
                  >
                    {formatNoteShare(note, "right")}
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums">
                    {delta ?? <span className="text-muted">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function announcement(state: ReturnType<typeof useComparison>): string {
  switch (state.status) {
    case "loading":
      return "Comparing the two recordings.";
    case "error":
      return `The comparison failed. ${state.message}`;
    case "ready":
      return state.comparison.comparable
        ? "Comparison ready. The measurements are below."
        : "These recordings cannot be compared yet. The reasons are below.";
  }
}
