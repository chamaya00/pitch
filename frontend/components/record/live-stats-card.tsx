"use client";

import type { LiveStats } from "@/lib/live-practice";
import {
  IN_TUNE_CENTS,
  MIN_CONSISTENCY_FRAMES,
  liveKeyLabel,
  liveKeyMarginLabel,
  liveKeyWaitingMessage,
} from "@/lib/live-practice";

interface LiveStatsCardProps {
  stats: LiveStats;
}

/**
 * What the session has covered so far: consistency, the range reached, and
 * the key it seems to be in.
 *
 * Updated a couple of times a second, not per frame — these are summary
 * figures, nobody reads them at 30 Hz, and this is the only part of the live
 * display that touches React state.
 *
 * Every figure distinguishes "not measured yet" from zero. A session where
 * nothing has been voiced has no consistency, no range and no key; it does not
 * have 0% consistency, a range of zero semitones and a blank key.
 *
 * The key is the one figure here with a counterpart elsewhere in the product,
 * and the two must never be shown together. The uploaded recording's key is a
 * **different measurement**: a different clarity gate over a different window,
 * so different frames survive and different profiles come out. This one belongs
 * to the session in progress, is labelled as such, and disappears with it —
 * neither validates the other and neither is the "real" one.
 */
export function LiveStatsCard({ stats }: LiveStatsCardProps) {
  const waiting = stats.windowFrames < MIN_CONSISTENCY_FRAMES;
  const keyLabel = liveKeyLabel(stats);
  const keyMargin = liveKeyMarginLabel(stats);

  return (
    <div className="rounded-xl border border-border bg-surface">
      <dl className="grid grid-cols-2 gap-px overflow-hidden bg-border sm:grid-cols-4">
        <div className="bg-surface px-4 py-3">
          <dt className="text-xs text-muted">Pitch consistency</dt>
          <dd className="mt-1 font-mono text-lg tabular-nums">
            {stats.consistency === null ? (
              <span className="text-sm text-muted">
                {waiting ? "Not enough yet" : "Not measured"}
              </span>
            ) : (
              `${Math.round(stats.consistency * 100)}%`
            )}
          </dd>
        </div>
        <div className="bg-surface px-4 py-3">
          <dt className="text-xs text-muted">Lowest</dt>
          <dd className="mt-1 font-mono text-lg tabular-nums">
            {stats.lowestNote ?? <span className="text-sm text-muted">—</span>}
          </dd>
        </div>
        <div className="bg-surface px-4 py-3">
          <dt className="text-xs text-muted">Highest</dt>
          <dd className="mt-1 font-mono text-lg tabular-nums">
            {stats.highestNote ?? <span className="text-sm text-muted">—</span>}
          </dd>
        </div>
        <div className="bg-surface px-4 py-3">
          <dt className="text-xs text-muted">Range</dt>
          <dd className="mt-1 font-mono text-lg tabular-nums">
            {stats.semitoneSpan === null ? (
              <span className="text-sm text-muted">—</span>
            ) : (
              `${stats.semitoneSpan} st`
            )}
          </dd>
        </div>
      </dl>

      <div className="border-t border-border px-4 py-3">
        <dl>
          <dt className="text-xs text-muted">Key you seem to be singing in</dt>
          <dd className="mt-1 font-mono text-lg tabular-nums">
            {keyLabel ?? (
              <span className="text-sm text-muted">Not enough yet</span>
            )}
          </dd>
        </dl>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          {keyLabel === null ? (
            liveKeyWaitingMessage(stats.keyUnmeasuredReason)
          ) : (
            <>
              {keyMargin}. A live estimate from everything this session has
              contained so far — not the key of a song, and not a claim that you
              sang it correctly.
            </>
          )}
        </p>
      </div>

      <p className="border-t border-border px-4 py-3 text-xs leading-relaxed text-muted">
        Consistency is the share of the last few seconds of voiced audio that sat
        within {IN_TUNE_CENTS} cents of the nearest note — not a measure of
        singing ability. Range is what this session contained, not the limit of
        your voice; a pitch has to be held briefly to count. The key is folded
        from every voiced frame of this session, so it settles slowly and
        averages a session that changes key.
      </p>
    </div>
  );
}
