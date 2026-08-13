import type {
  ComparedMetric,
  ComparedNote,
  ComparisonSide,
  ComparisonSideStatus,
  MetricUnit,
} from "@/types/api";

/**
 * Turning a comparison into words.
 *
 * Pure, and kept out of the components, because this is where a comparison
 * becomes a claim and the claims have to be exactly right:
 *
 * **A value and its delta have different units.** `in_tune_ratio` is shown as
 * `75%` but its difference is `+6 percentage points`. Calling that difference
 * "6 percent" is the most common numerical lie in software, and the two
 * formatters below are deliberately separate so it cannot happen by reuse.
 *
 * **Nothing is "better".** Four of the seven metrics are `neutral` and read as a
 * plain difference. The three that are directed say what the direction means —
 * nearer the nearest note, or steadier — and never that the singing improved.
 *
 * **`null` is never zero.** A missing measurement renders as "Not measured",
 * the same wording the single-recording analysis already uses, and produces no
 * delta sentence at all.
 */

/** The wording used everywhere a measurement is absent. Matches the analysis UI. */
export const NOT_MEASURED = "Not measured";

/** Minus sign (U+2212), not a hyphen: it aligns and reads as arithmetic. */
const MINUS = "−";

function signed(value: number, digits: number): string {
  const rounded = round(value, digits);
  if (rounded === 0) return "0";
  return rounded > 0 ? `+${rounded}` : `${MINUS}${Math.abs(rounded)}`;
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  // `+ 0` normalises -0, which would otherwise render as "−0".
  return Math.round(value * factor) / factor + 0;
}

/**
 * The comparative word for each unit: [more, less].
 *
 * Chosen so the sentence reads naturally without becoming a judgement.
 * "12 seconds longer" and "2 semitones wider" describe what was recorded;
 * neither says the recording was any good, and the neutral metrics add no
 * clause after them.
 */
const COMPARATIVES: Record<MetricUnit, readonly [string, string]> = {
  percentage_points: ["higher", "lower"],
  cents: ["higher", "lower"],
  semitones: ["wider", "narrower"],
  seconds: ["longer", "shorter"],
};

/** How many decimals a unit is worth showing. */
const DIGITS: Record<MetricUnit, number> = {
  percentage_points: 1,
  cents: 1,
  semitones: 0,
  seconds: 1,
};

/**
 * One recording's value for a metric, in the metric's own display unit.
 *
 * Note that a `percentage_points` metric's *value* is a percentage — the unit
 * names what the **difference** is measured in, which is the whole distinction.
 */
export function formatValue(metric: ComparedMetric, side: "left" | "right"): string {
  const value = side === "left" ? metric.left : metric.right;
  if (value === null) return NOT_MEASURED;

  const digits = DIGITS[metric.unit];
  switch (metric.unit) {
    case "percentage_points":
      return `${round(value, digits)}%`;
    case "cents":
      return `${round(value, digits)} cents`;
    case "semitones":
      return `${round(value, digits)} semitone${round(value, digits) === 1 ? "" : "s"}`;
    case "seconds":
      return `${round(value, digits)}s`;
  }
}

/** The difference, with its unit spelled out. `null` when there is no delta. */
export function formatDelta(metric: ComparedMetric): string | null {
  if (metric.delta === null) return null;

  const digits = DIGITS[metric.unit];
  const magnitude = Math.abs(round(metric.delta, digits));
  const value = signed(metric.delta, digits);

  switch (metric.unit) {
    case "percentage_points":
      return `${value} percentage point${magnitude === 1 ? "" : "s"}`;
    case "cents":
      return `${value} cent${magnitude === 1 ? "" : "s"}`;
    case "semitones":
      return `${value} semitone${magnitude === 1 ? "" : "s"}`;
    case "seconds":
      return `${value}s`;
  }
}

/**
 * A sentence describing the difference, phrased neutrally.
 *
 * "6 percentage points higher", never "6 points better". A directed metric adds
 * what its direction means, so a reader learns the definition alongside the
 * number rather than inferring a verdict from it.
 */
