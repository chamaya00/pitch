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
  KeyEstimate,
  KeyUnmeasuredReason,
  LoudnessMetrics,
  MusicalKey,
  NoteBreakdown,
  NoteSummary,
  PitchClassShare,
  PitchStabilityMetrics,
  SpectralMetrics,
} from "../types/api";

export const NOT_MEASURED = "Not measured";

/** Cents within which a voiced frame counts as on a note. Matches the backend. */
export const IN_TUNE_CENTS = 25;

/**
 * The cents threshold behind a "moved" stretch. Matches the backend.
 *
 * Here so the graph's caption and its screen-reader description quote one
 * number rather than two literals that can drift apart — which they did, within
 * an hour of being written, when the backend value moved and only one of them
 * followed.
 *
 * The quarter-second minimum is a word in the sentence rather than a constant,
 * because "0.25 of a second" is not how anybody says it. If it moves, this
 * sentence moves with it.
 */
export const UNSTABLE_CENTS_STD = 20;

/** "…moved more than 20 cents over at least a quarter of a second." */
export function movedStretchDefinition(): string {
  return `moved more than ${UNSTABLE_CENTS_STD} cents over at least a quarter of a second`;
}

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
    stability.mean_abs_cents_deviation === null
      ? unmeasured("Typical distance from a note")
      : measured(
          "Typical distance from a note",
          `${stability.mean_abs_cents_deviation.toFixed(1)} cents`,
          "How far from a note, ignoring whether it was flat or sharp.",
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

/*
 * **`semitone_variance` is deliberately not a row**, and this is where that is
 * written down.
 *
 * `docs/ai.md` withholds it from the prompt because semitones squared has no
 * plain-language reading, and `cents_std` already answers "how much did it
 * move" in units a person can picture. Every word of that applies to a reader
 * of this table, and until Step 11.6 the reason existed only for the model —
 * so the field looked like an oversight here rather than a decision. It stays
 * in the API for a client that wants it.
 */

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
    // Computed, documented and returned since Step 7I, and shown to nobody
    // until Step 11.6 — found by an audit of the payload against the screen
    // rather than by anyone asking for it.
    loudness.crest_factor_db === null
      ? unmeasured("Crest factor", "Too little audible signal to compare peak with level.")
      : measured(
          "Crest factor",
          `${loudness.crest_factor_db.toFixed(1)} dB`,
          "Peak over level. A steady tone is low; speech with sharp consonants is high.",
        ),
  ];
}

