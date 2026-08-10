/**
 * Turning audio measurements into rows a person can read.
 *
 * The one rule this module exists to enforce: **`null` is not zero.** A
 * recording whose signal could not support a measurement shows "Not measured",
 * never `0`, never `0%`, never `—` used to mean zero. Every row carries a
 * `measured` flag so the UI can style the difference rather than infer it from
 * the string.
 *
 * The second rule is that a number never appears without its definition.
 * "Pitch consistency 82%" means nothing on its own; "share of voiced frames
 * within 25 cents of a note" is a measurement. The hint travels with the row.
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

import type {
  AudioSummary,
  DetectedRange,
  LoudnessMetrics,
  PitchStabilityMetrics,
  SpectralMetrics,
} from "../types/api";

export const NOT_MEASURED = "Not measured";

/** Cents within which a voiced frame counts as on a note. Matches the backend. */
export const IN_TUNE_CENTS = 25;

export interface MetricRow {
  label: string;
  value: string;
  /** `false` when the signal did not support this measurement. */
  measured: boolean;
  hint?: string;
}

function measured(label: string, value: string, hint?: string): MetricRow {
  return { label, value, measured: true, hint };
}

function unmeasured(label: string, hint?: string): MetricRow {
  return { label, value: NOT_MEASURED, measured: false, hint };
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function cents(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return "0 cents";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} cents`;
}

/** `G2 — C5`, or `null` when nothing was held long enough to define a range. */
export function rangeLabel(range: DetectedRange | null): string | null {
  if (range === null) return null;
  return `${range.lowest_note} — ${range.highest_note}`;
}

export function rangeRows(range: DetectedRange | null): MetricRow[] {
  if (range === null) {
    return [
      unmeasured("Lowest detected note"),
      unmeasured("Highest detected note"),
      unmeasured("Span"),
    ];
  }
  return [
    measured(
      "Lowest detected note",
      range.lowest_note,
      `${range.lowest_frequency_hz.toFixed(1)} Hz`,
    ),
    measured(
      "Highest detected note",
      range.highest_note,
      `${range.highest_frequency_hz.toFixed(1)} Hz`,
    ),
    measured(
      "Span",
      `${range.semitone_span} ${range.semitone_span === 1 ? "semitone" : "semitones"}`,
      "Between the two extremes above.",
    ),
  ];
}

export function stabilityRows(stability: PitchStabilityMetrics): MetricRow[] {
  return [
    measured(
      "Pitch consistency",
      stability.in_tune_ratio === null ? NOT_MEASURED : percent(stability.in_tune_ratio),
      `Share of voiced frames within ${IN_TUNE_CENTS} cents of the nearest note.`,
    ),
    measured(
      "Frames with a pitch",
      percent(stability.voiced_ratio),
      `${stability.voiced_frames} of ${stability.total_frames} analysed frames.`,
    ),
    stability.mean_cents_deviation === null
      ? unmeasured("Average deviation", "Distance from the nearest note.")
      : measured(
          "Average deviation",
          cents(stability.mean_cents_deviation),
          "Signed: negative reads flat, positive sharp.",
        ),
    stability.cents_std === null
      ? unmeasured("Deviation spread")
      : measured(
          "Deviation spread",
          `${stability.cents_std.toFixed(1)} cents`,
          "Standard deviation across voiced frames.",
        ),
  ].map((row) =>
    // `in_tune_ratio` is null exactly when nothing was voiced, and the row above
    // builds its own string for that case; keep the flag honest.
    row.value === NOT_MEASURED ? { ...row, measured: false } : row,
  );
}

export function loudnessRows(loudness: LoudnessMetrics): MetricRow[] {
  return [
    measured("Level (RMS)", loudness.rms.toFixed(3), "Not a LUFS measurement."),
    measured(
      "Peak",
      loudness.peak.toFixed(3),
      loudness.peak >= 0.999 ? "At full scale." : undefined,
    ),
    loudness.dynamic_range_db === null
      ? unmeasured("Dynamic range", "Too little audible signal to compare.")
      : measured(
          "Dynamic range",
          `${loudness.dynamic_range_db.toFixed(1)} dB`,
          "95th minus 5th percentile of frame level. An estimate.",
        ),
    measured(
      "Clipped samples",
      percent(loudness.clipped_sample_ratio),
      "Samples at or beyond full scale.",
    ),
  ];
}

export function spectralRows(spectral: SpectralMetrics | null): MetricRow[] {
  if (spectral === null) {
    return [
      unmeasured("Spectral centroid"),
      unmeasured("Bandwidth"),
      unmeasured("Rolloff"),
      unmeasured("Flatness"),
    ];
  }
  return [
    measured("Spectral centroid", `${Math.round(spectral.centroid_hz)} Hz`),
    measured("Bandwidth", `${Math.round(spectral.bandwidth_hz)} Hz`),
    measured("Rolloff", `${Math.round(spectral.rolloff_hz)} Hz`, "85% of the energy is below this."),
    measured("Flatness", spectral.flatness.toFixed(3), "1 is noise-like, 0 is tone-like."),
  ];
}

/**
 * Whether there is a pitch result worth showing at all.
 *
 * A completed analysis always has a range — the server fails with
 * `INSUFFICIENT_PITCH_SIGNAL` otherwise — but the type allows `null`, and the
 * UI must handle it rather than assert it away.
 */
export function hasPitchResult(summary: AudioSummary | null): boolean {
  return summary !== null && summary.range !== null;
}
