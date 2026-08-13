/**
 * Audio-analysis presentation logic.
 *
 * Two guarantees are asserted here more than anything else:
 *
 * - **`null` never renders as zero.** A measurement the signal could not
 *   support says "Not measured". A recording where nothing was voiced must not
 *   claim 0% consistency or a range of zero semitones.
 * - **No message we did not write reaches a user.** The error map returns our
 *   own copy or generic copy, never the server's wording.
 *
 * The polling engine is also re-tested here through the audio response type,
 * since Step 7I made it generic and a generic that only ever sees one type is a
 * generic that is about to break on the second.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AUDIO_ANALYSIS_ERROR_MESSAGES,
  isNotMeasurable,
  messageForAudioAnalysisError,
} from "../lib/audio-analysis-errors.ts";
import {
  IN_TUNE_CENTS,
  NOT_MEASURED,
  hasNoteBreakdown,
  hasPitchResult,
  loudnessRows,
  noteRows,
  rangeLabel,
  rangeRows,
  spectralRows,
  stabilityRows,
} from "../lib/audio-analysis-metrics.ts";
import {
  AUDIO_STAGE_MESSAGES,
  audioStageMessage,
  createAnalysisRunner,
  isActiveStatus,
  isTerminalStatus,
  type AudioAnalysisApi,
  type AudioRunState,
} from "../lib/analysis-runner.ts";
import type {
  AudioAnalysisResponse,
  AudioSummary,
  DetectedRange,
  LoudnessMetrics,
  NoteBreakdown,
  NoteSummary,
  PitchStabilityMetrics,
  SpectralMetrics,
} from "../types/api.ts";

// --- Fixtures --------------------------------------------------------------

const RANGE: DetectedRange = {
  lowest_frequency_hz: 98.0,
  highest_frequency_hz: 523.25,
  lowest_note: "G2",
  highest_note: "C5",
  semitone_span: 29,
};

function stability(
  overrides: Partial<PitchStabilityMetrics> = {},
): PitchStabilityMetrics {
  return {
    voiced_ratio: 0.74,
    total_frames: 100,
    voiced_frames: 74,
    mean_cents_deviation: -17.2,
    mean_abs_cents_deviation: 19.4,
    cents_std: 21.4,
    semitone_variance: 3.2,
    in_tune_ratio: 0.82,
    unstable_sections: [],
    ...overrides,
  };
}

const LOUDNESS: LoudnessMetrics = {
  rms: 0.18,
  peak: 0.92,
  dynamic_range_db: 14.2,
  crest_factor_db: 8.1,
  clipped_sample_ratio: 0,
};

const SPECTRAL: SpectralMetrics = {
  centroid_hz: 1420.5,
  bandwidth_hz: 980.2,
  rolloff_hz: 3100.9,
  zero_crossing_rate: 0.08,
  flatness: 0.031,
};

function summary(overrides: Partial<AudioSummary> = {}): AudioSummary {
  return {
    duration_seconds: 12.5,
    settings: {
      sample_rate_hz: 44100,
      frame_length_samples: 4096,
      hop_length_samples: 1024,
      min_frequency_hz: 65,
      max_frequency_hz: 1100,
      clarity_threshold: 0.8,
      silence_rms: 0.005,
    },
    range: RANGE,
    stability: stability(),
    loudness: LOUDNESS,
    spectral: SPECTRAL,
    ...overrides,
  };
}

function response(overrides: Partial<AudioAnalysisResponse> = {}): AudioAnalysisResponse {
  return {
    audio_analysis_id: "a".repeat(32),
    recording_id: "b".repeat(32),
    status: "completed",
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:01Z",
    completed_at: "2026-01-01T00:00:03Z",
    error_code: null,
    summary: summary(),
    pitch_point_count: 420,
    ...overrides,
  };
}

// --- Range -----------------------------------------------------------------

test("a measured range reads as two notes", () => {
  assert.equal(rangeLabel(RANGE), "G2 — C5");
});

test("an absent range is null, not a zero span", () => {
  assert.equal(rangeLabel(null), null);
  const rows = rangeRows(null);
  assert.ok(rows.every((row) => row.value === NOT_MEASURED));
  assert.ok(rows.every((row) => row.measured === false));
  assert.ok(!rows.some((row) => row.value.includes("0")));
});

test("a measured range shows its notes and frequencies", () => {
  const rows = rangeRows(RANGE);
  assert.equal(rows[0].value, "G2");
  assert.equal(rows[1].value, "C5");
  assert.equal(rows[2].value, "29 semitones");
  assert.ok(rows.every((row) => row.measured));
  assert.ok(rows[0].hint?.includes("98.0 Hz"));
});

test("a one-semitone span is singular", () => {
  const rows = rangeRows({ ...RANGE, semitone_span: 1 });
  assert.equal(rows[2].value, "1 semitone");
});

test("a recording with no pitch has no pitch result", () => {
  assert.equal(hasPitchResult(summary()), true);
  assert.equal(hasPitchResult(summary({ range: null })), false);
  assert.equal(hasPitchResult(null), false);
});

// --- Stability -------------------------------------------------------------

test("pitch consistency always carries its definition", () => {
  const rows = stabilityRows(stability());
  const consistency = rows.find((row) => row.label === "Pitch consistency");
  assert.ok(consistency);
  assert.equal(consistency.value, "82%");
  assert.ok(
    consistency.hint?.includes(`${IN_TUNE_CENTS} cents`),
    "the number is meaningless without the threshold",
  );
});

test("nothing voiced reports Not measured rather than 0%", () => {
  const rows = stabilityRows(
    stability({
      voiced_ratio: 0,
      voiced_frames: 0,
      in_tune_ratio: null,
      mean_cents_deviation: null,
      mean_abs_cents_deviation: null,
      cents_std: null,
      semitone_variance: null,
    }),
  );

  const consistency = rows.find((row) => row.label === "Pitch consistency");
  assert.equal(consistency?.value, NOT_MEASURED);
  assert.equal(consistency?.measured, false);

  const deviation = rows.find((row) => row.label === "Average deviation");
  assert.equal(deviation?.value, NOT_MEASURED);
  assert.equal(deviation?.measured, false);

  // The voiced ratio itself IS a genuine zero and must still say so.
  const voiced = rows.find((row) => row.label === "Frames with a pitch");
  assert.equal(voiced?.value, "0%");
  assert.equal(voiced?.measured, true);
});

test("deviation is signed the way a tuner reads", () => {
  const flat = stabilityRows(stability({ mean_cents_deviation: -17.2 }));
  const sharp = stabilityRows(stability({ mean_cents_deviation: 17.2 }));
  const zero = stabilityRows(stability({ mean_cents_deviation: 0 }));

  assert.equal(flat.find((r) => r.label === "Average deviation")?.value, "−17.2 cents");
  assert.equal(sharp.find((r) => r.label === "Average deviation")?.value, "+17.2 cents");
  assert.equal(zero.find((r) => r.label === "Average deviation")?.value, "0 cents");
});

test("a measured zero deviation is measured, not missing", () => {
  const rows = stabilityRows(stability({ mean_cents_deviation: 0 }));
  const row = rows.find((r) => r.label === "Average deviation");
  assert.equal(row?.measured, true);
});

// --- Loudness and spectrum -------------------------------------------------

test("loudness says outright that it is not LUFS", () => {
  const rows = loudnessRows(LOUDNESS);
  const rms = rows.find((row) => row.label === "Level (RMS)");
  assert.ok(rms?.hint?.includes("LUFS"));
});

test("an unavailable dynamic range says so rather than showing 0 dB", () => {
  const rows = loudnessRows({ ...LOUDNESS, dynamic_range_db: null });
  const row = rows.find((r) => r.label === "Dynamic range");
  assert.equal(row?.value, NOT_MEASURED);
  assert.equal(row?.measured, false);
});

test("a genuine zero clipping ratio is shown as zero", () => {
  const rows = loudnessRows({ ...LOUDNESS, clipped_sample_ratio: 0 });
  const row = rows.find((r) => r.label === "Clipped samples");
  assert.equal(row?.value, "0%");
  assert.equal(row?.measured, true);
});

test("spectral rows never carry a timbre word", () => {
  const rows = spectralRows(SPECTRAL);
  const text = JSON.stringify(rows).toLowerCase();
  for (const label of ["bright", "dark", "breathy", "nasal", "warm", "harsh", "healthy"]) {
    assert.ok(!text.includes(label), `spectral rows described the voice as "${label}"`);
  }
});

test("absent spectral features read as Not measured", () => {
  const rows = spectralRows(null);
  assert.ok(rows.every((row) => row.value === NOT_MEASURED && !row.measured));
});

test("no row anywhere presents a score or a grade", () => {
  const everything = JSON.stringify([
    ...rangeRows(RANGE),
    ...stabilityRows(stability()),
    ...loudnessRows(LOUDNESS),
    ...spectralRows(SPECTRAL),
  ]).toLowerCase();
  for (const word of ["score", "grade", "rating", "overall", "out of 10"]) {
    assert.ok(!everything.includes(word), `a row mentioned "${word}"`);
  }
});

// --- Errors ----------------------------------------------------------------

test("insufficient pitch is explained as an outcome, not a crash", () => {
  const message = messageForAudioAnalysisError("INSUFFICIENT_PITCH_SIGNAL");
  assert.ok(message.startsWith("Not measured"));
  assert.ok(message.includes("whisper"));
});

test("insufficient pitch and too-short are not-measurable, others are errors", () => {
  assert.equal(isNotMeasurable("INSUFFICIENT_PITCH_SIGNAL"), true);
  assert.equal(isNotMeasurable("AUDIO_TOO_SHORT"), true);
  assert.equal(isNotMeasurable("AUDIO_UNSUPPORTED"), false);
  assert.equal(isNotMeasurable("INTERNAL_ERROR"), false);
  assert.equal(isNotMeasurable(null), false);
  assert.equal(isNotMeasurable(undefined), false);
});

test("an unknown code gets our generic copy, never the server's words", () => {
  const message = messageForAudioAnalysisError("SOMETHING_NEW");
  assert.ok(!message.includes("SOMETHING_NEW"));
  assert.equal(message, messageForAudioAnalysisError(undefined));
  assert.equal(message, messageForAudioAnalysisError(null));
});

test("no error message exposes an implementation detail", () => {
  const text = Object.values(AUDIO_ANALYSIS_ERROR_MESSAGES).join(" ").toLowerCase();
  for (const leak of ["soundfile", "libsndfile", "numpy", "traceback", "http", "/", "nsdf"]) {
    assert.ok(!text.includes(leak), `error copy mentioned "${leak}"`);
  }
});

// --- The shared runner, driven with audio responses ------------------------

test("audio stages describe measuring, not transcribing", () => {
  assert.equal(audioStageMessage("pending"), AUDIO_STAGE_MESSAGES.pending);
  assert.ok(audioStageMessage("analyzing").toLowerCase().includes("measuring"));
  assert.ok(!audioStageMessage("analyzing").toLowerCase().includes("transcrib"));
  // No stage message may contain a percentage; the server reports a stage.
  for (const message of Object.values(AUDIO_STAGE_MESSAGES)) {
    assert.ok(!/\d+\s*%/.test(message), `"${message}" implies a percentage`);
  }
});

test("the audio statuses classify the same way as the speech ones", () => {
  assert.equal(isActiveStatus("pending"), true);
  assert.equal(isActiveStatus("analyzing"), true);
  assert.equal(isTerminalStatus("completed"), true);
  assert.equal(isTerminalStatus("failed"), true);
  assert.equal(isActiveStatus("completed"), false);
});

function fakeClock() {
  const queue: (() => void)[] = [];
  return {
    // The delay is ignored on purpose: these tests assert the *sequence* of
    // requests, not the backoff curve, which `pollDelay` is tested for directly.
    setTimer: (callback: () => void) => {
      queue.push(callback);
      return queue.length;
    },
    clearTimer: () => {},
    tick: () => {
      const next = queue.shift();
      next?.();
    },
    get pending() {
      return queue.length;
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

/** The runner hands `describeError` whatever failed — a code or a thrown value. */
const describe = (error: unknown) =>
  messageForAudioAnalysisError(typeof error === "string" ? error : undefined);

