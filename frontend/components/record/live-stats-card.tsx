"use client";

import type { LiveStats } from "@/lib/live-practice";
import { IN_TUNE_CENTS, MIN_CONSISTENCY_FRAMES } from "@/lib/live-practice";

interface LiveStatsCardProps {
  stats: LiveStats;
}

/**
 * What the session has covered so far: consistency, and the range reached.
 *
 * Updated a couple of times a second, not per frame — these are summary
 * figures, nobody reads them at 30 Hz, and this is the only part of the live
 * display that touches React state.
 *
 * Both figures distinguish "not measured yet" from zero. A session where
 * nothing has been voiced has no consistency and no range; it does not have 0%
 * consistency and a range of zero semitones.
 */
export function LiveStatsCard({ stats }: LiveStatsCardProps) {
  const waiting = stats.windowFrames < MIN_CONSISTENCY_FRAMES;

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

      <p className="border-t border-border px-4 py-3 text-xs leading-relaxed text-muted">
        Consistency is the share of the last few seconds of voiced audio that sat
        within {IN_TUNE_CENTS} cents of the nearest note — not a measure of
        singing ability. Range is what this session contained, not the limit of
        your voice; a pitch has to be held briefly to count.
      </p>
    </div>
  );
}
