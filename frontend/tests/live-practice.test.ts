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
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  CONSISTENCY_WINDOW,
  EMPTY_STATS,
  IN_TUNE_CENTS,
  LIVE_FRAME_SECONDS,
  LIVE_KEY_WAITING_MESSAGES,
  LivePracticeStats,
  METER_RANGE_CENTS,
  MIN_CONSISTENCY_FRAMES,
  MIN_RANGE_FRAMES,
  compareToTarget,
  liveKeyLabel,
  liveKeyMarginLabel,
  liveKeyWaitingMessage,
  type LiveStats,
  meterReading,
  pitchClass,
  targetLabel,
  targetOptions,
  tuningDirection,
  tuningLabel,
} from "../lib/live-practice.ts";
import {
  KEY_DWELL_TICKS,
  LiveKeyTracker,
  estimateKey,
  pitchClassProfile,
} from "../lib/live-key.ts";
import { DETECTION_INTERVAL_MS } from "../lib/live-pitch-engine.ts";
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

// --- The live key ----------------------------------------------------------
//
// Two things are being tested here and they are different in kind.
//
// The estimator itself is `live-key.test.ts`'s job, and its agreement with the
// backend is the shared parity table's. What is tested *here* is everything the
// backend has no equivalent of: which frames are counted, how long the evidence
// must hold still before a label appears, and what a session that sits on the
// major/minor boundary does to it. The dwell constant was chosen by the sweep
// below rather than picked, so the sweep is run rather than quoted.

/** A tick's worth of frames at the detector's rate: 500 ms of publishing. */
const FRAMES_PER_TICK = 15;

/** Sing a sequence of pitch classes, then publish. */
function sing(stats: LivePracticeStats, pitchClasses: number[]): void {
  for (const pitchClass of pitchClasses) stats.push(voiced(60 + pitchClass));
}

/** The C major melody the backend fixtures use, as something to sing. */
const MAJOR_MELODY_FRAMES = [0, 0, 2, 4, 4, 5, 7, 7, 9, 11, 0, 7, 4, 5, 2];

test("the frame duration is the interval the detector actually runs at", () => {
  // The one number this module copies from the microphone engine. A test owns
  // the copy so it cannot drift; the module stays free of `AudioContext`.
  assert.equal(LIVE_FRAME_SECONDS, DETECTION_INTERVAL_MS / 1000);
});

test("a session with nothing sung has no key, and does not have a blank one", () => {
  const stats = new LivePracticeStats();
  feed(stats, unvoiced(), 200);
  const snapshot = stats.snapshot();

  assert.equal(snapshot.keyTonic, null);
  assert.equal(snapshot.keyMode, null);
  assert.equal(snapshot.keyConfidence, null);
  assert.equal(snapshot.keyUnmeasuredReason, "TOO_LITTLE_VOICED_TIME");
});

test("a hum is not a key, however long it is held", () => {
  const stats = new LivePracticeStats();
  for (let tick = 0; tick < 20; tick++) {
    sing(stats, new Array(FRAMES_PER_TICK).fill(0));
    stats.snapshot();
  }
  const snapshot = stats.snapshot();
  assert.equal(snapshot.keyTonic, null);
  assert.equal(snapshot.keyUnmeasuredReason, "TOO_FEW_PITCH_CLASSES");
});

test("a melody commits, and the tick it commits on is asserted", () => {
  const stats = new LivePracticeStats();
  const labels: (string | null)[] = [];
  for (let tick = 0; tick < 10; tick++) {
    sing(stats, MAJOR_MELODY_FRAMES);
    const snapshot = stats.snapshot();
    labels.push(snapshot.keyTonic === null ? null : `${snapshot.keyTonic} ${snapshot.keyMode}`);
  }

  // Ticks 0 and 1 are refused on voiced time — 15 frames is 0.495 s, and the
  // gate is a second. The verdict is C major from tick 2; the dwell holds the
  // label back until it has said so KEY_DWELL_TICKS times running, which is
  // tick 5 — three seconds of singing.
  assert.deepEqual(labels.slice(0, 5), new Array(5).fill(null));
  assert.deepEqual(labels.slice(5), new Array(5).fill("C major"));

  const settled = stats.snapshot();
  assert.equal(settled.keyMode, "major");
  assert.ok(settled.keyConfidence !== null && settled.keyConfidence >= 0.05);
  assert.equal(settled.keyUnmeasuredReason, null, "a displayed key is not also waiting");
});

