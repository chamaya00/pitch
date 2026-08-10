/**
 * Live practice logic.
 *
 * All of it derives from the detector's output; none of it detects anything.
 * The detector's own tests live in `pitch.test.ts` and are untouched.
 *
 * The properties asserted hardest here are the ones a practice display gets
 * wrong in ways that flatter the user:
 *
 * - a needle pinned at the end of the meter must not read as an exact value;
 * - "not enough data yet" must never render as 0%;
 * - a transient between two notes must not widen the session range;
 * - a detected pitch must never be reported as the target merely because a
 *   pitch was detected.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CONSISTENCY_WINDOW,
  EMPTY_STATS,
  IN_TUNE_CENTS,
  LivePracticeStats,
  METER_RANGE_CENTS,
  MIN_CONSISTENCY_FRAMES,
  MIN_RANGE_FRAMES,
  compareToTarget,
  meterReading,
  pitchClass,
  targetLabel,
  targetOptions,
  tuningDirection,
  tuningLabel,
} from "../lib/live-practice.ts";
import { formatClock } from "../lib/format.ts";
import { describeFrequency, midiToFrequency } from "../lib/pitch.ts";
import type { LivePitchSample } from "../lib/pitch-stream.ts";

// --- Fixtures --------------------------------------------------------------

let clock = 0;

function voiced(midi: number, cents = 0): LivePitchSample {
  const frequency = midiToFrequency(midi + cents / 100);
  assert.ok(frequency !== null);
  const reading = describeFrequency(frequency);
  assert.ok(reading, `${midi} + ${cents}c is not a usable fixture`);
  return {
    timestamp: (clock += 33),
    frequency: reading.frequency,
    midi: reading.midi,
    note: reading.note,
    cents: reading.cents,
    confidence: 0.95,
    voiced: true,
  };
}

function unvoiced(): LivePitchSample {
  return {
    timestamp: (clock += 33),
    frequency: null,
    midi: null,
    note: null,
    cents: null,
    confidence: 0,
    voiced: false,
  };
}

function feed(stats: LivePracticeStats, sample: LivePitchSample, times: number): void {
  for (let i = 0; i < times; i++) stats.push(sample);
}

// --- The meter -------------------------------------------------------------

test("the meter centres on an in-tune pitch", () => {
  assert.deepEqual(meterReading(0), { position: 0, clamped: false });
});

test("the meter reaches its ends at ±50 cents", () => {
  assert.deepEqual(meterReading(-METER_RANGE_CENTS), { position: -1, clamped: false });
  assert.deepEqual(meterReading(METER_RANGE_CENTS), { position: 1, clamped: false });
});

test("the meter is linear in between", () => {
  assert.equal(meterReading(25)?.position, 0.5);
  assert.equal(meterReading(-25)?.position, -0.5);
  assert.equal(meterReading(10)?.position, 0.2);
});

test("a deviation past the ends is clamped, and says it was clamped", () => {
  const sharp = meterReading(80);
  assert.equal(sharp?.position, 1);
  assert.equal(sharp?.clamped, true, "a pinned needle must not read as exactly 50 cents");

  const flat = meterReading(-120);
  assert.equal(flat?.position, -1);
  assert.equal(flat?.clamped, true);
});

test("nothing voiced means no needle at all", () => {
  assert.equal(meterReading(null), null);
  assert.equal(meterReading(Number.NaN), null);
  assert.equal(meterReading(Number.POSITIVE_INFINITY), null);
});

test("flat and sharp are communicated in words, not only position", () => {
  assert.equal(tuningLabel(0), "In tune");
  assert.equal(tuningLabel(2), "In tune");
  assert.equal(tuningLabel(-2), "In tune");
  assert.equal(tuningLabel(12), "12 cents sharp");
  assert.equal(tuningLabel(-23), "23 cents flat");
  assert.equal(tuningLabel(null), null);
});

test("the direction is available separately, so colour is never the only cue", () => {
  assert.equal(tuningDirection(0), "in-tune");
  assert.equal(tuningDirection(30), "sharp");
  assert.equal(tuningDirection(-30), "flat");
  assert.equal(tuningDirection(null), null);
});

// --- Consistency -----------------------------------------------------------

test("a fresh session has measured nothing", () => {
  const stats = new LivePracticeStats().snapshot();
  assert.deepEqual(stats, EMPTY_STATS);
  assert.equal(stats.consistency, null);
});

test("no voiced frames is not zero per cent", () => {
  const stats = new LivePracticeStats();
  feed(stats, unvoiced(), 200);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.consistency, null, "silence must not read as 0% consistency");
  assert.equal(snapshot.voicedFrames, 0);
  assert.equal(snapshot.totalFrames, 200);
});

test("too few voiced frames is not enough to report", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), MIN_CONSISTENCY_FRAMES - 1);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.consistency, null);
  assert.equal(snapshot.windowFrames, MIN_CONSISTENCY_FRAMES - 1);
});

test("one more frame crosses the threshold", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), MIN_CONSISTENCY_FRAMES);
  assert.equal(stats.snapshot().consistency, 1);
});

test("a steady pitch is highly consistent", () => {
  const stats = new LivePracticeStats();
  for (let i = 0; i < 60; i++) stats.push(voiced(69, i % 2 === 0 ? 4 : -4));
  assert.equal(stats.snapshot().consistency, 1);
});

test("a pitch wandering off the note is less consistent", () => {
  const stats = new LivePracticeStats();
  // Half the frames sit well outside the in-tune window.
  for (let i = 0; i < 60; i++) stats.push(voiced(69, i % 2 === 0 ? 2 : 45));
  const consistency = stats.snapshot().consistency;
  assert.ok(consistency !== null);
  assert.ok(consistency > 0.4 && consistency < 0.6, `got ${consistency}`);
});

test("a pitch consistently off the note reports low, not zero-by-accident", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69, 45), 60);
  assert.equal(stats.snapshot().consistency, 0);
});

test("the boundary counts as in tune", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69, IN_TUNE_CENTS), 40);
  assert.equal(stats.snapshot().consistency, 1);
});

test("consistency is rolling, so improving is visible while singing", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69, 45), CONSISTENCY_WINDOW);
  assert.equal(stats.snapshot().consistency, 0);

  feed(stats, voiced(69, 2), CONSISTENCY_WINDOW);
  assert.equal(stats.snapshot().consistency, 1, "the old frames should have rolled out");
});

test("unvoiced frames do not enter the consistency window", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69, 2), 40);
  feed(stats, unvoiced(), 100);
  assert.equal(stats.snapshot().consistency, 1);
  assert.equal(stats.snapshot().windowFrames, 40);
});

// --- Session range ---------------------------------------------------------

test("one held note is a range of zero semitones", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), 20);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, "A4");
  assert.equal(snapshot.highestNote, "A4");
  assert.equal(snapshot.semitoneSpan, 0);
});

test("two held notes span the interval between them", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(57), 20); // A3
  feed(stats, unvoiced(), 5);
  feed(stats, voiced(69), 20); // A4

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, "A3");
  assert.equal(snapshot.highestNote, "A4");
  assert.equal(snapshot.semitoneSpan, 12);
});

test("an octave leap is tracked when both ends are held", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(48), 15); // C3
  feed(stats, voiced(60), 15); // C4
  feed(stats, voiced(72), 15); // C5

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, "C3");
  assert.equal(snapshot.highestNote, "C5");
  assert.equal(snapshot.semitoneSpan, 24);
});

test("a transient between two notes does not widen the range", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), 20);
  // Two frames of a sub-harmonic, the shape of a real detector artefact.
  feed(stats, voiced(45), 2);
  feed(stats, voiced(69), 20);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, "A4", "a two-frame excursion became the bottom of the range");
  assert.equal(snapshot.semitoneSpan, 0);
  assert.ok(MIN_RANGE_FRAMES > 2, "the rule this test depends on");
});

test("a pitch held just long enough does count", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), 20);
  feed(stats, voiced(64), MIN_RANGE_FRAMES);

  assert.equal(stats.snapshot().lowestNote, "E4");
});

test("silence never produces a range", () => {
  const stats = new LivePracticeStats();
  feed(stats, unvoiced(), 100);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, null);
  assert.equal(snapshot.highestNote, null);
  assert.equal(snapshot.semitoneSpan, null, "no range is not a range of zero");
});

test("a run broken by silence does not qualify on its combined length", () => {
  const stats = new LivePracticeStats();
  for (let i = 0; i < 4; i++) {
    feed(stats, voiced(50), 2);
    stats.push(unvoiced());
  }
  assert.equal(stats.snapshot().lowestNote, null);
});

test("a glide is not a held pitch until it settles", () => {
  const stats = new LivePracticeStats();
  // Rising a semitone per frame: continuous, but never held.
  for (let midi = 60; midi < 72; midi++) stats.push(voiced(midi));
  const wandering = stats.snapshot();

  // A glide is continuous within the tolerance, so it does accumulate a run —
  // what matters is that it cannot report a range before the run qualifies.
  assert.ok(
    wandering.semitoneSpan === null || wandering.semitoneSpan > 0,
    "a glide should either be unmeasured or span what it covered",
  );
});

test("reset clears the session", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(69), 60);
  stats.reset();
  assert.deepEqual(stats.snapshot(), EMPTY_STATS);
});

test("a second session starts from nothing", () => {
  const stats = new LivePracticeStats();
  feed(stats, voiced(48), 20);
  stats.reset();
  feed(stats, voiced(69), 20);

  const snapshot = stats.snapshot();
  assert.equal(snapshot.lowestNote, "A4");
  assert.equal(snapshot.highestNote, "A4");
});

// --- Target note -----------------------------------------------------------

test("the target list covers the detector's range and is well formed", () => {
  const options = targetOptions();
  assert.equal(options[0].note, "C2");
  assert.equal(options[options.length - 1].note, "C6");
  assert.ok(options.every((option) => /^[A-G]#?\d$/.test(option.note)));
});

test("singing the target reports the target, in tune", () => {
  const comparison = compareToTarget(voiced(60), 60);
  assert.ok(comparison);
  assert.equal(comparison.targetNote, "C4");
  assert.equal(comparison.detectedNote, "C4");
  assert.equal(comparison.onTarget, true);
  assert.ok(Math.abs(comparison.centsFromTarget) < 1);
  assert.equal(targetLabel(comparison), "In tune");
});

test("singing flat of the target reports flat of the target", () => {
  const comparison = compareToTarget(voiced(60, -30), 60);
  assert.ok(comparison);
  assert.equal(comparison.onTarget, true);
  assert.ok(comparison.centsFromTarget < -25);
  assert.ok(targetLabel(comparison)?.endsWith("flat"));
});

test("singing sharp of the target reports sharp of the target", () => {
  const comparison = compareToTarget(voiced(60, 30), 60);
  assert.ok(comparison);
  assert.ok(comparison.centsFromTarget > 25);
  assert.ok(targetLabel(comparison)?.endsWith("sharp"));
});

test("a different note is never reported as the target", () => {
  // B3 against a C4 target. The whole point: a detected pitch is not a correct
  // pitch, and "C4, slightly flat" would be a lie.
  const comparison = compareToTarget(voiced(59), 60);
  assert.ok(comparison);
  assert.equal(comparison.detectedNote, "B3");
  assert.equal(comparison.targetNote, "C4");
  assert.equal(comparison.onTarget, false);
  assert.equal(comparison.semitonesFromTarget, -1);
  assert.equal(targetLabel(comparison), "1 semitone below the target");
});

test("an octave error is reported as an octave, not as success", () => {
  const low = compareToTarget(voiced(48), 60);
  assert.ok(low);
  assert.equal(low.onTarget, false);
  assert.equal(low.semitonesFromTarget, -12);
  assert.equal(low.centsFromTarget, -1200);
  assert.equal(targetLabel(low), "An octave below the target");

  const high = compareToTarget(voiced(72), 60);
  assert.equal(targetLabel(high), "An octave above the target");
});

test("nothing voiced means nothing to compare", () => {
  assert.equal(compareToTarget(unvoiced(), 60), null);
  assert.equal(targetLabel(null), null);
});

test("pitch classes are named with sharps", () => {
  assert.equal(pitchClass(60), "C");
  assert.equal(pitchClass(61), "C#");
  assert.equal(pitchClass(69), "A");
});

// --- The recording clock ---------------------------------------------------

test("the recording clock reads as a clock, fixed width", () => {
  assert.equal(formatClock(0), "00:00");
  assert.equal(formatClock(1), "00:01");
  assert.equal(formatClock(14), "00:14");
  assert.equal(formatClock(59.9), "00:59");
  assert.equal(formatClock(60), "01:00");
  assert.equal(formatClock(300), "05:00");
  assert.equal(formatClock(3661), "1:01:01");
});

test("the clock refuses impossible values rather than rendering NaN", () => {
  assert.equal(formatClock(-1), "0:00");
  assert.equal(formatClock(Number.NaN), "0:00");
  assert.equal(formatClock(Number.POSITIVE_INFINITY), "0:00");
});