export function spectralRows(spectral: SpectralMetrics | null): MetricRow[] {
  if (spectral === null) {
    return [
      unmeasured("Spectral centroid"),
      unmeasured("Bandwidth"),
      unmeasured("Rolloff"),
      unmeasured("Zero-crossing rate"),
      unmeasured("Flatness"),
    ];
  }
  return [
    measured("Spectral centroid", `${Math.round(spectral.centroid_hz)} Hz`),
    measured("Bandwidth", `${Math.round(spectral.bandwidth_hz)} Hz`),
    measured("Rolloff", `${Math.round(spectral.rolloff_hz)} Hz`, "85% of the energy is below this."),
    // As with the crest factor: measured all along, named in the README, and
    // absent from the screen until an audit compared the two.
    measured(
      "Zero-crossing rate",
      spectral.zero_crossing_rate.toFixed(3),
      "Share of samples where the waveform crosses zero. Higher for noise and for consonants.",
    ),
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

/* --- Note breakdown -------------------------------------------------------- */

/** One row of the note table, formatted and ready to render. */
export interface NoteRow {
  note: string;
  midi: number;
  duration: string;
  share: string;
  /** 0–100, for the bar width. The formatted share is `share`. */
  sharePercent: number;
  inTune: string;
  /** Signed mean deviation, e.g. `−4 cents`. */
  deviation: string;
  frameCount: number;
}

/**
 * Format a breakdown for display.
 *
 * Ordering is the server's — longest first, lower note first on a tie — and is
 * deliberately not re-sorted here: two sort implementations is two places for
 * the order to differ from what the API documented.
 */
export function noteRows(notes: NoteSummary[]): NoteRow[] {
  return notes.map((note) => ({
    note: note.note_name,
    midi: note.midi_note,
    duration: `${note.duration_seconds.toFixed(2)}s`,
    share: percent(note.percentage_of_voiced_time / 100),
    sharePercent: note.percentage_of_voiced_time,
    inTune: percent(note.in_tune_ratio),
    deviation: cents(note.average_cents),
    frameCount: note.frame_count,
  }));
}

/**
 * Whether there is a breakdown worth rendering.
 *
 * An empty note list is "no notes were detected", not a table with no rows.
 */
export function hasNoteBreakdown(breakdown: NoteBreakdown | null): boolean {
  return breakdown !== null && breakdown.notes.length > 0;
}

/* --- Musical key ----------------------------------------------------------- */

/**
 * The margin below which an answered key is presented as thin evidence.
 *
 * **Presentational, and only presentational.** The backend's own gate is
 * `MIN_KEY_CONFIDENCE = 0.05`: below that it reports no key at all, and nothing
 * here can change that. This second, higher line decides only whether an
 * answered key is shown with its weakness stated in words, and it is set from
 * the same sweep that set the backend constant (200 jittered draws per melody,
 * Temperley, hop 0.0232 s):
 *
 * | Input                        | Margin |
 * | ---------------------------- | ------ |
 * | Sung major melody            | 0.262  |
 * | Pentatonic melody            | 0.241  |
 * | Sung minor melody            | 0.205  |
 * | Bare unweighted major scale  | 0.146  |
 * | Backend refuses below        | 0.050  |
 *
 * 0.19 is the bottom of the band a *weighted* melody reaches, and it puts the
 * bare unweighted scale — which the specification requires be "answered, and
 * answered weakly" — on the weak side of the line. Roughly a third to a half of
 * heavily jittered melodies land below it too, which is the intended reading:
 * they are the genuinely ambiguous ones.
 *
 * It is never a second refusal. A key below this line is still shown, with its
 * tonic, its runner-up and its evidence.
 */
export const WEAK_KEY_CONFIDENCE = 0.19;

/** `G major`. The tonic and mode, and nothing implied about correctness. */
export function keyLabel(key: KeyEstimate | null): string | null {
  if (key === null) return null;
  return `${key.tonic} ${key.mode}`;
}

/**
 * Whether an answered key rests on thin evidence.
 *
 * `false` for a key that was never answered: "not measured" is its own state,
 * and calling it weak would imply a key was found.
 */
export function isWeakKey(key: KeyEstimate | null): boolean {
  return key !== null && key.confidence < WEAK_KEY_CONFIDENCE;
}

/**
 * Why no key was reported, in words.
 *
 * Every one of these describes the *recording*, never a fault. An unrecognised
 * reason falls back to the same voice rather than to an error.
 */
export const KEY_UNMEASURED_MESSAGES: Readonly<Record<KeyUnmeasuredReason, string>> = {
  TOO_FEW_PITCH_CLASSES:
    "Too few different notes were sung to tell one key from another. A held note or a short arpeggio fits many keys equally well.",
  TOO_LITTLE_VOICED_TIME:
    "There was not enough pitched singing in this recording to fold into a key.",
  AMBIGUOUS:
    "No key stood clear of the next-best one. The notes that were sung fit several keys about equally well.",
};

const KEY_UNMEASURED_FALLBACK =
  "The notes in this recording did not establish a key.";

export function keyUnmeasuredMessage(
  reason: KeyUnmeasuredReason | null | undefined,
): string {
  if (reason && reason in KEY_UNMEASURED_MESSAGES) {
    return KEY_UNMEASURED_MESSAGES[reason];
  }
  return KEY_UNMEASURED_FALLBACK;
}

/** One row of the pitch-class table, formatted and ready to render. */
export interface PitchClassRow {
  name: string;
  pitchClass: number;
  share: string;
  /** 0–100, for the bar width. The formatted share is `share`. */
  sharePercent: number;
  /** Whether this class carried enough time to count towards the evidence. */
  used: boolean;
}

/**
 * The share of pitched time each pitch class carried.
 *
 * Order is the server's — pitch-class order, C first — and is deliberately not
 * re-sorted. Unlike the note table, this one is **not** ranked by size: a key is
 * decided as much by which classes are absent as by which are present, so the
 * zeroes are rows too and moving them to the end would hide the shape.
 *
 * `used` mirrors the backend's `MIN_PITCH_CLASS_SHARE`: a class below it stays
 * in the profile and in this table, but does not count towards the
 * distinct-pitch-class evidence. One stray frame at a note boundary should not
 * make a two-note hum look like three-note music.
 */
export const MIN_PITCH_CLASS_SHARE = 2.0;

export function pitchClassRows(shares: PitchClassShare[]): PitchClassRow[] {
  return shares.map((share) => ({
    name: share.name,
    pitchClass: share.pitch_class,
    share: `${share.percentage_of_voiced_time.toFixed(1)}%`,
    sharePercent: share.percentage_of_voiced_time,
    used: share.percentage_of_voiced_time >= MIN_PITCH_CLASS_SHARE,
  }));
}

/**
 * Whether there is pitch-class evidence worth rendering.
 *
 * The server returns twelve entries whenever there was anything pitched to
 * fold, including the unused ones at zero. An empty list is "nothing was
 * folded", not twelve measured zeroes, and must not become a table of dashes.
 */
export function hasPitchClassEvidence(musicalKey: MusicalKey | null): boolean {
  return musicalKey !== null && musicalKey.pitch_classes.length > 0;
}
