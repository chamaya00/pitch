/**
 * The key estimator, in the runtime that has to agree with another one.
 *
 * This suite exists twice over. Half of it is the ordinary job — all 24 keys,
 * transposition, and every adversarial fixture refused — and the other half is
 * the only thing standing between two implementations of one measurement and
 * two measurements: `fixtures/key-parity.json`, read here and in
 * `backend/tests/test_audio_key.py`, verdict for verdict and margin for margin.
 *
 * The parity table is not decoration. Slice 5's mutation run against the
 * backend found that redefining confidence as the lead over the next candidate
 * with a *different tonic* passed every other fixture, because the pitch-class
 * gate stops them before the correlation is reached. `both thirds, neither
 * mode` is the one row that can see the difference, and a second implementation
 * is exactly where that definition would be got wrong again — so it is in the
 * table, and it is asserted here.
 *
 * **No fixture here validates the estimator against human singing.** Every
 * profile is constructed, as on the backend, and for the same reason: there is
 * no annotated corpus of real singing in this repository.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  DEFAULT_PROFILE_SET,
  MIN_DISTINCT_PITCH_CLASSES,
  MIN_KEY_CONFIDENCE,
  MIN_PITCH_CLASS_SHARE,
  MIN_VOICED_SECONDS,
  TEMPERLEY,
  correlation,
  correlationOf,
  estimateKey,
  pitchClassProfile,
  rankedCandidates,
  type KeyMode,
  type LiveKeyVerdict,
} from "../lib/live-key.ts";
import { NOTE_NAMES } from "../lib/pitch.ts";

// --- The shared table ------------------------------------------------------

interface ParityCase {
  name: string;
  note: string;
  frames: number[];
  expect: {
    tonic: string | null;
    mode: KeyMode | null;
    confidence: number | null;
    alternative_tonic: string | null;
    alternative_mode: KeyMode | null;
    unmeasured_reason: string | null;
    distinct_pitch_classes: number;
  };
}

interface ParityTable {
  frame_seconds: number;
  profile_set: string;
  thresholds: {
    min_distinct_pitch_classes: number;
    min_voiced_seconds: number;
    min_key_confidence: number;
    min_pitch_class_share: number;
  };
  cases: ParityCase[];
}

const PARITY: ParityTable = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "fixtures", "key-parity.json"), "utf8"),
) as ParityTable;

/** Frames to a verdict, the way the accumulator will: counts in, key out. */
function verdict(frames: number[], frameSeconds = PARITY.frame_seconds): LiveKeyVerdict {
  const total = frames.reduce((sum, count) => sum + count, 0);
  return estimateKey(pitchClassProfile(frames), total * frameSeconds);
}

function framesFor(weights: Record<number, number>): number[] {
  return Array.from({ length: 12 }, (_, pitchClass) => weights[pitchClass] ?? 0);
}

function transpose(frames: number[], semitones: number): number[] {
  return Array.from({ length: 12 }, (_, pitchClass) => frames[(((pitchClass - semitones) % 12) + 12) % 12]);
}

// The same three melodies the backend fixtures use, written the same way.
const MAJOR_MELODY = framesFor({ 0: 40, 2: 20, 4: 30, 5: 20, 7: 40, 9: 20, 11: 10 });
const MINOR_MELODY = framesFor({ 0: 40, 2: 20, 3: 30, 5: 20, 7: 40, 8: 20, 10: 10 });
const PENTATONIC = framesFor({ 0: 40, 2: 20, 4: 25, 7: 35, 9: 20 });

// --- Parity ----------------------------------------------------------------

test("the shared table is the one this implementation was written against", () => {
  assert.equal(PARITY.profile_set, DEFAULT_PROFILE_SET.name);
  assert.equal(PARITY.thresholds.min_distinct_pitch_classes, MIN_DISTINCT_PITCH_CLASSES);
  assert.equal(PARITY.thresholds.min_voiced_seconds, MIN_VOICED_SECONDS);
  assert.equal(PARITY.thresholds.min_key_confidence, MIN_KEY_CONFIDENCE);
  assert.equal(PARITY.thresholds.min_pitch_class_share, MIN_PITCH_CLASS_SHARE);
  assert.ok(PARITY.cases.length >= 15, "the table has lost fixtures");
});

