"use client";

import {
  caveatMessages,
  fitRows,
  keyLabel,
  landingLabel,
  rangeLabel,
  refusalMessage,
  sourceLabel,
  transpositionSentence,
  windowLabel,
} from "@/lib/song-compatibility";
import type { NoteRange, SongCompatibility } from "@/types/api";

interface CompatibilityResultProps {
  result: SongCompatibility;
}

/**
 * A recording's detected range, placed against a song's asserted one.
 *
 * **The two ranges are never styled alike, and the difference is in words.**
 * One was measured from audio; the other is what somebody typed. A reader who
 * cannot tell them apart is reading a claim this product does not make — so
 * each range carries its provenance as a sentence, not as a colour, because a
 * colour is not announced by a screen reader and does not survive a screenshot.
 *
 * **There is no headline figure**, and no field in the payload could hold one.
 * A single number would have to weight the gap at the top against the gap at
 * the bottom against the overlap in the middle, and nothing measured anywhere
 * sets those weights. The components are the answer.
 *
 * **The three standing caveats are rendered, always.** They describe the method
 * rather than the inputs, so they are true of every result this card can show,
 * and they are sentences in the body rather than fine print under it.
 */
export function CompatibilityResult({ result }: CompatibilityResultProps) {
  if (!result.comparable) {
    return (
      <div className="px-5 py-6">
        <p className="text-sm font-medium">No comparison yet</p>
        <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
          {refusalMessage(result.recording_status)}
        </p>
      </div>
    );
  }

  // Guaranteed together by the server: a comparable result carries both ranges,
  // the fit and the transposition, or none of them.
  const { recording_range: mine, reference_range: song, fit, transposition } = result;
  if (mine === null || song === null || fit === null || transposition === null) {
    return null;
  }

  const landing = landingLabel(transposition);
  const alternatives = windowLabel(transposition);
  const resultingKey = keyLabel(transposition.resulting_key);

  return (
    <div className="fade-in">
      <div className="grid gap-px border-b border-border bg-border sm:grid-cols-2">
        <RangeColumn heading="Your range in this recording" range={mine} />
        <RangeColumn
          heading={result.reference?.title ?? "The song"}
          range={song}
          keyName={keyLabel(result.reference?.key ?? null)}
        />
      </div>

      <dl className="grid gap-px bg-border sm:grid-cols-2">
        {fitRows(fit).map((row) => (
          <div key={row.label} className="bg-surface px-5 py-4">
            <dt className="text-xs text-muted">{row.label}</dt>
            <dd className="mt-1 font-mono text-lg tabular-nums">{row.value}</dd>
            <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
              {row.hint}
            </p>
          </div>
        ))}
      </dl>

      <section
        aria-labelledby="transposition-heading"
        className="border-t border-border px-5 py-5"
      >
        <h5 id="transposition-heading" className="text-xs text-muted">
          Shifting the song
        </h5>
        <p className="mt-1 max-w-prose text-sm leading-relaxed">
          {transpositionSentence(transposition)}
        </p>
        {landing !== null && (
          <p className="mt-2 font-mono text-sm tabular-nums">
            {landing}
            {resultingKey !== null && (
              <span className="text-muted"> · {resultingKey}</span>
            )}
          </p>
        )}
        {alternatives !== null && (
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            {alternatives}
          </p>
        )}
        <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
          This is arithmetic on two ranges. It says a shift exists — not that
          the result is singable, and not that it should be performed that way.
        </p>
      </section>

      <section
        aria-labelledby="compatibility-caveats-heading"
        className="border-t border-border px-5 py-5"
      >
        <h5 id="compatibility-caveats-heading" className="text-xs text-muted">
          How to read this
        </h5>
        <ul className="mt-2 space-y-2">
          {caveatMessages(result).map((message) => (
            <li key={message} className="max-w-prose text-xs leading-relaxed text-muted">
              {message}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function RangeColumn({
  heading,
  range,
  keyName,
}: {
  heading: string;
  range: NoteRange;
  keyName?: string | null;
}) {
  return (
    <div className="bg-surface px-5 py-5">
      <p className="text-xs text-muted">{heading}</p>
      <p className="mt-1 font-mono text-2xl tabular-nums">{rangeLabel(range)}</p>
      <p className="mt-2 text-xs leading-relaxed text-muted">
        {sourceLabel(range.source)}
        {keyName != null && ` · ${keyName}`}
      </p>
    </div>
  );
}
