/**
 * Comparison wording.
 *
 * The rules under test are the ones that turn a number into a claim:
 * a percentage-point difference must never be called a percentage, a missing
 * measurement must never render as a zero, and nothing may read as a verdict.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NOT_MEASURED,
  caveatMessage,
  describeDelta,
  formatDelta,
  formatNoteDelta,
  formatNoteShare,
  formatValue,
  isDirected,
  sideStatusLabel,
  sideStatusMessage,
} from "../lib/comparison.ts";
import type {
  ComparedMetric,
  ComparedNote,
  ComparisonSide,
  ComparisonSideStatus,
  MetricAvailability,
} from "../types/api.ts";

function metric(overrides: Partial<ComparedMetric> = {}): ComparedMetric {
  const left = overrides.left === undefined ? 50 : overrides.left;
  const right = overrides.right === undefined ? 56 : overrides.right;
  const availability: MetricAvailability =
    left !== null && right !== null
      ? "both"
      : left !== null
        ? "left_only"
        : right !== null
          ? "right_only"
          : "neither";
  return {
    key: "in_tune_ratio",
    label: "Share of pitched time within 25 cents of a note",
    unit: "percentage_points",
    direction: "higher_is_nearer_the_note",
    left,
    right,
    delta: left !== null && right !== null ? right - left : null,
    availability,
    ...overrides,
  };
}

function note(overrides: Partial<ComparedNote> = {}): ComparedNote {
  const entry = {
    duration_seconds: 2,
    percentage_of_voiced_time: 40,
    frame_count: 80,
    mean_abs_cents: 10,
    in_tune_ratio: 0.8,
  };
  return {
    midi_note: 60,
    note_name: "C4",
    presence: "both",
    left: entry,
    right: { ...entry, percentage_of_voiced_time: 55 },
    share_delta_points: 15,
    duration_delta_seconds: 0.5,
    mean_abs_cents_delta: -3,
    ...overrides,
  };
}

function side(overrides: Partial<ComparisonSide> = {}): ComparisonSide {
  return {
    recording_id: "a".repeat(32),
    status: "ready",
    original_filename: "take.wav",
    created_at: "2026-08-11T10:00:00Z",
    duration_seconds: 12,
    audio_format: "wav",
    error_code: null,
    lowest_note: "G2",
    highest_note: "C5",
    ...overrides,
  };
}

// --- Percentage points are not percent -------------------------------------

test("a ratio value renders as a percentage", () => {
  assert.equal(formatValue(metric({ left: 75 }), "left"), "75%");
});

test("a ratio delta renders as percentage points, never percent", () => {
  const formatted = formatDelta(metric({ left: 50, right: 56 }));

  assert.equal(formatted, "+6 percentage points");
  assert.ok(!/percent\b/.test(formatted ?? ""), "must not say 'percent'");
});

test("the delta sentence says percentage points too", () => {
  const sentence = describeDelta(metric({ left: 50, right: 56 }));

  assert.match(sentence, /6 percentage points higher/);
  assert.ok(!/\b6 percent\b/.test(sentence));
});

test("one percentage point is singular", () => {
  assert.equal(formatDelta(metric({ left: 50, right: 51 })), "+1 percentage point");
});

// --- Units -----------------------------------------------------------------

test("cents deltas are labelled in cents", () => {
  const cents = metric({
    key: "mean_abs_cents_deviation",
    unit: "cents",
    direction: "lower_is_nearer_the_note",
    left: 20,
    right: 12,
  });

  assert.equal(formatValue(cents, "left"), "20 cents");
  assert.equal(formatDelta(cents), "−8 cents");
});

test("semitone deltas are labelled in semitones", () => {
  const span = metric({
    key: "semitone_span",
    unit: "semitones",
    direction: "neutral",
    left: 12,
    right: 14,
  });

  assert.equal(formatValue(span, "right"), "14 semitones");
  assert.equal(formatDelta(span), "+2 semitones");
});

test("a one-semitone difference is singular", () => {
  const span = metric({ unit: "semitones", direction: "neutral", left: 12, right: 13 });

  assert.equal(formatDelta(span), "+1 semitone");
});

test("seconds deltas are labelled in seconds", () => {
  const duration = metric({
    key: "duration_seconds",
    unit: "seconds",
    direction: "neutral",
    left: 12,
    right: 24,
  });

  assert.equal(formatValue(duration, "left"), "12s");
  assert.equal(formatDelta(duration), "+12s");
});

test("a negative delta uses a minus sign, not a hyphen", () => {
  const formatted = formatDelta(metric({ left: 60, right: 50 })) ?? "";

  assert.ok(formatted.startsWith("−"), formatted);
  assert.ok(!formatted.startsWith("-"), "a hyphen would not read as arithmetic");
});

// --- Missing measurements --------------------------------------------------

test("a missing value renders as Not measured, never as zero", () => {
  const missing = metric({ right: null });

  assert.equal(formatValue(missing, "right"), NOT_MEASURED);
  assert.notEqual(formatValue(missing, "right"), "0%");
});

test("a missing value produces no delta at all", () => {
  assert.equal(formatDelta(metric({ right: null })), null);
});

test("each availability gets its own explanation", () => {
  assert.match(describeDelta(metric({ left: null, right: null })), /either recording/);
  assert.match(describeDelta(metric({ right: null })), /first recording/);
  assert.match(describeDelta(metric({ left: null })), /second recording/);
});

test("a measured zero is still a measurement", () => {
  const zeroed = metric({ left: 0, right: 20 });

  assert.equal(formatValue(zeroed, "left"), "0%");
  assert.equal(formatDelta(zeroed), "+20 percentage points");
});

test("no difference says so rather than showing a signed zero", () => {
  const same = metric({ left: 42, right: 42 });

  assert.equal(describeDelta(same), "The same in both recordings.");
  assert.equal(formatDelta(same), "0 percentage points");
  assert.ok(!(formatDelta(same) ?? "").includes("−0"));
});

// --- Nothing is a verdict --------------------------------------------------

test("no delta sentence calls a difference better or worse", () => {
  const cases: ComparedMetric[] = [
    metric({ left: 50, right: 80 }),
    metric({ left: 80, right: 50 }),
    metric({ unit: "cents", direction: "lower_is_nearer_the_note", left: 20, right: 8 }),
    metric({ unit: "cents", direction: "lower_is_steadier", left: 8, right: 20 }),
    metric({ unit: "semitones", direction: "neutral", left: 12, right: 24 }),
    metric({ unit: "seconds", direction: "neutral", left: 10, right: 60 }),
  ];

  for (const item of cases) {
    const sentence = describeDelta(item).toLowerCase();
    for (const word of ["better", "worse", "improve", "score", "grade", "rank", "ability"]) {
      assert.ok(!sentence.includes(word), `"${word}" appeared in: ${sentence}`);
    }
  }
});

test("a neutral metric is stated without interpretation", () => {
  const wider = metric({
    key: "semitone_span",
    unit: "semitones",
    direction: "neutral",
    left: 12,
    right: 24,
  });

  assert.equal(describeDelta(wider), "The second recording is 12 semitones wider.");
  assert.equal(isDirected(wider), false);
});

test("the comparative word matches the unit rather than defaulting to higher", () => {
  const longer = metric({ unit: "seconds", direction: "neutral", left: 10, right: 22 });
  const shorter = metric({ unit: "seconds", direction: "neutral", left: 22, right: 10 });
  const narrower = metric({ unit: "semitones", direction: "neutral", left: 24, right: 12 });

  assert.match(describeDelta(longer), /12s longer\.$/);
  assert.match(describeDelta(shorter), /12s shorter\.$/);
  assert.match(describeDelta(narrower), /12 semitones narrower\.$/);
});


test("a directed metric explains what its direction means", () => {
  assert.match(describeDelta(metric({ left: 50, right: 70 })), /within 25 cents of a note/);
  assert.match(
    describeDelta(
      metric({ unit: "cents", direction: "lower_is_nearer_the_note", left: 20, right: 8 }),
    ),
    /nearer the nearest note/,
  );
  assert.match(
    describeDelta(metric({ unit: "cents", direction: "lower_is_steadier", left: 8, right: 20 })),
    /spread out/,
  );
});

// --- Sides -----------------------------------------------------------------

test("every side status has its own wording", () => {
  const statuses: ComparisonSideStatus[] = [
    "ready",
    "not_found",
    "analysis_missing",
    "analysis_in_progress",
    "analysis_failed",
    "insufficient_pitch_signal",
  ];

  const messages = statuses.map((status) => sideStatusMessage(side({ status })));
  const labels = statuses.map((status) => sideStatusLabel(status));

  assert.equal(new Set(messages).size, statuses.length);
  assert.equal(new Set(labels).size, statuses.length);
});

test("no reliable pitch reads as normal, not as a broken recording", () => {
  const message = sideStatusMessage(side({ status: "insufficient_pitch_signal" }));

  assert.match(message, /normal/);
  assert.ok(!message.toLowerCase().includes("error"));
});

test("a recording that is not yours reads the same as one that does not exist", () => {
  assert.match(sideStatusMessage(side({ status: "not_found" })), /could not be found/);
  assert.ok(!sideStatusMessage(side({ status: "not_found" })).toLowerCase().includes("permission"));
});

// --- Notes -----------------------------------------------------------------

test("a note absent from one recording reads as never sung, not zero", () => {
  const absent = note({ presence: "left_only", right: null, share_delta_points: null });

  assert.equal(formatNoteShare(absent, "right"), "Not sung");
  assert.notEqual(formatNoteShare(absent, "right"), "0%");
  assert.equal(formatNoteDelta(absent), null);
});

test("a note in both recordings gets a percentage-point delta", () => {
  assert.equal(formatNoteDelta(note()), "+15 percentage points");
});

test("an unchanged note share reads as zero points, not as missing", () => {
  assert.equal(formatNoteDelta(note({ share_delta_points: 0 })), "0 percentage points");
});

// --- Caveats ---------------------------------------------------------------

test("every caveat the backend can emit has plain wording", () => {
  const emitted = [
    "different_duration",
    "different_voiced_time",
    "little_pitched_signal",
    "different_sample_rate",
    "different_audio_format",
    "different_analysis_settings",
    "clipping",
    "no_detected_range",
  ];

  for (const caveat of emitted) {
    const message = caveatMessage(caveat);
    assert.notEqual(message, caveat, `${caveat} has no wording`);
    assert.ok(message.length > 20, `${caveat} wording is too thin`);
  }
});

test("an unrecognised caveat is shown as itself rather than dropped", () => {
  assert.equal(caveatMessage("something_new"), "something_new");
});

test("no caveat claims the app can measure the room or the microphone", () => {
  for (const caveat of ["different_duration", "clipping", "different_sample_rate"]) {
    const message = caveatMessage(caveat).toLowerCase();
    assert.ok(!message.includes("microphone quality"));
    assert.ok(!message.includes("room acoustics"));
  }
});
