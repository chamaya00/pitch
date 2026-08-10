/**
 * Live pitch tests.
 *
 * Same runner as the other suites — Node's built-in `--test` with type
 * stripping, no new dependency.
 *
 * The three modules under test are deliberately free of browser APIs so that
 * the things most likely to be wrong can be driven directly: the conversions
 * with known reference frequencies, the detector with synthetic tones whose
 * true pitch we chose ourselves, and the smoother with a scripted sequence of
 * estimates. None of this needs a microphone, and none of it is a mock of the
 * real algorithm — it is the real algorithm, fed known input.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  A4_FREQUENCY,
  A4_MIDI,
  centsFromNearestNote,
  describeFrequency,
  formatCents,
  formatFrequency,
  frequencyToMidi,
  midiToFrequency,
  midiToNoteName,
} from "../lib/pitch.ts";
import {
  DEFAULT_CLARITY_THRESHOLD,
  detectPitch,
  rootMeanSquare,
  SILENCE_RMS,
} from "../lib/pitch-detector.ts";
import {
  median,
  PitchStream,
  summarise,
  SMOOTHING_WINDOW,
  UNVOICED_FRAMES_TO_CLEAR,
  type LivePitchSample,
} from "../lib/pitch-stream.ts";

// --- Signal helpers --------------------------------------------------------

const SAMPLE_RATE = 48000;
const WINDOW = 2048;

/** A pure tone. The simplest thing a detector must not get wrong. */
function sine(
  frequency: number,
  sampleRate = SAMPLE_RATE,
  length = WINDOW,
  amplitude = 0.5,
): Float32Array {
  const samples = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    samples[i] = amplitude * Math.sin((2 * Math.PI * frequency * i) / sampleRate);
  }
  return samples;
}

/**
 * A tone with harmonics, which is what a voice actually is. This is the signal
 * that breaks naive autocorrelation — the harmonics give it strong correlation
 * at twice the true period, so a detector that takes the tallest peak reports
 * an octave too low.
 */
function harmonicTone(
  fundamental: number,
  sampleRate = SAMPLE_RATE,
  length = WINDOW,
): Float32Array {
  const samples = new Float32Array(length);
  const partials = [1, 0.6, 0.4, 0.25, 0.15];
  for (let i = 0; i < length; i++) {
    let value = 0;
    for (let h = 0; h < partials.length; h++) {
      value +=
        partials[h] *
        Math.sin((2 * Math.PI * fundamental * (h + 1) * i) / sampleRate);
    }
    samples[i] = value * 0.25;
  }
  return samples;
}

/** Deterministic pseudo-noise: no pitch to find, but plenty of level. */
function noise(length = WINDOW, amplitude = 0.4): Float32Array {
  const samples = new Float32Array(length);
  let seed = 12345;
  for (let i = 0; i < length; i++) {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    samples[i] = ((seed / 2147483648) * 2 - 1) * amplitude;
  }
  return samples;
}

function centsBetween(a: number, b: number): number {
  return 1200 * Math.log2(a / b);
}

// --- Conversions -----------------------------------------------------------

test("440 Hz is A4", () => {
  const reading = describeFrequency(A4_FREQUENCY);
  assert.ok(reading);
  assert.equal(reading.note, "A4");
  assert.equal(reading.midi, A4_MIDI);
  assert.equal(reading.cents, 0);
});

test("261.625 Hz is C4", () => {
  const reading = describeFrequency(261.625);
  assert.ok(reading);
  assert.equal(reading.note, "C4");
  assert.equal(Math.round(reading.midi), 60);
  // Middle C is 261.6256 Hz; the truncated value is a hair flat, not a
  // different note.
  assert.ok(Math.abs(reading.cents) < 1, `cents was ${reading.cents}`);
});

test("MIDI and frequency are inverses at the anchors", () => {
  assert.equal(midiToFrequency(A4_MIDI), A4_FREQUENCY);
  assert.equal(frequencyToMidi(A4_FREQUENCY), A4_MIDI);
  assert.equal(midiToNoteName(60), "C4");
  assert.equal(midiToNoteName(69), "A4");
  assert.equal(midiToNoteName(21), "A0");
});

test("an octave is twelve semitones, in both directions", () => {
  assert.equal(describeFrequency(880)?.note, "A5");
  assert.equal(describeFrequency(220)?.note, "A3");
  assert.equal(frequencyToMidi(880), A4_MIDI + 12);
  assert.equal(frequencyToMidi(220), A4_MIDI - 12);
});