test("the runner drives an audio analysis to completion", async () => {
  const clock = fakeClock();
  const states: AudioRunState[] = [];
  let gets = 0;

  const api: AudioAnalysisApi = {
    start: async () => response({ status: "pending", summary: null, pitch_point_count: 0 }),
    get: async () => {
      gets += 1;
      return gets === 1
        ? response({ status: "analyzing", summary: null, pitch_point_count: 0 })
        : response();
    },
  };

  const runner = createAnalysisRunner<AudioAnalysisResponse>({
    recordingId: "r".repeat(32),
    api,
    onState: (state) => states.push(state),
    describeError: describe,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  runner.start();
  await settle();
  clock.tick();
  await settle();
  clock.tick();
  await settle();

  assert.deepEqual(
    states.map((state) => state.status),
    ["starting", "running", "running", "completed"],
  );
  assert.equal(clock.pending, 0, "polling continued after completion");
});

test("a failed audio analysis carries our copy, not the code", async () => {
  const states: AudioRunState[] = [];
  const api: AudioAnalysisApi = {
    start: async () =>
      response({
        status: "failed",
        summary: null,
        pitch_point_count: 0,
        error_code: "INSUFFICIENT_PITCH_SIGNAL",
      }),
    get: async () => {
      throw new Error("should not poll a finished analysis");
    },
  };

  const runner = createAnalysisRunner<AudioAnalysisResponse>({
    recordingId: "r".repeat(32),
    api,
    onState: (state) => states.push(state),
    describeError: describe,
    setTimer: () => 0,
    clearTimer: () => {},
  });

  runner.start();
  await settle();

  const last = states[states.length - 1];
  assert.equal(last.status, "failed");
  assert.ok(last.status === "failed" && last.message.startsWith("Not measured"));
  assert.ok(last.status === "failed" && !last.message.includes("INSUFFICIENT"));
});

test("stopping the runner silences it", async () => {
  const states: AudioRunState[] = [];
  const runner = createAnalysisRunner<AudioAnalysisResponse>({
    recordingId: "r".repeat(32),
    api: {
      start: async () => response({ status: "pending", summary: null, pitch_point_count: 0 }),
      get: async () => response(),
    },
    onState: (state) => states.push(state),
    describeError: describe,
    setTimer: () => 0,
    clearTimer: () => {},
  });

  runner.start();
  runner.stop();
  await settle();

  assert.deepEqual(
    states.map((state) => state.status),
    ["starting"],
    "state reached the caller after stop()",
  );
});

test("a second start does not begin a second measurement", async () => {
  let starts = 0;
  const runner = createAnalysisRunner<AudioAnalysisResponse>({
    recordingId: "r".repeat(32),
    api: {
      start: async () => {
        starts += 1;
        return response({ status: "analyzing", summary: null, pitch_point_count: 0 });
      },
      get: async () => response(),
    },
    onState: () => {},
    describeError: describe,
    setTimer: () => 0,
    clearTimer: () => {},
  });

  runner.start();
  runner.start();
  await settle();
  runner.start();
  await settle();

  assert.equal(starts, 1);
});

// --- Note breakdown --------------------------------------------------------

function note(overrides: Partial<NoteSummary> = {}): NoteSummary {
  return {
    midi_note: 60,
    note_name: "C4",
    duration_seconds: 1.84,
    percentage_of_voiced_time: 31.2,
    frame_count: 92,
    average_cents: -4.1,
    mean_abs_cents: 8.7,
    in_tune_ratio: 0.84,
    ...overrides,
  };
}

function breakdown(notes: NoteSummary[]): NoteBreakdown {
  return {
    recording_id: "b".repeat(32),
    audio_analysis_id: "a".repeat(32),
    voiced_seconds: notes.reduce((total, item) => total + item.duration_seconds, 0),
    total_frames: notes.reduce((total, item) => total + item.frame_count, 0),
    in_tune_cents: 25,
    notes,
  };
}

test("a note row carries every figure the table shows", () => {
  const [row] = noteRows([note()]);
  assert.equal(row.note, "C4");
  assert.equal(row.midi, 60);
  assert.equal(row.duration, "1.84s");
  assert.equal(row.share, "31%");
  assert.equal(row.inTune, "84%");
  assert.equal(row.deviation, "−4.1 cents");
  assert.equal(row.frameCount, 92);
});

test("the bar width is the real share, not the rounded label", () => {
  const [row] = noteRows([note({ percentage_of_voiced_time: 31.24 })]);
  assert.equal(row.share, "31%");
  assert.equal(row.sharePercent, 31.24);
});

test("deviation is signed the way a tuner reads", () => {
  assert.equal(noteRows([note({ average_cents: 4.1 })])[0].deviation, "+4.1 cents");
  assert.equal(noteRows([note({ average_cents: -4.1 })])[0].deviation, "−4.1 cents");
  assert.equal(noteRows([note({ average_cents: 0 })])[0].deviation, "0 cents");
});

test("the server's order is preserved rather than re-sorted", () => {
  // Deliberately not in duration order: the server decides, and a second sort
  // here would be a second place for the order to differ.
  const rows = noteRows([
    note({ midi_note: 64, note_name: "E4", duration_seconds: 0.5 }),
    note({ midi_note: 60, note_name: "C4", duration_seconds: 2.0 }),
  ]);
  assert.deepEqual(
    rows.map((row) => row.note),
    ["E4", "C4"],
  );
});

test("a note held for a single frame is still a row", () => {
  const rows = noteRows([note({ frame_count: 1, duration_seconds: 0.02, percentage_of_voiced_time: 0.4 })]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].frameCount, 1);
  assert.equal(rows[0].share, "0%");
});

test("an empty breakdown is not a breakdown", () => {
  assert.equal(hasNoteBreakdown(null), false);
  assert.equal(hasNoteBreakdown(breakdown([])), false, "zero notes is not a measurement");
  assert.equal(hasNoteBreakdown(breakdown([note()])), true);
});

test("shares across a real breakdown reach 100", () => {
  const notes = [
    note({ midi_note: 60, note_name: "C4", percentage_of_voiced_time: 50 }),
    note({ midi_note: 62, note_name: "D4", percentage_of_voiced_time: 30 }),
    note({ midi_note: 64, note_name: "E4", percentage_of_voiced_time: 20 }),
  ];
  const total = noteRows(notes).reduce((sum, row) => sum + row.sharePercent, 0);
  assert.ok(Math.abs(total - 100) < 0.01);
});

test("the typical distance from a note is surfaced beside the signed one", () => {
  const rows = stabilityRows(stability({ mean_abs_cents_deviation: 19.4 }));
  const abs = rows.find((row) => row.label === "Typical distance from a note");
  assert.ok(abs);
  assert.equal(abs.value, "19.4 cents");
  assert.ok(abs.hint?.includes("ignoring"));

  // And it still says nothing rather than zero when there was nothing to measure.
  const none = stabilityRows(stability({ mean_abs_cents_deviation: null }));
  const missing = none.find((row) => row.label === "Typical distance from a note");
  assert.equal(missing?.value, NOT_MEASURED);
  assert.equal(missing?.measured, false);
});

test("no note-breakdown label reads as a grade", () => {
  const text = JSON.stringify(noteRows([note()])).toLowerCase();
  for (const word of ["score", "grade", "rating", "ability", "accuracy"]) {
    assert.ok(!text.includes(word), `a note row mentioned "${word}"`);
  }
});

test("the runner keeps polling while feedback is generating", () => {
  // A status missing from the active list stops the loop silently, which is
  // the worst possible failure: the UI waits forever and never asks again.
  assert.equal(isActiveStatus("generating"), true);
  assert.equal(isTerminalStatus("generating"), false);
  assert.equal(isActiveStatus("not_requested"), false);
});