for (const parityCase of PARITY.cases) {
  test(`parity with the backend: ${parityCase.name}`, () => {
    const result = verdict(parityCase.frames);
    const expected = parityCase.expect;

    assert.equal(result.key?.tonic ?? null, expected.tonic, parityCase.note);
    assert.equal(result.key?.mode ?? null, expected.mode);
    assert.equal(result.unmeasuredReason, expected.unmeasured_reason);
    assert.equal(result.distinctPitchClasses, expected.distinct_pitch_classes);

    if (expected.confidence === null) {
      assert.equal(result.key, null);
      return;
    }
    assert.ok(result.key !== null);
    // Tight rather than approximate: the two implementations perform the same
    // operations in the same order over the same doubles, so anything beyond
    // floating-point noise is a difference in the measurement itself.
    assert.ok(
      Math.abs(result.key.confidence - expected.confidence) < 1e-9,
      `margin ${result.key.confidence} is not the backend's ${expected.confidence}`,
    );
    assert.equal(result.key.alternative?.tonic ?? null, expected.alternative_tonic);
    assert.equal(result.key.alternative?.mode ?? null, expected.alternative_mode);
  });
}

test("the margin is over the next candidate and not the next tonic", () => {
  // The row that pins the definition. Under the weaker definition this profile
  // leads by 0.409 and reads as a confident C minor; under the one that shipped
  // it leads by 0.021 and is refused.
  const ambiguous = PARITY.cases.find((row) => row.name === "both thirds, neither mode");
  assert.ok(ambiguous, "the fixture that pins the margin has been removed from the table");

  const total = ambiguous.frames.reduce((sum, count) => sum + count, 0);
  const ranked = rankedCandidates(pitchClassProfile(ambiguous.frames));
  const [best, second] = ranked;

  assert.equal(best.tonic, second.tonic);
  assert.notEqual(best.mode, second.mode);

  const overAnyCandidate = best.correlation - second.correlation;
  const overOtherTonic =
    best.correlation - ranked.find((candidate) => candidate.tonic !== best.tonic)!.correlation;

  assert.ok(Math.abs(overAnyCandidate - 0.0205) < 0.001, `${overAnyCandidate}`);
  assert.ok(Math.abs(overOtherTonic - 0.4094) < 0.001, `${overOtherTonic}`);
  assert.ok(overAnyCandidate < MIN_KEY_CONFIDENCE && MIN_KEY_CONFIDENCE <= overOtherTonic);
  assert.equal(estimateKey(pitchClassProfile(ambiguous.frames), total * PARITY.frame_seconds).key, null);
});

// --- All 24 keys, and transposition ----------------------------------------

for (let tonic = 0; tonic < 12; tonic++) {
  test(`${NOTE_NAMES[tonic]} major is identified`, () => {
    const result = verdict(transpose(MAJOR_MELODY, tonic));
    assert.ok(result.key !== null, `${result.unmeasuredReason}`);
    assert.equal(result.key.tonic, NOTE_NAMES[tonic]);
    assert.equal(result.key.mode, "major");
  });

  test(`${NOTE_NAMES[tonic]} minor is identified`, () => {
    const result = verdict(transpose(MINOR_MELODY, tonic));
    assert.ok(result.key !== null, `${result.unmeasuredReason}`);
    assert.equal(result.key.tonic, NOTE_NAMES[tonic]);
    assert.equal(result.key.mode, "minor");
  });
}

test("transposing a melody moves only the tonic", () => {
  const original = verdict(MAJOR_MELODY);
  assert.ok(original.key !== null);

  for (let semitones = 1; semitones < 12; semitones++) {
    const moved = verdict(transpose(MAJOR_MELODY, semitones));
    assert.ok(moved.key !== null);
    assert.equal(moved.key.tonic, NOTE_NAMES[semitones]);
    assert.equal(moved.key.mode, original.key.mode);
    // The margin is a property of the shape, not of where it sits.
    assert.ok(
      Math.abs(moved.key.confidence - original.key.confidence) < 1e-9,
      `${moved.key.confidence} vs ${original.key.confidence}`,
    );
  }
});

// --- Refusals --------------------------------------------------------------

test("a pentatonic melody is answered: the gates are not tuned until nothing passes", () => {
  const result = verdict(PENTATONIC);
  assert.ok(result.key !== null, `${result.unmeasuredReason}`);
  assert.equal(result.distinctPitchClasses, 5);
});

test("a hum is not a key", () => {
  const result = verdict(framesFor({ 0: 140 }));
  assert.equal(result.key, null);
  assert.equal(result.unmeasuredReason, "TOO_FEW_PITCH_CLASSES");
});

test("a three-note arpeggio is not a key, though it out-scores real music", () => {
  const result = verdict(framesFor({ 0: 60, 4: 45, 7: 50 }));
  assert.equal(result.key, null);
  assert.equal(result.unmeasuredReason, "TOO_FEW_PITCH_CLASSES");

  // The number the pitch-class gate is protecting against: 0.309, above the
  // 0.262 a real major melody reaches. Confidence alone would answer this.
  const ranked = rankedCandidates(pitchClassProfile(framesFor({ 0: 60, 4: 45, 7: 50 })));
  assert.ok(ranked[0].correlation - ranked[1].correlation > 0.3);
});