export function describeDelta(metric: ComparedMetric): string {
  if (metric.delta === null) {
    switch (metric.availability) {
      case "neither":
        return "Not measured in either recording.";
      case "left_only":
        return "Only measured in the first recording, so there is nothing to compare.";
      case "right_only":
        return "Only measured in the second recording, so there is nothing to compare.";
      default:
        return "Not comparable.";
    }
  }

  const formatted = formatDelta(metric);
  if (formatted === null || round(metric.delta, DIGITS[metric.unit]) === 0) {
    return "The same in both recordings.";
  }

  const higher = metric.delta > 0;
  const change = COMPARATIVES[metric.unit][higher ? 0 : 1];
  const sentence = `The second recording is ${stripSign(formatted)} ${change}.`;

  switch (metric.direction) {
    case "higher_is_nearer_the_note":
      return `${sentence} More of its pitched time sat within 25 cents of a note.`;
    case "lower_is_nearer_the_note":
      return `${sentence} ${
        higher ? "Its pitch sat further from" : "Its pitch sat nearer"
      } the nearest note on average.`;
    case "lower_is_steadier":
      return `${sentence} Its pitch placement was ${higher ? "more" : "less"} spread out.`;
    case "neutral":
      // Deliberately no interpretation. A longer recording, more pitched time
      // or a wider detected range are differences in what was recorded, not
      // improvements, and adding a clause here would imply otherwise.
      return sentence;
  }
}

function stripSign(formatted: string): string {
  return formatted.replace(/^[+−]/, "");
}

/** Whether a metric's direction has a defined desirable end at all. */
export function isDirected(metric: ComparedMetric): boolean {
  return metric.direction !== "neutral";
}

/** Why one side cannot take part, in words the reader can act on. */
export function sideStatusMessage(side: ComparisonSide): string {
  switch (side.status) {
    case "ready":
      return "Measured and ready to compare.";
    case "not_found":
      // Deliberately one message for "does not exist" and "not yours" — the
      // server does not distinguish them either.
      return "This recording could not be found.";
    case "analysis_missing":
      return "This recording has not been measured yet. Open it and choose “Measure the audio”.";
    case "analysis_in_progress":
      return "This recording is still being measured. Try again in a moment.";
    case "insufficient_pitch_signal":
      return "There was not enough reliable pitch in this recording to measure. That is normal for speech, a whisper or a noisy room.";
    case "analysis_failed":
      return "This recording's audio could not be measured.";
  }
}

/** A short label for a side's state, for the column heading. */
export function sideStatusLabel(status: ComparisonSideStatus): string {
  switch (status) {
    case "ready":
      return "Measured";
    case "not_found":
      return "Not found";
    case "analysis_missing":
      return "Not measured";
    case "analysis_in_progress":
      return "Measuring";
    case "insufficient_pitch_signal":
      return "No reliable pitch";
    case "analysis_failed":
      return "Could not be measured";
  }
}

/**
 * Plain-language wording for each measurable caveat.
 *
 * These are the differences the system can actually detect. The standing
 * caveat — that two recordings are only meaningfully comparable when captured
 * under reasonably similar conditions — is not in this list because it is not
 * conditional, and the panel states it every time.
 */
const CAVEATS: Record<string, string> = {
  different_duration:
    "The recordings are very different lengths, so they contain different amounts of singing.",
  different_voiced_time:
    "One recording contains much more pitched audio than the other, so its figures rest on more evidence.",
  little_pitched_signal:
    "At least one recording carried a pitch for only a small share of its length, so its figures rest on very little.",
  different_sample_rate:
    "The recordings were captured at different sample rates, so they were not measured identically.",
  different_audio_format:
    "One recording is compressed and the other is not. Lossy compression changes what the analysis saw.",
  different_analysis_settings:
    "The two analyses ran with different settings, so their numbers are not directly equivalent.",
  clipping:
    "At least one recording clipped, which distorts the signal the analysis measured.",
  no_detected_range:
    "At least one recording had no pitch held long enough to establish a range, so the range comparison is unavailable.",
};

export function caveatMessage(caveat: string): string {
  return CAVEATS[caveat] ?? caveat;
}

/** A note's figure for one side, or the "never sung" wording. */
export function formatNoteShare(note: ComparedNote, side: "left" | "right"): string {
  const entry = side === "left" ? note.left : note.right;
  // Absent means the note was never sung. Rendering 0% would say it was sung
  // for no time, which is a different — and false — statement.
  if (entry === null) return "Not sung";
  return `${round(entry.percentage_of_voiced_time, 1)}%`;
}

export function formatNoteDelta(note: ComparedNote): string | null {
  if (note.share_delta_points === null) return null;
  const rounded = round(note.share_delta_points, 1);
  if (rounded === 0) return "0 percentage points";
  return `${signed(note.share_delta_points, 1)} percentage point${
    Math.abs(rounded) === 1 ? "" : "s"
  }`;
}
