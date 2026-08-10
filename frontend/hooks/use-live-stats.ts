"use client";

import { useEffect, useState } from "react";
import { EMPTY_STATS, LivePracticeStats, type LiveStats } from "@/lib/live-practice";
import type { LivePitchSample } from "@/lib/pitch-stream";

/** How often the summary figures reach React. Twice a second is plenty. */
export const STATS_REFRESH_MS = 500;

/**
 * Session statistics, accumulated at frame rate and published slowly.
 *
 * This is the one place live pitch data meets React, and the split is the whole
 * point: `push` runs ~30 times a second inside the detector's subscription and
 * touches no state, while a timer publishes a snapshot twice a second. Two
 * renders a second for four numbers that change slowly, instead of thirty for
 * numbers nobody can read that fast.
 *
 * Restarting is keyed on `sessionId`: a new take gets fresh statistics rather
 * than inheriting the previous one's range.
 */
export function useLiveStats(
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void,
  active: boolean,
  sessionId: number,
): LiveStats {
  const [stats, setStats] = useState<LiveStats>(EMPTY_STATS);

  useEffect(() => {
    if (!active) return;

    const accumulator = new LivePracticeStats();
    const unsubscribe = subscribe((sample) => accumulator.push(sample));
    const timer = setInterval(() => setStats(accumulator.snapshot()), STATS_REFRESH_MS);

    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [subscribe, active, sessionId]);

  return stats;
}
