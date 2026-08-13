import type {
  HistoryDepth,
  MetricUnit,
  ProgressPoint,
  ProgressSeries,
} from "@/types/api";

/**
 * Turning a progress history into words and shapes.
 *
 * Pure, and kept out of the components, because this is where a series of
 * numbers becomes a claim about a person:
 *
 * **Nothing here says "improved".** The strongest sentence available is that a
 * number was higher or lower in the most recent recording, and for the four
 * neutral series it does not even say that much beyond describing the change.
 *
 * **A gap is a gap.** `null` never becomes zero, in the wording or in the
 * geometry: the polyline is built as separate segments so an unmeasured
 * recording leaves a hole rather than a dive to the axis.
 *
 * **Two points are not a trend**, and `depthMessage` says so rather than
 * letting a line between two dots imply otherwise.
 */

/** The wording used wherever a measurement is absent. Matches the analysis UI. */
export const NOT_MEASURED = "Not measured";

/** Minus sign (U+2212), not a hyphen: it aligns and reads as arithmetic. */
const MINUS = "−";

/** How many decimals a unit is worth showing. */
const DIGITS: Record<MetricUnit, number> = {
  percentage_points: 1,
  cents: 1,
  semitones: 0,
  seconds: 1,
};

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  // `+ 0` normalises -0, which would otherwise render as "−0".
  return Math.round(value * factor) / factor + 0;
}

/**
 * One point's value, in the series' display unit.
 *
 * A `percentage_points` series holds percentages; the unit names what a
 * *difference* is measured in, which is the distinction that keeps six
 * percentage points from being called six percent.
 */
export function formatValue(value: number | null, unit: MetricUnit): string {
  if (value === null) return NOT_MEASURED;
  const digits = DIGITS[unit];
  const rounded = round(value, digits);
  switch (unit) {
    case "percentage_points":
      return `${rounded}%`;
    case "cents":
      return `${rounded} cents`;
    case "semitones":
      return `${rounded} semitone${rounded === 1 ? "" : "s"}`;
    case "seconds":
      return `${rounded}s`;
  }
}

/** The axis label for a series — what the numbers on it are. */
export function axisLabel(unit: MetricUnit): string {
  switch (unit) {
    case "percentage_points":
      return "percent";
    case "cents":
      return "cents";
    case "semitones":
      return "semitones";
    case "seconds":
      return "seconds";
  }
}

/** A signed difference with its unit spelled out. */
export function formatDelta(delta: number, unit: MetricUnit): string {
  const digits = DIGITS[unit];
  const rounded = round(delta, digits);
  const magnitude = Math.abs(rounded);
  const signed =
    rounded === 0 ? "0" : rounded > 0 ? `+${rounded}` : `${MINUS}${magnitude}`;

  switch (unit) {
    case "percentage_points":
      return `${signed} percentage point${magnitude === 1 ? "" : "s"}`;
    case "cents":
      return `${signed} cent${magnitude === 1 ? "" : "s"}`;
    case "semitones":
      return `${signed} semitone${magnitude === 1 ? "" : "s"}`;
    case "seconds":
      return `${signed}s`;
  }
}

/** Why a point has no value. Absent for a measured one. */
export function pointStatusMessage(point: ProgressPoint): string | null {
  switch (point.status) {
    case "measured":
      return null;
    case "not_measured":
      // The analysis ran. This particular measurement was not available from
      // it — which is not a failure of the recording.
      return "This recording was measured, but not for this.";
    case "not_eligible":
      return "This recording has no completed audio analysis.";
  }
}

/**
 * A neutral sentence about the most recent measurement.
 *
 * Never a verdict. A directed series adds what its direction means so a reader
 * learns the definition alongside the number; a neutral series states the change
 * and stops, because a wider range or a longer recording is not an achievement
 * and a clause here would imply it was.
 */
