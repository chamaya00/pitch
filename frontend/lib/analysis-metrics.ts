/**
 * Turning measured metrics into rows a component can render without thinking.
 *
 * The whole module exists to protect one distinction the backend went to
 * trouble to preserve: **`null` is not zero.** `0` pauses means the recording
 * was measured and no gap was long enough. `null` pauses means there were no
 * word timings to look for gaps in. Rendering both as "0" would invent a
 * measurement, so every row carries a `measured` flag and unmeasured rows say
 * so in words.
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

import { formatDuration } from "./format.ts";
import type { FillerWordStats, SpeechMetrics } from "../types/api";

/** Copy shown wherever a measurement could not be taken. */
export const NOT_MEASURED = "Not measured";

export interface MetricRow {
  label: string;
  /** Already formatted. Equals `NOT_MEASURED` when `measured` is false. */
  value: string;
  /** `false` when the recording did not support this measurement. */
  measured: boolean;
  /** Short explanation, shown for context. */
  hint?: string;
}

function seconds(value: number): string {
  // Sub-minute durations read better with one decimal than as "0:03".
  return value < 60 ? `${round(value, 1)}s` : formatDuration(value);
}

function round(value: number, places: number): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

function row(
  label: string,
  value: number | null,
  format: (value: number) => string,
  hint?: string,
  unmeasuredHint?: string,
): MetricRow {
  if (value === null || Number.isNaN(value)) {
    return {
      label,
      value: NOT_MEASURED,
      measured: false,
      ...(unmeasuredHint ? { hint: unmeasuredHint } : {}),
    };
  }
  return { label, value: format(value), measured: true, ...(hint ? { hint } : {}) };
}

/** Rows describing how much was said and how fast. */
export function paceRows(metrics: SpeechMetrics): MetricRow[] {
  const spanHint =
    metrics.duration_source === "recording_duration"
      ? "Measured over the whole recording, including silence at either end."
      : metrics.duration_source === "word_timings"
        ? "Measured from the first word to the last."
        : undefined;

  return [
    {
      label: "Words",
      value: String(metrics.word_count),
      measured: true,
    },
    row(
      "Speaking time",
      metrics.speaking_duration_seconds,
      seconds,
      spanHint,
      "No usable duration was available for this recording.",
    ),
    row(
      "Words per minute",
      metrics.words_per_minute,
      (value) => String(round(value, 1)),
      spanHint,
      "Needs a duration, which this recording did not provide.",
    ),
    row(
      "Articulation rate",
      metrics.articulation_rate_wpm,
      (value) => `${round(value, 1)} wpm`,
      "Words per minute with pauses removed.",
      "Needs word timings, which this transcript did not include.",
    ),
  ];
}

/** Rows describing pauses. */
export function pauseRows(metrics: SpeechMetrics): MetricRow[] {
  const thresholdHint =
    metrics.pause_threshold_seconds === null
      ? undefined
      : `A gap of ${round(metrics.pause_threshold_seconds, 2)}s or more counts as a pause.`;
  const needsTimings = "Needs word timings, which this transcript did not include.";

  return [
    row("Pauses", metrics.pause_count, String, thresholdHint, needsTimings),
    row("Total pause time", metrics.total_pause_seconds, seconds, undefined, needsTimings),
    row(
      "Longest pause",
      metrics.longest_pause_seconds,
      seconds,
      undefined,
      metrics.pause_count === 0
        ? "No pause was long enough to measure one."
        : needsTimings,
    ),
    row(
      "Average pause",
      metrics.mean_pause_seconds,
      seconds,
      undefined,
      metrics.pause_count === 0
        ? "No pause was long enough to measure one."
        : needsTimings,
    ),
  ];
}

export interface FillerSummary {
  /** `false` when the transcript was not verbatim — never "none were said". */
  measured: boolean;
  hesitations: number | null;
  discourseMarkers: number | null;
  terms: { term: string; count: number; category: string }[];
  note: string;
}

/**
 * Filler statistics, or an explanation of why there are none.
 *
 * Absent statistics get `measured: false` and no counts at all. Reporting "0
 * filler words" for a transcript that could not preserve them would be the
 * single most misleading number this product could show.
 */
export function fillerSummary(stats: FillerWordStats | null): FillerSummary {
  if (stats === null) {
    return {
      measured: false,
      hesitations: null,
      discourseMarkers: null,
      terms: [],
      note: "This transcript isn't guaranteed to keep filler sounds, so they can't be counted from it.",
    };
  }

  return {
    measured: true,
    hesitations: stats.hesitation_count,
    discourseMarkers: stats.discourse_marker_count,
    terms: stats.by_term.map((item) => ({
      term: item.term,
      count: item.count,
      category: String(item.category),
    })),
    note: "Hesitations are sounds with no other meaning. Discourse markers are ordinary words matched by text, so they include normal uses.",
  };
}