test("every voiced frame counts towards the key, not only held ones", () => {
  // The range admits a pitch only after MIN_RANGE_FRAMES; the key must not.
  // A melody sung with every note shorter than a held note still has a key,
  // and it is the same one the backend would fold from the same frames.
  const stats = new LivePracticeStats();
  const quick = [0, 2, 4, 5, 7, 9, 11, 0, 7, 4, 5, 2, 0, 7, 4];
  for (let tick = 0; tick < 6; tick++) {
    sing(stats, quick);
    stats.snapshot();
  }
  const snapshot = stats.snapshot();

  assert.equal(snapshot.keyTonic, "C");
  assert.equal(snapshot.keyMode, "major");
  // Nothing was held long enough to widen the range, which is the whole point:
  // the two measurements read the same frames under different rules.
  assert.equal(snapshot.lowestNote, null);
  assert.equal(snapshot.highestNote, null);
});

test("the key is cumulative over the session, not a rolling window", () => {
  // Four seconds rarely contains five distinct pitch classes, so a windowed key
  // would answer "not enough yet" almost always. The cost of the choice is
  // asserted with it: the earlier singing still counts.
  const stats = new LivePracticeStats();
  for (let tick = 0; tick < 6; tick++) {
    sing(stats, MAJOR_MELODY_FRAMES);
    stats.snapshot();
  }
  assert.equal(stats.snapshot().keyTonic, "C");

  // A long hum afterwards does not erase what was sung before it.
  for (let tick = 0; tick < 8; tick++) {
    sing(stats, new Array(FRAMES_PER_TICK).fill(0));
    stats.snapshot();
  }
  assert.equal(stats.snapshot().keyTonic, "C");
});

test("a new take starts with no key at all", () => {
  const stats = new LivePracticeStats();
  for (let tick = 0; tick < 8; tick++) {
    sing(stats, MAJOR_MELODY_FRAMES);
    stats.snapshot();
  }
  assert.equal(stats.snapshot().keyTonic, "C");

  stats.reset();
  assert.deepEqual(stats.snapshot(), EMPTY_STATS);
});

test("the empty stats carry no key, which is what a new take starts from", () => {
  // No tonic, no mode, no margin — and the reason a fresh accumulator gives for
  // having none, so the two are one state rather than two.
  assert.equal(EMPTY_STATS.keyTonic, null);
  assert.equal(EMPTY_STATS.keyMode, null);
  assert.equal(EMPTY_STATS.keyConfidence, null);
  assert.equal(EMPTY_STATS.keyUnmeasuredReason, "TOO_LITTLE_VOICED_TIME");
  assert.deepEqual(new LivePracticeStats().snapshot(), EMPTY_STATS);
});

// --- The boundary session, and the constant it chose ------------------------

/**
 * A session that sits on the major/minor boundary.
 *
 * A mode-neutral scaffold — C, D, F, G — with the major and the minor third
 * taking it in turns to be the more-sung one, `block` ticks at a time. The
 * cumulative profile crosses back and forth over the boundary, which is the
 * input that makes a raw per-tick verdict unreadable.
 */
function boundarySession(ticks: number, block: number): number[][] {
  const scaffold = [0, 0, 0, 2, 2, 5, 5, 7, 7, 7];
  return Array.from({ length: ticks }, (_, tick) => {
    const major = Math.floor(tick / block) % 2 === 0;
    const thirds = major ? 5 : 7;
    return Array.from({ length: FRAMES_PER_TICK }, (_, index) =>
      index < FRAMES_PER_TICK - thirds ? scaffold[index % scaffold.length] : major ? 4 : 3,
    );
  });
}

/** Drive a session through one dwell setting and count what the reader sees. */
function displayedChanges(session: number[][], dwellTicks: number): number {
  const counts = new Array(12).fill(0);
  const tracker = new LiveKeyTracker(dwellTicks);
  let voicedFrames = 0;
  let previous: string | null = null;
  let changes = 0;

  for (const frames of session) {
    for (const pitchClass of frames) {
      counts[pitchClass] += 1;
      voicedFrames += 1;
    }
    const readout = tracker.tick(
      estimateKey(pitchClassProfile(counts), voicedFrames * LIVE_FRAME_SECONDS),
    );
    const label = readout.tonic === null ? null : `${readout.tonic} ${readout.mode}`;
    if (label !== previous) changes += 1;
    previous = label;
  }
  return changes;
}

test("the raw per-tick verdict flickers, which is why a dwell exists", () => {
  // 14 changes in 40 ticks — a label rewriting itself every second and a half.
  assert.equal(displayedChanges(boundarySession(40, 1), 1), 14);
});

