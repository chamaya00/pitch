"use client";

import {
  isWeakKey,
  keyLabel,
  keyUnmeasuredMessage,
  pitchClassRows,
} from "@/lib/audio-analysis-metrics";
import type { MusicalKey } from "@/types/api";

interface MusicalKeyCardProps {
  musicalKey: MusicalKey | null;
  error: string | null;
}

/**
 * The key the notes in this recording best fit.
 *
 * The one card in this product that reports a **classification** rather than a
 * measurement, and it is built to keep that visible. A pitch is arithmetic on a
 * frequency and is right or wrong in a checkable way; a key is a label chosen by
 * correlating a distribution against hand-authored profiles. So the twelve
 * pitch-class shares that produced the label are shown beside it — in every
 * state, including the one where there is no label — and the reader can see the
 * evidence rather than being asked to trust the verdict.
 *
 * Three things this card must never do, each of which has a test:
 *
 * - **Render a tonic when there is none.** `key: null` is a successful
 *   measurement meaning the notes settled nothing. It is "Not measured", with
 *   the reason in words, exactly as every other unavailable measurement here.
 * - **Show the confidence as a percentage or a grade.** It is a margin over the
 *   next-best candidate, and it travels with that definition attached. A margin
 *   of 0.26 is not "26% certain".
 * - **Round a weak answer up.** A thin margin is stated in words, not softened
 *   and not hidden behind the label.
 *
 * There is no song here. The label is what *was sung in this recording*, never
 * the key of anything the singer may have been aiming at — this product has no
 * reference melody to compare against.
 */
export function MusicalKeyCard({ musicalKey, error }: MusicalKeyCardProps) {
  const estimate = musicalKey?.key ?? null;
  const weak = isWeakKey(estimate);

  return (
    <section
      aria-labelledby="musical-key-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <div className="border-b border-border px-5 py-4">
        <h4 id="musical-key-heading" className="text-sm font-medium">
          Estimated key of what was sung in this recording
        </h4>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          Which key the notes best fit, folded from the same pitched time as the
          note breakdown with the octaves discarded. It is not a claim that this
          was the right key — there is no reference melody here to compare
          against.
        </p>
      </div>

      {musicalKey === null ? (
        <p className="px-5 py-4 text-sm text-muted">
          {error ?? "Loading the estimated key…"}
        </p>
      ) : (
        <>
          <div className="px-5 py-6">
            {estimate === null ? (
              <>
                <p className="font-mono text-3xl tabular-nums text-muted">
                  Not measured
                </p>
                <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                  {keyUnmeasuredMessage(musicalKey.unmeasured_reason)} The pitch
                  classes it was folded from are below, so you can see why.
                </p>
              </>
            ) : (
              <>
                <p className="font-mono text-3xl tabular-nums">
                  {keyLabel(estimate)}
                </p>
                {weak && (
                  // Stated in words, never colour alone — the rule the pitch
                  // meter follows. A weak answer is still an answer, and it is
                  // shown with its tonic, its runner-up and its evidence.
                  <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                    <strong className="font-medium">
                      This key rests on thin evidence.
                    </strong>{" "}
                    It stood only narrowly clear of the next-best candidate, so
                    read it as the closest fit rather than a settled answer.
                  </p>
                )}
                <dl className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div>
                    <dt className="text-xs text-muted">Margin over the next-best</dt>
                    <dd className="mt-1 font-mono text-sm tabular-nums">
                      {estimate.confidence.toFixed(3)}
                    </dd>
                    <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
                      How far this candidate stood clear of the next one. Not a
                      percentage, not a probability and not a mark out of
                      anything.
                    </p>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Next-best candidate</dt>
                    <dd className="mt-1 font-mono text-sm tabular-nums">
                      {estimate.alternative === null ? (
                        <span className="text-muted">Not measured</span>
                      ) : (
                        `${estimate.alternative.tonic} ${estimate.alternative.mode}`
                      )}
                    </dd>
                    <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
                      The key that came second, shown so a close call is visible
                      rather than hidden behind one label.
                    </p>
                  </div>
                </dl>
              </>
            )}
          </div>

          {musicalKey.pitch_classes.length > 0 && (
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  The twelve pitch classes and the share of pitched time each
                  carried, in pitch-class order starting at C. Classes carrying
                  none of the time are listed at zero.
                </caption>
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted">
                    <th scope="col" className="px-5 py-2 font-normal">
                      Pitch class
                    </th>
                    <th scope="col" className="px-3 py-2 font-normal">
                      Share of pitched time
                    </th>
                    <th scope="col" className="px-5 py-2 font-normal">
                      Counted as used
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pitchClassRows(musicalKey.pitch_classes).map((row) => (
                    <tr
                      key={row.pitchClass}
                      className="border-b border-border/60"
                      // Decoration only, as in the note breakdown: every number
                      // the bar encodes is in a cell of the same row.
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
                        {row.name}
                      </th>
                      <td className="px-3 py-2.5 font-mono tabular-nums">
                        {row.share}
                      </td>
                      <td className="px-5 py-2.5 font-mono tabular-nums text-muted">
                        {row.used ? "Yes" : "No"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="border-t border-border px-5 py-4 text-xs leading-relaxed text-muted">
            Folded from {musicalKey.voiced_seconds.toFixed(2)}s of pitched time,
            of which {musicalKey.distinct_pitch_classes} pitch{" "}
            {musicalKey.distinct_pitch_classes === 1 ? "class" : "classes"}{" "}
            carried enough of it to count as used. Compared against the{" "}
            <span className="font-mono">{musicalKey.method}</span> key profiles.
            This estimate reads the melody only: it cannot hear harmony, it
            assumes equal temperament, it reports one key for the whole
            recording, and it has not been validated against real singing.
          </p>
        </>
      )}
    </section>
  );
}