test("cents are signed and never exceed half a semitone", () => {
  // A quarter-tone above A4 is the worst case: 50 cents, still A4.
  const quarterSharp = A4_FREQUENCY * 2 ** (0.5 / 12);
  const cents = centsFromNearestNote(quarterSharp);
  assert.ok(cents !== null);
  assert.ok(Math.abs(cents) <= 50.0001, `cents was ${cents}`);

  const flat = centsFromNearestNote(A4_FREQUENCY * 2 ** (-0.1 / 12));
  assert.ok(flat !== null && flat < 0, `expected a negative reading, got ${flat}`);
});

test("frequencies that cannot be a pitch produce no note", () => {
  for (const bad of [0, -1, -440, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, null]) {
    assert.equal(frequencyToMidi(bad), null, `frequencyToMidi(${String(bad)})`);
    assert.equal(centsFromNearestNote(bad), null, `cents(${String(bad)})`);
    assert.equal(describeFrequency(bad), null, `describeFrequency(${String(bad)})`);
  }
});

test("frequencies outside the audible pitch range produce no note", () => {
  assert.equal(describeFrequency(5), null);
  assert.equal(describeFrequency(20000), null);
});

test("formatters show an em dash rather than a fabricated zero", () => {
  assert.equal(formatCents(null), "—");
  assert.equal(formatCents(Number.NaN), "—");
  assert.equal(formatFrequency(null), "—");
  assert.equal(formatCents(0), "0 cents");
  assert.equal(formatCents(5.2), "+5 cents");
  assert.equal(formatCents(-5.2), "−5 cents");
  assert.equal(formatFrequency(440), "440.0 Hz");
});

// --- Detector --------------------------------------------------------------

test("a pure 440 Hz tone is detected within a few cents", () => {
  const estimate = detectPitch(sine(440), SAMPLE_RATE);
  assert.ok(estimate.frequency !== null, "no frequency detected");
  const error = Math.abs(centsBetween(estimate.frequency, 440));
  assert.ok(error < 5, `off by ${error.toFixed(2)} cents`);
  assert.ok(
    estimate.clarity >= DEFAULT_CLARITY_THRESHOLD,
    `clarity ${estimate.clarity} below threshold`,
  );
  assert.equal(describeFrequency(estimate.frequency)?.note, "A4");
});

test("a pure 261.626 Hz tone is detected as C4", () => {
  const estimate = detectPitch(sine(261.626), SAMPLE_RATE);
  assert.ok(estimate.frequency !== null);
  const error = Math.abs(centsBetween(estimate.frequency, 261.626));
  assert.ok(error < 5, `off by ${error.toFixed(2)} cents`);
  assert.equal(describeFrequency(estimate.frequency)?.note, "C4");
});

test("a harmonic tone is not detected an octave low", () => {
  // The regression this detector exists for. A mean-of-autocorrelation
  // approach reports 110 Hz here; NSDF plus first-acceptable-peak reports 220.
  for (const fundamental of [110, 165, 220, 330]) {
    const estimate = detectPitch(harmonicTone(fundamental), SAMPLE_RATE);
    assert.ok(estimate.frequency !== null, `no detection at ${fundamental} Hz`);
    const error = Math.abs(centsBetween(estimate.frequency, fundamental));
    assert.ok(
      error < 15,
      `${fundamental} Hz read as ${estimate.frequency.toFixed(1)} Hz (${error.toFixed(0)} cents off)`,
    );
  }
});

test("the detector works at other sample rates", () => {
  for (const rate of [16000, 44100]) {
    const estimate = detectPitch(sine(220, rate, 2048), rate);
    assert.ok(estimate.frequency !== null, `no detection at ${rate} Hz`);
    const error = Math.abs(centsBetween(estimate.frequency, 220));
    assert.ok(error < 10, `${rate} Hz: off by ${error.toFixed(2)} cents`);
  }
});

test("silence produces no pitch at all", () => {
  const estimate = detectPitch(new Float32Array(WINDOW), SAMPLE_RATE);
  assert.equal(estimate.frequency, null);
  assert.equal(estimate.clarity, 0);
  assert.equal(estimate.rms, 0);
});

test("a signal below the silence gate produces no pitch", () => {
  const quiet = sine(440, SAMPLE_RATE, WINDOW, SILENCE_RMS / 4);
  const estimate = detectPitch(quiet, SAMPLE_RATE);
  assert.equal(estimate.frequency, null);
});