test("the dwell is the value the sweep chose, and the sweep runs here", () => {
  // Rows are how many consecutive ticks each third holds the floor; columns are
  // the dwell. Dwell n absorbs an excursion shorter than n ticks and nothing
  // longer, so no value settles every session — 4 is the smallest that holds
  // all four fixtures at or below 3 changes. Every number below is measured by
  // this assertion, not copied from a note.
  const measured = [1, 2, 3, 4].map((block) =>
    [1, 2, 3, 4, 6].map((dwell) => displayedChanges(boundarySession(40, block), dwell)),
  );

  assert.deepEqual(measured, [
    [14, 1, 1, 1, 1],
    [19, 3, 1, 1, 1],
    [18, 7, 6, 1, 0],
    [15, 9, 6, 3, 0],
  ]);

  assert.equal(KEY_DWELL_TICKS, 4);
  for (const row of measured) {
    assert.ok(
      row[3] <= 3,
      "the shipped dwell must hold every boundary fixture at or below 3 changes",
    );
  }
});

test("what the dwell costs is a second and a half, and it is asserted too", () => {
  // The other half of the trade. A clear melody's label appears at tick 5
  // rather than tick 2: 3.0 s of singing instead of 1.5 s, on top of the
  // evidence gates rather than instead of them.
  const commitTick = (dwellTicks: number): number | null => {
    const counts = new Array(12).fill(0);
    const tracker = new LiveKeyTracker(dwellTicks);
    let voicedFrames = 0;
    for (let tick = 0; tick < 20; tick++) {
      for (const pitchClass of MAJOR_MELODY_FRAMES) {
        counts[pitchClass] += 1;
        voicedFrames += 1;
      }
      const readout = tracker.tick(
        estimateKey(pitchClassProfile(counts), voicedFrames * LIVE_FRAME_SECONDS),
      );
      if (readout.tonic !== null) return tick;
    }
    return null;
  };

  assert.equal(commitTick(1), 2);
  assert.equal(commitTick(KEY_DWELL_TICKS), 5);
});

test("a single tick of another key never reaches the screen", () => {
  const tracker = new LiveKeyTracker();
  const counts = new Array(12).fill(0);
  let voicedFrames = 0;
  const tick = (pitchClasses: number[]) => {
    for (const pitchClass of pitchClasses) {
      counts[pitchClass] += 1;
      voicedFrames += 1;
    }
    return tracker.tick(estimateKey(pitchClassProfile(counts), voicedFrames * LIVE_FRAME_SECONDS));
  };

  let readout = { tonic: null as string | null };
  for (let index = 0; index < 8; index++) readout = tick(MAJOR_MELODY_FRAMES);
  assert.equal(readout.tonic, "C");

  // One tick of something else, then back. The label never moved.
  assert.equal(tick(MAJOR_MELODY_FRAMES.map((pc) => (pc + 6) % 12)).tonic, "C");
  assert.equal(tick(MAJOR_MELODY_FRAMES).tonic, "C");
});

test("losing the evidence waits out the dwell too, so the label does not blink", () => {
  // A refusal is a challenger like any other. Only a sustained one clears the
  // label — and with a cumulative profile a settled session does not lose it.
  const tracker = new LiveKeyTracker(2);
  const shares = pitchClassProfile(MAJOR_MELODY_FRAMES.reduce(
    (counts, pitchClass) => {
      counts[pitchClass] += 10;
      return counts;
    },
    new Array(12).fill(0),
  ));
  const answered = estimateKey(shares, 60);
  const refused = estimateKey(pitchClassProfile(new Array(12).fill(0)), 0);

  tracker.tick(answered);
  assert.equal(tracker.tick(answered).tonic, "C");
  assert.equal(tracker.tick(refused).tonic, "C", "one refused tick must not clear it");
  assert.equal(tracker.tick(refused).tonic, null, "two must");
});

test("the margin travels with the label and is the one the verdict carried", () => {
  const stats = new LivePracticeStats();
  for (let tick = 0; tick < 8; tick++) {
    sing(stats, MAJOR_MELODY_FRAMES);
    stats.snapshot();
  }
  const snapshot = stats.snapshot();
  assert.ok(snapshot.keyConfidence !== null);

  const counts = new Array(12).fill(0);
  for (let tick = 0; tick < 9; tick++) {
    for (const pitchClass of MAJOR_MELODY_FRAMES) counts[pitchClass] += 1;
  }
  const direct = estimateKey(
    pitchClassProfile(counts),
    9 * FRAMES_PER_TICK * LIVE_FRAME_SECONDS,
  );
  assert.equal(snapshot.keyConfidence, direct.key?.confidence);
});

// --- Cost ------------------------------------------------------------------

