/**
 * Progress wording and chart geometry.
 *
 * The rules under test are the ones that turn a line into a claim: no verdict
 * language, a null that never becomes a zero in either the words or the
 * geometry, and two points that are never called a trend.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHART_HEIGHT,
  NOT_MEASURED,
  axisLabel,
  chartGeometry,
  depthMessage,
  formatDelta,
  formatPointDate,
  formatPointDateTime,
  formatValue,
  observationMessage,
  pointStatusMessage,
} from "../lib/progress.ts";
import type {
  HistoryDepth,
  MetricUnit,
  ProgressPoint,
  ProgressSeries,
} from "../types/api.ts";

function point(value: number | null, index = 0): ProgressPoint {
  return {
    recording_id: `${index}`.padStart(32, "0"),
    recorded_at: `2026-08-0${index + 1}T10:00:00Z`,
    status: value === null ? "not_measured" : "measured",
    value,
  };
}

function series(overrides: Partial<ProgressSeries> = {}): ProgressSeries {
  const points = overrides.points ?? [point(50, 0), point(56, 1)];
  const measured = points.filter((p) => p.value !== null);
  return {
    metric: "in_tune_ratio",
    label: "Share of pitched time within 25 cents of a note",
    unit: "percentage_points",
    direction: "higher_is_nearer_the_note",
    points,
    measured_count: measured.length,
    observation:
      measured.length >= 2
        ? {
            direction:
              (measured.at(-1)!.value as number) > (measured.at(-2)!.value as number)
                ? "higher"
                : (measured.at(-1)!.value as number) < (measured.at(-2)!.value as number)
                  ? "lower"
                  : "unchanged",
            latest: measured.at(-1)!.value,
            previous: measured.at(-2)!.value,
            delta: (measured.at(-1)!.value as number) - (measured.at(-2)!.value as number),
            latest_recording_id: measured.at(-1)!.recording_id,
            previous_recording_id: measured.at(-2)!.recording_id,
          }
        : {
            direction: "insufficient_data",
            latest: null,
            previous: null,
            delta: null,
            latest_recording_id: null,
            previous_recording_id: null,
          },
    ...overrides,
  };
}

// --- Units -----------------------------------------------------------------

test("a percentage series renders values as percentages", () => {
  assert.equal(formatValue(74.2, "percentage_points"), "74.2%");
});

test("a difference in a percentage series is percentage points, never percent", () => {
  const formatted = formatDelta(6, "percentage_points");

  assert.equal(formatted, "+6 percentage points");
  assert.ok(!/percent\b/.test(formatted));
});

test("one percentage point is singular", () => {
  assert.equal(formatDelta(1, "percentage_points"), "+1 percentage point");
});

test("each unit renders and labels its axis distinctly", () => {
  const units: MetricUnit[] = ["percentage_points", "cents", "semitones", "seconds"];
  const values = units.map((unit) => formatValue(12, unit));
  const labels = units.map(axisLabel);

  assert.equal(new Set(values).size, units.length);
  assert.equal(new Set(labels).size, units.length);
});

test("a negative delta uses a minus sign, not a hyphen", () => {
  assert.ok(formatDelta(-8, "cents").startsWith("−"));
  assert.ok(!formatDelta(-8, "cents").startsWith("-"));
});

test("a zero delta does not render as a signed zero", () => {
  assert.equal(formatDelta(0, "cents"), "0 cents");
  assert.ok(!formatDelta(0, "cents").includes("−0"));
});

// --- Null is never zero ----------------------------------------------------

test("a null value renders as Not measured, never as zero", () => {
  assert.equal(formatValue(null, "percentage_points"), NOT_MEASURED);
  assert.notEqual(formatValue(null, "percentage_points"), "0%");
});

test("a measured zero renders as zero", () => {
  assert.equal(formatValue(0, "percentage_points"), "0%");
  assert.equal(formatValue(0, "cents"), "0 cents");
});

test("the two absence states get different explanations", () => {
  const notMeasured = pointStatusMessage({ ...point(null), status: "not_measured" });
  const notEligible = pointStatusMessage({ ...point(null), status: "not_eligible" });

  assert.notEqual(notMeasured, notEligible);
  assert.match(notEligible ?? "", /no completed audio analysis/);
});

test("a measured point needs no explanation", () => {
  assert.equal(pointStatusMessage(point(50)), null);
});

// --- Observation wording ---------------------------------------------------

test("no observation calls a change an improvement", () => {
  const cases: ProgressSeries[] = [
    series({ points: [point(40, 0), point(80, 1)] }),
    series({ points: [point(80, 0), point(40, 1)] }),
    series({
      unit: "cents",
      direction: "lower_is_nearer_the_note",
      points: [point(20, 0), point(8, 1)],
    }),
    series({
      unit: "cents",
      direction: "lower_is_steadier",
      points: [point(8, 0), point(20, 1)],
    }),
    series({
      unit: "semitones",
      direction: "neutral",
      points: [point(12, 0), point(24, 1)],
    }),
  ];

  for (const item of cases) {
    const message = observationMessage(item).toLowerCase();
    for (const word of ["improve", "better", "worse", "score", "grade", "level", "skill", "progress"]) {
      assert.ok(!message.includes(word), `"${word}" appeared in: ${message}`);
    }
  }
});

test("a neutral series states the change without interpreting it", () => {
  const wider = series({
    metric: "semitone_span",
    unit: "semitones",
    direction: "neutral",
    points: [point(12, 0), point(24, 1)],
  });

  assert.equal(
    observationMessage(wider),
    "Your latest recording was 12 semitones higher than the one before it.",
  );
});

test("a directed series explains what its direction means", () => {
  assert.match(
    observationMessage(series({ points: [point(50, 0), point(70, 1)] })),
    /within 25 cents of a note/,
  );
  assert.match(
    observationMessage(
      series({
        unit: "cents",
        direction: "lower_is_nearer_the_note",
        points: [point(20, 0), point(8, 1)],
      }),
    ),
    /nearer the nearest note/,
  );
  assert.match(
    observationMessage(
      series({
        unit: "cents",
        direction: "lower_is_steadier",
        points: [point(8, 0), point(20, 1)],
      }),
    ),
    /spread out/,
  );
});

test("an unchanged pair says so rather than reporting a zero change", () => {
  assert.match(
    observationMessage(series({ points: [point(50, 0), point(50, 1)] })),
    /measured the same/,
  );
});

test("one measurement asks for another rather than claiming no change", () => {
  const message = observationMessage(series({ points: [point(50, 0)] }));

  assert.match(message, /Record another session/);
  assert.ok(!message.includes("same"));
});

test("nothing measured says nothing was measured", () => {
  assert.match(
    observationMessage(series({ points: [point(null, 0)] })),
    /Nothing has been measured/,
  );
});

// --- Minimum history -------------------------------------------------------

test("two points are explicitly not called a trend", () => {
  const message = depthMessage("pair");

  assert.match(message, /not enough to call it a trend/);
});

test("each depth says something different, and a full series says nothing extra", () => {
  const depths: HistoryDepth[] = ["empty", "single", "pair", "series"];
  const messages = depths.map(depthMessage);

  assert.equal(new Set(messages).size, depths.length);
  assert.equal(depthMessage("series"), "");
  assert.match(depthMessage("single"), /Record another session/);
});

// --- Chart geometry --------------------------------------------------------

test("an empty series produces no geometry rather than throwing", () => {
  const geometry = chartGeometry([]);

  assert.deepEqual(geometry.dots, []);
  assert.deepEqual(geometry.segments, []);
});

test("every measured point becomes a dot", () => {
  const geometry = chartGeometry([point(10, 0), point(20, 1), point(30, 2)]);

  assert.equal(geometry.dots.length, 3);
  assert.equal(geometry.segments.length, 1);
  assert.equal(geometry.segments[0].length, 3);
});

test("a null point creates a gap, not a dive to the axis", () => {
  const geometry = chartGeometry([point(10, 0), point(null, 1), point(30, 2)]);

  assert.equal(geometry.dots.length, 2, "the unmeasured point is not drawn");
  assert.equal(geometry.segments.length, 0, "and the line does not cross the gap");
});

test("a gap splits the line into separate segments", () => {
  const geometry = chartGeometry([
    point(10, 0),
    point(12, 1),
    point(null, 2),
    point(30, 3),
    point(32, 4),
  ]);

  assert.equal(geometry.segments.length, 2);
  assert.equal(geometry.dots.length, 4);
});

test("no dot is ever placed at the zero of the axis because of a null", () => {
  const geometry = chartGeometry([point(50, 0), point(null, 1), point(50, 2)]);

  // Both real values are equal, so both sit mid-chart; nothing sits at the
  // bottom edge, which is where a null-as-zero would land.
  for (const dot of geometry.dots) {
    assert.notEqual(dot.y, CHART_HEIGHT);
  }
});

test("higher values sit higher on the chart", () => {
  const geometry = chartGeometry([point(10, 0), point(90, 1)]);

  assert.ok(geometry.dots[1].y < geometry.dots[0].y, "y grows downwards in SVG");
});

test("a flat series still renders a line rather than dividing by zero", () => {
  const geometry = chartGeometry([point(42, 0), point(42, 1), point(42, 2)]);

  assert.equal(geometry.dots.length, 3);
  assert.ok(geometry.dots.every((dot) => Number.isFinite(dot.y)));
  assert.ok(geometry.max > geometry.min);
});

test("a single measured point draws a dot and no line", () => {
  const geometry = chartGeometry([point(42, 0)]);

  assert.equal(geometry.dots.length, 1);
  assert.equal(geometry.segments.length, 0);
});

test("a measured zero is plotted, not treated as missing", () => {
  const geometry = chartGeometry([point(0, 0), point(10, 1)]);

  assert.equal(geometry.dots.length, 2);
  assert.equal(geometry.dots[0].point.value, 0);
});

test("geometry is deterministic", () => {
  const points = [point(10, 0), point(null, 1), point(30, 2)];

  assert.deepEqual(chartGeometry(points), chartGeometry(points));
});

// --- Dates -----------------------------------------------------------------

test("dates render as something readable, not as the raw string", () => {
  assert.notEqual(formatPointDate("2026-08-11T10:30:00Z"), "2026-08-11T10:30:00Z");
  assert.match(formatPointDateTime("2026-08-11T10:30:00Z"), /2026/);
});

test("an unparseable date says so rather than rendering NaN", () => {
  assert.equal(formatPointDate("not a date"), "Unknown date");
  assert.equal(formatPointDateTime(""), "Unknown date");
});