test("noise never reaches the clarity threshold", () => {
  const estimate = detectPitch(noise(), SAMPLE_RATE);
  assert.ok(
    estimate.frequency === null || estimate.clarity < DEFAULT_CLARITY_THRESHOLD,
    `noise read as ${String(estimate.frequency)} Hz with clarity ${estimate.clarity}`,
  );
});

test("a window too short to hold a period returns null, not a guess", () => {
  const estimate = detectPitch(sine(440, SAMPLE_RATE, 64), SAMPLE_RATE);
  assert.equal(estimate.frequency, null);
});

test("an impossible sample rate returns null rather than throwing", () => {
  for (const rate of [0, -48000, Number.NaN, Number.POSITIVE_INFINITY]) {
    const estimate = detectPitch(sine(440), rate);
    assert.equal(estimate.frequency, null, `sampleRate ${rate}`);
  }
});

test("an empty buffer returns null rather than throwing", () => {
  const estimate = detectPitch(new Float32Array(0), SAMPLE_RATE);
  assert.equal(estimate.frequency, null);
  assert.equal(estimate.rms, 0);
});

test("rootMeanSquare measures level, and is zero for silence", () => {
  assert.equal(rootMeanSquare(new Float32Array(0)), 0);
  assert.equal(rootMeanSquare(new Float32Array(128)), 0);
  // A sine of amplitude a has RMS a/sqrt(2).
  const rms = rootMeanSquare(sine(440, SAMPLE_RATE, WINDOW, 0.5));
  assert.ok(Math.abs(rms - 0.5 / Math.SQRT2) < 0.01, `rms was ${rms}`);
});

// --- Smoothing and voicing -------------------------------------------------

test("median ignores an outlier that a mean would follow", () => {
  assert.equal(median([]), null);
  assert.equal(median([440]), 440);
  assert.equal(median([440, 441, 880]), 441);
  assert.equal(median([1, 2, 3, 4]), 2.5);
});

test("low-confidence frames never produce a note", () => {
  const stream = new PitchStream();
  const sample = stream.push({
    timestamp: 0,
    frequency: 440,
    clarity: DEFAULT_CLARITY_THRESHOLD - 0.2,
  });
  assert.equal(sample.voiced, false);
  assert.equal(sample.note, null);
  assert.equal(sample.frequency, null);
  assert.equal(sample.midi, null);
  assert.equal(sample.cents, null);
});

test("a null frequency never produces a note, whatever the clarity claims", () => {
  const stream = new PitchStream();
  const sample = stream.push({ timestamp: 0, frequency: null, clarity: 1 });
  assert.equal(sample.voiced, false);
  assert.equal(sample.note, null);
});

test("a confident frame produces the note it measured", () => {
  const stream = new PitchStream();
  const sample = stream.push({ timestamp: 0, frequency: 440, clarity: 0.97 });
  assert.equal(sample.voiced, true);
  assert.equal(sample.note, "A4");
  assert.equal(sample.confidence, 0.97);
});

test("a noisy sequence around one note displays that note steadily", () => {
  const stream = new PitchStream();
  // ±8 cents of wobble, which is what a held note actually looks like.
  const wobble = [438.2, 441.1, 439.6, 440.8, 438.9, 441.4, 440.2, 439.1];
  const notes = wobble.map(
    (frequency, index) =>
      stream.push({ timestamp: index * 33, frequency, clarity: 0.95 }).note,
  );
  assert.deepEqual(
    new Set(notes),
    new Set(["A4"]),
    `expected a steady A4, got ${notes.join(", ")}`,
  );
});

test("a single octave-jumped frame does not move the display", () => {
  const stream = new PitchStream();
  for (const frequency of [440, 441, 439, 440]) {
    stream.push({ timestamp: 0, frequency, clarity: 0.95 });
  }
  const glitch = stream.push({ timestamp: 0, frequency: 880, clarity: 0.95 });
  assert.equal(glitch.note, "A4", "one bad frame changed the displayed note");
  assert.ok(glitch.frequency !== null && glitch.frequency < 500);
});

test("a genuine note change is followed, not smoothed away", () => {
  const stream = new PitchStream();
  for (let i = 0; i < SMOOTHING_WINDOW; i++) {
    stream.push({ timestamp: i, frequency: 440, clarity: 0.95 });
  }
  let sample: LivePitchSample | null = null;
  // A real move needs more than half the window before the median follows;
  // at ~30 frames a second that is under a tenth of a second.
  for (let i = 0; i < SMOOTHING_WINDOW; i++) {
    sample = stream.push({ timestamp: 10 + i, frequency: 523.25, clarity: 0.95 });
  }
  assert.equal(sample?.note, "C5");
});