test("a chromatic wander is refused on confidence, not on evidence", () => {
  const result = verdict(framesFor(Object.fromEntries(Array.from({ length: 12 }, (_, pc) => [pc, 20 + pc]))));
  assert.equal(result.key, null);
  assert.equal(result.unmeasuredReason, "AMBIGUOUS");
  assert.equal(result.distinctPitchClasses, 12);
});

test("half a second of five classes is refused on time alone", () => {
  const result = verdict(framesFor({ 0: 5, 2: 4, 4: 5, 5: 4, 7: 5 }));
  assert.equal(result.key, null);
  assert.equal(result.unmeasuredReason, "TOO_LITTLE_VOICED_TIME");
  assert.ok(result.voicedSeconds < MIN_VOICED_SECONDS);
});

test("nothing voiced is refused rather than answered", () => {
  const result = estimateKey(pitchClassProfile(new Array(12).fill(0)), 0);
  assert.equal(result.key, null);
  assert.equal(result.unmeasuredReason, "TOO_LITTLE_VOICED_TIME");
  // Not twelve measured zeroes: no pitch class was measured at all.
  assert.deepEqual(result.pitchClasses, []);
});

test("the gates are reported in order of how fundamental they are", () => {
  const result = verdict(framesFor({ 0: 10 }));
  assert.equal(result.unmeasuredReason, "TOO_LITTLE_VOICED_TIME");
});

test("a refusal still carries its evidence", () => {
  const result = verdict(framesFor({ 0: 140 }));
  assert.equal(result.pitchClasses.length, 12);
  assert.ok(Math.abs(result.voicedSeconds - 140 * PARITY.frame_seconds) < 1e-9);
  assert.equal(result.method, "temperley");
});

test("a verdict is a key or a refusal and never both", () => {
  for (const frames of [MAJOR_MELODY, PENTATONIC, framesFor({ 0: 140 }), new Array(12).fill(0)]) {
    const result = verdict(frames);
    assert.equal(result.key === null, result.unmeasuredReason !== null);
  }
});

// --- The profile and the arithmetic ----------------------------------------

test("the profile always has twelve entries in pitch-class order", () => {
  const shares = pitchClassProfile(framesFor({ 0: 40, 7: 60 }));
  assert.equal(shares.length, 12);
  assert.deepEqual(
    shares.map((share) => share.name),
    [...NOTE_NAMES],
  );
  assert.ok(Math.abs(shares.reduce((sum, share) => sum + share.percentageOfVoicedTime, 0) - 100) < 1e-9);
});

test("a negligible share does not make a five-note melody look like six", () => {
  const withStray = [...PENTATONIC];
  withStray[1] = 1;
  const result = verdict(withStray);

  assert.equal(result.distinctPitchClasses, 5);
  const stray = result.pitchClasses[1];
  assert.ok(stray.percentageOfVoicedTime > 0 && stray.percentageOfVoicedTime < MIN_PITCH_CLASS_SHARE);
});

test("a profile of the wrong length is a programming error, not a verdict", () => {
  assert.throws(() => pitchClassProfile([1, 2, 3]), RangeError);
});

test("a flat profile correlates to nothing rather than dividing by zero", () => {
  assert.equal(correlation(new Array(12).fill(5), TEMPERLEY.major), 0);
});

test("a profile correlates perfectly with itself", () => {
  assert.ok(Math.abs(correlation([...TEMPERLEY.major], TEMPERLEY.major) - 1) < 1e-12);
});

test("the ranking is total, and every key appears in it exactly once", () => {
  const ranked = rankedCandidates(pitchClassProfile(MAJOR_MELODY));
  assert.equal(ranked.length, 24);
  assert.equal(new Set(ranked.map((c) => `${c.tonic} ${c.mode}`)).size, 24);
  for (let index = 1; index < ranked.length; index++) {
    assert.ok(ranked[index - 1].correlation >= ranked[index].correlation);
  }
});

test("a named key's correlation can be read back out of the ranking", () => {
  const ranked = rankedCandidates(pitchClassProfile(MAJOR_MELODY));
  assert.equal(correlationOf(ranked, "C", "major"), ranked[0].correlation);
  assert.equal(correlationOf(ranked, "H", "major"), null);
});

test("the same profile always produces the same answer", () => {
  const first = verdict(MAJOR_MELODY);
  const second = verdict(MAJOR_MELODY);
  assert.deepEqual(first.key, second.key);
});