export function observationMessage(series: ProgressSeries): string {
  const { observation, unit, direction } = series;

  if (observation.direction === "insufficient_data") {
    return series.measured_count === 0
      ? "Nothing has been measured for this yet."
      : "Record another session to see how this changes.";
  }
  if (observation.direction === "unchanged" || observation.delta === null) {
    return "Your latest recording measured the same as the one before it.";
  }

  const change = observation.direction === "higher" ? "higher" : "lower";
  const amount = formatDelta(observation.delta, unit).replace(/^[+−]/, "");
  const sentence = `Your latest recording was ${amount} ${change} than the one before it.`;

  const higher = observation.direction === "higher";
  switch (direction) {
    case "higher_is_nearer_the_note":
      return `${sentence} ${
        higher ? "More" : "Less"
      } of its pitched time sat within 25 cents of a note.`;
    case "lower_is_nearer_the_note":
      return `${sentence} Its pitch sat ${
        higher ? "further from" : "nearer"
      } the nearest note on average.`;
    case "lower_is_steadier":
      return `${sentence} Its pitch placement was ${
        higher ? "more" : "less"
      } spread out.`;
    case "neutral":
      // Deliberately nothing further. This is a difference in what was
      // recorded, not an improvement.
      return sentence;
  }
}

/** What may honestly be shown for the amount of history that exists. */
export function depthMessage(depth: HistoryDepth): string {
  switch (depth) {
    case "empty":
      return "Once you have measured a recording, its numbers will appear here.";
    case "single":
      return "One measured recording so far. Record another session to see how these numbers change.";
    case "pair":
      return "Two measured recordings. That is enough to see a change between them, but not enough to call it a trend.";
    case "series":
      return "";
  }
}

/** A short date for an axis tick or a table row, in the reader's locale. */
export function formatPointDate(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Unknown date";
  return when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The full date and time, for the table where there is room for it. */
export function formatPointDateTime(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Unknown date";
  return when.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* --- Chart geometry -------------------------------------------------------- */

export interface ChartGeometry {
  /** Every point that carries a value, in x/y viewBox coordinates. */
  readonly dots: readonly { x: number; y: number; point: ProgressPoint }[];
  /**
   * Polyline segments. **Separate segments, not one line with holes**, so an
   * unmeasured recording leaves a visible gap instead of a stroke drawn
   * straight through it as if the value were interpolated.
   */
  readonly segments: readonly (readonly { x: number; y: number }[])[];
  readonly min: number;
  readonly max: number;
}

/** viewBox is fixed; the SVG scales with its container. */
export const CHART_WIDTH = 640;
export const CHART_HEIGHT = 200;

/**
 * Lay out a series inside the chart's coordinate space.
 *
 * The y-range is padded and always includes the data; a series whose values are
 * all identical gets an artificial span so the line renders in the middle
 * rather than dividing by zero. The x-axis is the point *index*, not elapsed
 * time: the points are practice sessions, and spacing them by wall-clock date
 * would squash a month of daily practice against one recording from last year.
 * The table carries the actual dates.
 */
export function chartGeometry(points: readonly ProgressPoint[]): ChartGeometry {
  const measured = points
    .map((point, index) => ({ point, index }))
    .filter((entry) => entry.point.value !== null);

  if (measured.length === 0) {
    return { dots: [], segments: [], min: 0, max: 1 };
  }

  const values = measured.map((entry) => entry.point.value as number);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    // A flat series still deserves a line. Without this the scale collapses.
    min -= 1;
    max += 1;
  }
  const span = max - min;
  const lastIndex = Math.max(points.length - 1, 1);

  const dots = measured.map((entry) => ({
    x: (entry.index / lastIndex) * CHART_WIDTH,
    y: CHART_HEIGHT - ((entry.point.value as number) - min) / span * CHART_HEIGHT,
    point: entry.point,
  }));

  // Break the line wherever consecutive measured points are not adjacent in
  // the series — that is where a gap belongs.
  const segments: { x: number; y: number }[][] = [];
  let current: { x: number; y: number }[] = [];
  let previousIndex: number | null = null;

  measured.forEach((entry, position) => {
    const dot = dots[position];
    if (previousIndex !== null && entry.index !== previousIndex + 1) {
      if (current.length > 0) segments.push(current);
      current = [];
    }
    current.push({ x: dot.x, y: dot.y });
    previousIndex = entry.index;
  });
  if (current.length > 0) segments.push(current);

  return {
    dots,
    // A one-point segment draws nothing as a polyline; the dot still renders.
    segments: segments.filter((segment) => segment.length > 1),
    min,
    max,
  };
}