test("a brief unvoiced gap holds the note; a real silence clears it", () => {
  const stream = new PitchStream();
  for (let i = 0; i < 4; i++) {
    stream.push({ timestamp: i, frequency: 440, clarity: 0.95 });
  }

  const held = stream.push({ timestamp: 5, frequency: null, clarity: 0.1 });
  assert.equal(held.note, "A4", "a single consonant blanked the display");
  assert.equal(held.timestamp, 5, "the held sample kept a stale timestamp");

  let last = held;
  for (let i = 0; i < UNVOICED_FRAMES_TO_CLEAR; i++) {
    last = stream.push({ timestamp: 6 + i, frequency: null, clarity: 0.1 });
  }
  assert.equal(last.voiced, false);
  assert.equal(last.note, null, "silence kept showing a note");
});

test("after a clearing silence the stream starts from scratch", () => {
  const stream = new PitchStream();
  for (let i = 0; i < 5; i++) {
    stream.push({ timestamp: i, frequency: 440, clarity: 0.95 });
  }
  for (let i = 0; i < 5; i++) {
    stream.push({ timestamp: 10 + i, frequency: null, clarity: 0 });
  }
  const first = stream.push({ timestamp: 20, frequency: 523.25, clarity: 0.95 });
  assert.equal(
    first.note,
    "C5",
    "stale frequencies from before the silence leaked into the median",
  );
});

test("reset discards all history", () => {
  const stream = new PitchStream();
  for (let i = 0; i < 5; i++) {
    stream.push({ timestamp: i, frequency: 440, clarity: 0.95 });
  }
  stream.reset();
  const first = stream.push({ timestamp: 0, frequency: 523.25, clarity: 0.95 });
  assert.equal(first.note, "C5");
});

// --- Summary ---------------------------------------------------------------

function voicedSample(frequency: number, timestamp = 0): LivePitchSample {
  const reading = describeFrequency(frequency);
  assert.ok(reading, `${frequency} Hz is not a usable test fixture`);
  return {
    timestamp,
    frequency: reading.frequency,
    midi: reading.midi,
    note: reading.note,
    cents: reading.cents,
    confidence: 0.95,
    voiced: true,
  };
}

function unvoicedSample(timestamp = 0): LivePitchSample {
  return {
    timestamp,
    frequency: null,
    midi: null,
    note: null,
    cents: null,
    confidence: 0,
    voiced: false,
  };
}

test("an empty session summarises to nothing measured, not to zero", () => {
  const summary = summarise([]);
  assert.equal(summary.lowestNote, null);
  assert.equal(summary.highestNote, null);
  assert.equal(summary.mostFrequentNote, null);
  assert.equal(summary.averageCents, null);
  assert.equal(summary.voicedFraction, null);
  assert.equal(summary.totalFrames, 0);
});

test("a session with no voiced frames reports a fraction but no notes", () => {
  const summary = summarise([unvoicedSample(0), unvoicedSample(1)]);
  assert.equal(summary.voicedFraction, 0);
  assert.equal(summary.voicedFrames, 0);
  assert.equal(summary.totalFrames, 2);
  assert.equal(summary.lowestNote, null);
  assert.equal(summary.highestNote, null);
});

test("the summary reports the range actually voiced", () => {
  const summary = summarise([
    voicedSample(220, 0),
    voicedSample(440, 1),
    voicedSample(440, 2),
    voicedSample(880, 3),
    unvoicedSample(4),
  ]);
  assert.equal(summary.lowestNote, "A3");
  assert.equal(summary.highestNote, "A5");
  assert.equal(summary.mostFrequentNote, "A4");
  assert.equal(summary.voicedFrames, 4);
  assert.equal(summary.totalFrames, 5);
  assert.ok(summary.voicedFraction !== null);
  assert.ok(Math.abs(summary.voicedFraction - 0.8) < 1e-9);
});

test("unvoiced frames never enter the range", () => {
  const summary = summarise([unvoicedSample(0), voicedSample(440, 1), unvoicedSample(2)]);
  assert.equal(summary.lowestNote, "A4");
  assert.equal(summary.highestNote, "A4");
  assert.ok(summary.averageCents !== null);
  assert.ok(Math.abs(summary.averageCents) < 1e-9);
});