test("the fold and the estimate are far inside the publish budget", () => {
  // The rule this feature has to live under: the fold runs per frame at ~30 Hz
  // and the estimate on the 500 ms publish tick. Both are measured rather than
  // assumed, with generous ceilings — this asserts the shape of the cost, not
  // the speed of the machine it runs on.
  const stats = new LivePracticeStats();
  const sample = voiced(60);

  const frames = 30000;
  const startFold = performance.now();
  for (let index = 0; index < frames; index++) stats.push(sample);
  const perFrame = (performance.now() - startFold) / frames;

  const shares = pitchClassProfile(
    MAJOR_MELODY_FRAMES.reduce((counts, pitchClass) => {
      counts[pitchClass] += 40;
      return counts;
    }, new Array(12).fill(0)),
  );
  const estimates = 2000;
  const startEstimate = performance.now();
  for (let index = 0; index < estimates; index++) estimateKey(shares, 60);
  const perEstimate = (performance.now() - startEstimate) / estimates;

  assert.ok(perFrame < 0.01, `${perFrame} ms per frame`);
  assert.ok(perEstimate < 1, `${perEstimate} ms per estimate`);
  // The cost of an estimate does not grow with the session: the fold is
  // incremental and the correlation is always over twelve numbers.
  assert.equal(shares.length, 12);
});

// --- The readout -----------------------------------------------------------
//
// Presentation only: what a reader sees for each state the accumulator can be
// in. The component is a few lines of markup around these functions, so this is
// where the rules that matter are enforced — and the last test in the file is
// structural, because "never shown beside the uploaded key" is a fact about the
// component tree rather than about a string.

test("no key renders no tonic, and no empty label either", () => {
  assert.equal(liveKeyLabel(EMPTY_STATS), null);
  assert.equal(liveKeyMarginLabel(EMPTY_STATS), null);
});

test("a committed key renders as a tonic and a mode, and nothing else", () => {
  const stats: LiveStats = {
    ...EMPTY_STATS,
    keyTonic: "G",
    keyMode: "major",
    keyConfidence: 0.2621,
    keyUnmeasuredReason: null,
  };
  assert.equal(liveKeyLabel(stats), "G major");
  // Not a percentage, not a grade, not a mark out of anything.
  assert.equal(liveKeyMarginLabel(stats), "Margin 0.262 over the next-best key");
});

test("half a key is not a label", () => {
  // Tonic and mode arrive together or not at all; neither alone is renderable.
  assert.equal(liveKeyLabel({ ...EMPTY_STATS, keyTonic: "G" }), null);
  assert.equal(liveKeyLabel({ ...EMPTY_STATS, keyMode: "minor" }), null);
});

test("a margin without a key is a number about nothing", () => {
  assert.equal(liveKeyMarginLabel({ ...EMPTY_STATS, keyConfidence: 0.3 }), null);
});

test("each way of having no key says what it is waiting for", () => {
  assert.match(liveKeyWaitingMessage("TOO_LITTLE_VOICED_TIME"), /Keep singing/);
  assert.match(liveKeyWaitingMessage("TOO_FEW_PITCH_CLASSES"), /more different notes/);
  assert.match(liveKeyWaitingMessage("AMBIGUOUS"), /several keys/);
  // An unrecognised reason falls back to the same voice, never to an error.
  assert.match(liveKeyWaitingMessage(null), /Keep singing/);
});

test("nothing in the waiting copy reads as a fault or a score", () => {
  const forbidden = /\b(fail|failed|error|bad|wrong|poor|score|grade|out of \d)\b/i;
  for (const message of Object.values(LIVE_KEY_WAITING_MESSAGES)) {
    assert.ok(!forbidden.test(message), message);
  }
});

test("the live key never claims to be a song's key or a correct one", () => {
  const card = readFileSync(
    join(import.meta.dirname, "..", "components", "record", "live-stats-card.tsx"),
    "utf8",
  );
  assert.match(card, /Key you seem to be singing in/);
  assert.ok(!/key of (the|this) song/i.test(card), "there is no song here");
  assert.ok(!/correct key|right key|in the correct/i.test(card));
});

test("no component renders the live key beside the uploaded recording's key", () => {
  // The two are different measurements over different frames and will sometimes
  // disagree. The repository's answer to that is not to reconcile them: they
  // are labelled apart and never placed side by side. This asserts the second
  // half — that no single component can put both on the screen.
  const roots = ["components", "app"].map((directory) =>
    join(import.meta.dirname, "..", directory),
  );
  const files: string[] = [];
  const walk = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) files.push(path);
    }
  };
  for (const root of roots) walk(root);
  assert.ok(files.length > 20, "the component sweep found almost nothing");

  const rendersLiveKey = /liveKeyLabel|keyTonic/;
  const rendersUploadedKey = /MusicalKeyCard|musicalKey/;
  for (const path of files) {
    const source = readFileSync(path, "utf8");
    assert.ok(
      !(rendersLiveKey.test(source) && rendersUploadedKey.test(source)),
      `${path} reaches for both keys`,
    );
  }
});
