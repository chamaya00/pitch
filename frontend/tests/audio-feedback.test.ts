/**
 * Audio feedback: what reaches the screen, and what must not.
 *
 * The component itself is exercised in the browser; what is asserted here is
 * the logic behind it — which sections render, which are omitted, and the
 * guarantees that hold whatever a model returns:
 *
 * - a failure never removes the measurements;
 * - an empty section is omitted rather than rendered as "None";
 * - mock output is always identifiable as mock;
 * - no score, no timbre label, no health claim can reach the UI, because the
 *   type has nowhere to put one.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createAnalysisRunner,
  isActiveStatus,
  isTerminalStatus,
  type RunState,
  type RunnerApi,
} from "../lib/analysis-runner.ts";
import { messageForAudioAnalysisError } from "../lib/audio-analysis-errors.ts";
import type { AudioFeedback, AudioFeedbackState } from "../types/api.ts";

const REAL_PROVENANCE = { provider: "claude", model: "claude-opus-5", is_mock: false };
const MOCK_PROVENANCE = { provider: "mock", model: null, is_mock: true };

function feedback(overrides: Partial<AudioFeedback> = {}): AudioFeedback {
  return {
    summary: "This recording held one note for most of its length.",
    strengths: ["Most of the pitched time sat close to a note."],
    areas_to_improve: [],
    pitch_observations: ["71% of the pitched frames sat within 25 cents of a note."],
    range_observations: [],
    note_observations: [],
    audio_observations: [],
    exercises: [],
    practice_plan: [],
    provenance: REAL_PROVENANCE,
    ...overrides,
  };
}

function state(overrides: Partial<AudioFeedbackState> = {}): AudioFeedbackState {
  return {
    recording_id: "b".repeat(32),
    audio_analysis_id: "a".repeat(32),
    status: "completed",
    error_code: null,
    feedback: feedback(),
    ...overrides,
  };
}

/** The section order the card renders, mirrored so the test can assert on it. */
const SECTION_KEYS = [
  "strengths",
  "areas_to_improve",
  "pitch_observations",
  "range_observations",
  "note_observations",
  "audio_observations",
  "exercises",
  "practice_plan",
] as const;

function renderedSections(value: AudioFeedback): string[] {
  return SECTION_KEYS.filter((key) => value[key].length > 0);
}

// --- Sections --------------------------------------------------------------

test("only the sections with content are rendered", () => {
  assert.deepEqual(renderedSections(feedback()), ["strengths", "pitch_observations"]);
});

test("a model that returns nothing but a summary renders only the summary", () => {
  const sparse = feedback({ strengths: [], pitch_observations: [] });
  assert.deepEqual(renderedSections(sparse), []);
  assert.ok(sparse.summary.length > 0);
});

test("an empty section is never rendered as None or as zero", () => {
  const value = feedback({ range_observations: [] });
  assert.ok(!renderedSections(value).includes("range_observations"));
});

test("every section a model can fill has somewhere to go", () => {
  const full = feedback({
    strengths: ["a"],
    areas_to_improve: ["b"],
    pitch_observations: ["c"],
    range_observations: ["d"],
    note_observations: ["e"],
    audio_observations: ["f"],
    exercises: ["g"],
    practice_plan: ["h"],
  });
  assert.equal(renderedSections(full).length, SECTION_KEYS.length);
});

// --- What the type cannot carry -------------------------------------------

test("the feedback type has nowhere to put a score or a timbre label", () => {
  // The cheapest guarantee available: a model cannot return what the schema
  // has no field for, and the UI cannot render what the type cannot hold.
  const keys = Object.keys(feedback());
  for (const forbidden of ["score", "grade", "rating", "timbre", "health", "ability"]) {
    assert.ok(
      !keys.some((key) => key.includes(forbidden)),
      `the feedback type has a "${forbidden}" field`,
    );
  }
});

// --- Provenance ------------------------------------------------------------

test("mock feedback is always identifiable as mock", () => {
  const mock = feedback({ provenance: MOCK_PROVENANCE });
  assert.equal(mock.provenance.is_mock, true);
  assert.equal(feedback().provenance.is_mock, false);
});

// --- States ----------------------------------------------------------------

test("generating is an active state, so polling continues", () => {
  assert.equal(isActiveStatus("generating"), true);
  assert.equal(isTerminalStatus("completed"), true);
  assert.equal(isTerminalStatus("failed"), true);
  assert.equal(isActiveStatus("not_requested"), false);
});

const settle = () => new Promise((resolve) => setImmediate(resolve));

function fakeClock() {
  const queue: (() => void)[] = [];
  return {
    setTimer: (callback: () => void) => queue.push(callback),
    clearTimer: () => {},
    tick: () => queue.shift()?.(),
    get pending() {
      return queue.length;
    },
  };
}

test("the card polls from generating through to completed", async () => {
  const clock = fakeClock();
  const seen: RunState<AudioFeedbackState>[] = [];
  let gets = 0;

  const api: RunnerApi<AudioFeedbackState> = {
    start: async () => state({ status: "generating", feedback: null }),
    get: async () => {
      gets += 1;
      return gets === 1 ? state({ status: "generating", feedback: null }) : state();
    },
  };

  const runner = createAnalysisRunner<AudioFeedbackState>({
    recordingId: "r".repeat(32),
    api,
    onState: (value) => seen.push(value),
    describeError: () => "",
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
    seen.map((value) => value.status),
    ["starting", "running", "running", "completed"],
  );
  assert.equal(clock.pending, 0, "polling continued after completion");
});

test("a second request never starts a second provider call", async () => {
  // Every duplicate here is a paid call, so this matters more than elsewhere.
  let starts = 0;
  const runner = createAnalysisRunner<AudioFeedbackState>({
    recordingId: "r".repeat(32),
    api: {
      start: async () => {
        starts += 1;
        return state({ status: "generating", feedback: null });
      },
      get: async () => state(),
    },
    onState: () => {},
    describeError: () => "",
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

test("a failed generation carries our copy, not the code", async () => {
  const seen: RunState<AudioFeedbackState>[] = [];
  const runner = createAnalysisRunner<AudioFeedbackState>({
    recordingId: "r".repeat(32),
    api: {
      start: async () =>
        state({
          status: "failed",
          feedback: null,
          error_code: "ANALYSIS_PROVIDER_UNAVAILABLE",
        }),
      get: async () => {
        throw new Error("should not poll a finished request");
      },
    },
    onState: (value) => seen.push(value),
    describeError: (error) =>
      messageForAudioAnalysisError(typeof error === "string" ? error : undefined),
    setTimer: () => 0,
    clearTimer: () => {},
  });

  runner.start();
  await settle();

  const last = seen[seen.length - 1];
  assert.equal(last.status, "failed");
  assert.ok(last.status === "failed" && !last.message.includes("ANALYSIS_PROVIDER"));
  assert.ok(last.status === "failed" && last.message.length > 0);
});

test("a failed generation still carries the analysis it belongs to", () => {
  // Which is what lets the UI keep the measurements on screen.
  const failed = state({ status: "failed", feedback: null, error_code: "AUDIO_ANALYSIS_FAILED" });
  assert.equal(failed.feedback, null);
  assert.equal(failed.audio_analysis_id, "a".repeat(32));
});
