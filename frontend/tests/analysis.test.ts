/**
 * Analysis logic tests.
 *
 * Run with Node's built-in test runner and TypeScript type-stripping, exactly
 * like `logic.test.ts` — the project gains no test-framework dependency.
 *
 * The polling engine is plain TypeScript for this reason: the behaviours that
 * matter (never two requests in flight, never a loop left running, never a
 * second job from an impatient click) are driven here directly with a fake
 * clock, rather than being inferred through a renderer.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ANALYSIS_ERROR_MESSAGES,
  messageForAnalysisError,
} from "../lib/analysis-errors.ts";
import {
  fillerSummary,
  NOT_MEASURED,
  paceRows,
  pauseRows,
} from "../lib/analysis-metrics.ts";
import {
  createAnalysisRunner,
  isActiveStatus,
  isTerminalStatus,
  MAX_POLL_DELAY_MS,
  pollDelay,
  stageMessage,
  type AnalysisApi,
  type AnalysisRunState,
} from "../lib/analysis-runner.ts";
import type {
  AnalysisResponse,
  AnalysisStatus,
  SpeechMetrics,
} from "../types/api.ts";

// --- Fixtures --------------------------------------------------------------

const REAL_PROVENANCE = {
  transcription: { provider: "deepgram", model: "nova-3", is_mock: false },
  feedback: { provider: "claude", model: "claude-opus-5", is_mock: false },
  is_mock: false,
};

const MOCK_PROVENANCE = {
  transcription: { provider: "mock", model: null, is_mock: true },
  feedback: { provider: "mock", model: null, is_mock: true },
  is_mock: true,
};

function analysis(
  status: AnalysisStatus,
  overrides: Partial<AnalysisResponse> = {},
): AnalysisResponse {
  return {
    analysis_id: "a".repeat(32),
    recording_id: "b".repeat(32),
    status,
    created_at: "2026-08-10T09:00:00Z",
    started_at: null,
    completed_at: null,
    error_code: null,
    transcript: null,
    metrics: null,
    feedback: null,
    provenance: REAL_PROVENANCE,
    ...overrides,
  };
}

function metrics(overrides: Partial<SpeechMetrics> = {}): SpeechMetrics {
  return {
    word_count: 120,
    duration_source: "word_timings",
    speaking_duration_seconds: 60,
    words_per_minute: 120,
    articulation_rate_wpm: 135,
    pause_threshold_seconds: 0.5,
    pause_count: 3,
    total_pause_seconds: 4.5,
    longest_pause_seconds: 2,
    mean_pause_seconds: 1.5,
    filler_words: null,
    ...overrides,
  };
}

/** A fake clock: timers fire only when the test says so. */
function fakeClock() {
  const pending = new Map<number, () => void>();
  let nextHandle = 1;
  return {
    // The delay is deliberately ignored: these tests assert *whether* a poll is
    // scheduled, and `pollDelay` is covered separately.
    setTimer(callback: () => void): number {
      const handle = nextHandle++;
      pending.set(handle, callback);
      return handle;
    },
    clearTimer(handle: number): void {
      pending.delete(handle);
    },
    get scheduled(): number {
      return pending.size;
    },
    /** Fire every scheduled timer once. */
    tick(): void {
      const callbacks = [...pending.values()];
      pending.clear();
      for (const callback of callbacks) callback();
    },
  };
}

function runnerFor(
  responses: AnalysisResponse[],
  options: { clock?: ReturnType<typeof fakeClock> } = {},
) {
  const clock = options.clock ?? fakeClock();
  const states: AnalysisRunState[] = [];
  const calls: string[] = [];
  let index = 0;

  const api: AnalysisApi = {
    async start() {
      calls.push("start");
      return responses[Math.min(index++, responses.length - 1)];
    },
    async get() {
      calls.push("get");
      return responses[Math.min(index++, responses.length - 1)];
    },
  };

  const runner = createAnalysisRunner({
    recordingId: "b".repeat(32),
    api,
    onState: (state) => states.push(state),
    describeError: (error) =>
      messageForAnalysisError(typeof error === "string" ? error : undefined),
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  return { runner, states, calls, clock };
}

/** Let queued promise callbacks run. */
const settle = () => new Promise((resolve) => setImmediate(resolve));

// --- Status helpers --------------------------------------------------------

test("terminal and active statuses are exactly the documented ones", () => {
  assert.equal(isTerminalStatus("completed"), true);
  assert.equal(isTerminalStatus("failed"), true);
  for (const status of ["pending", "transcribing", "analyzing"] as const) {
    assert.equal(isTerminalStatus(status), false);
    assert.equal(isActiveStatus(status), true);
  }
  assert.equal(isActiveStatus("completed"), false);
  assert.equal(isActiveStatus("failed"), false);
});

test("each running stage has its own message", () => {
  assert.equal(stageMessage("pending"), "Preparing your recording…");
  assert.equal(stageMessage("transcribing"), "Transcribing your speech…");
  assert.equal(stageMessage("analyzing"), "Generating your feedback…");
});

test("no stage message claims a percentage", () => {
  for (const status of [
    "pending",
    "transcribing",
    "analyzing",
    "completed",
    "failed",
  ] as const) {
    assert.ok(
      !/\d/.test(stageMessage(status)),
      `${status} message contains a number: ${stageMessage(status)}`,
    );
  }
});

test("poll delay backs off and is capped", () => {
  assert.ok(pollDelay(0) < pollDelay(1));
  assert.ok(pollDelay(1) < pollDelay(2));
  assert.equal(pollDelay(50), MAX_POLL_DELAY_MS);
  assert.ok(pollDelay(0) >= 300, "the first poll should not be instant");
});

// --- Running an analysis ---------------------------------------------------

test("starting an analysis posts once and reports the accepted record", async () => {
  const { runner, states, calls } = runnerFor([analysis("pending")]);

  runner.start();
  assert.deepEqual(states.at(-1), { status: "starting" });

  await settle();
  assert.deepEqual(calls, ["start"]);
  assert.equal(states.at(-1)?.status, "running");
});

test("polling begins only after the analysis is accepted", async () => {
  const { runner, clock, calls } = runnerFor([analysis("pending")]);

  assert.equal(clock.scheduled, 0, "nothing is scheduled before start()");
  runner.start();
  assert.equal(clock.scheduled, 0, "nothing is scheduled while the POST is open");

  await settle();
  assert.equal(clock.scheduled, 1);
  assert.deepEqual(calls, ["start"]);
});

test("polling walks the stages and stops on completed", async () => {
  const { runner, states, calls, clock } = runnerFor([
    analysis("pending"),
    analysis("transcribing"),
    analysis("analyzing"),
    analysis("completed"),
  ]);

  runner.start();
  await settle();
  for (let i = 0; i < 3; i++) {
    clock.tick();
    await settle();
  }

  assert.deepEqual(calls, ["start", "get", "get", "get"]);
  assert.deepEqual(
    states.map((state) => state.status),
    ["starting", "running", "running", "running", "completed"],
  );
  assert.equal(clock.scheduled, 0, "no further poll is scheduled");
});

test("polling stops on a failed analysis", async () => {
  const { runner, states, clock } = runnerFor([
    analysis("pending"),
    analysis("failed", { error_code: "ANALYSIS_PROVIDER_TIMEOUT" }),
  ]);

  runner.start();
  await settle();
  clock.tick();
  await settle();

  const last = states.at(-1);
  assert.equal(last?.status, "failed");
  assert.equal(clock.scheduled, 0);
  assert.equal(
    last?.status === "failed" ? last.message : "",
    ANALYSIS_ERROR_MESSAGES.ANALYSIS_PROVIDER_TIMEOUT,
  );
});

test("an analysis that is already complete never schedules a poll", async () => {
  const { runner, states, calls, clock } = runnerFor([analysis("completed")]);

  runner.start();
  await settle();

  assert.deepEqual(calls, ["start"]);
  assert.equal(states.at(-1)?.status, "completed");
  assert.equal(clock.scheduled, 0);
});

test("clicking twice does not start a second analysis", async () => {
  const { runner, calls } = runnerFor([analysis("pending")]);

  runner.start();
  runner.start();
  await settle();
  runner.start();

  assert.deepEqual(calls, ["start"], "only one POST may be sent");
});

test("a finished analysis can be retried", async () => {
  const { runner, calls, clock } = runnerFor([
    analysis("failed", { error_code: "ANALYSIS_PROVIDER_ERROR" }),
  ]);

  runner.start();
  await settle();
  assert.deepEqual(calls, ["start"]);

  runner.start();
  await settle();
  assert.deepEqual(calls, ["start", "start"]);
  assert.equal(clock.scheduled, 0);
});

test("only one request is ever in flight", async () => {
  const clock = fakeClock();
  let open = 0;
  let maxOpen = 0;
  const api: AnalysisApi = {
    async start() {
      return analysis("pending");
    },
    async get() {
      open += 1;
      maxOpen = Math.max(maxOpen, open);
      await settle();
      open -= 1;
      return analysis("transcribing");
    },
  };

  const runner = createAnalysisRunner({
    recordingId: "b".repeat(32),
    api,
    onState: () => {},
    describeError: () => "failed",
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  runner.start();
  await settle();
  for (let i = 0; i < 4; i++) {
    clock.tick();
    await settle();
    await settle();
  }

  assert.equal(maxOpen, 1);
  runner.stop();
});

test("stopping cancels the loop and silences further updates", async () => {
  const { runner, states, calls, clock } = runnerFor([
    analysis("pending"),
    analysis("transcribing"),
  ]);

  runner.start();
  await settle();
  const seen = states.length;

  runner.stop();
  clock.tick();
  await settle();

  assert.equal(clock.scheduled, 0, "the pending timer is cleared");
  assert.equal(states.length, seen, "no state arrives after stop()");
  assert.deepEqual(calls, ["start"], "no request is made after stop()");
});

test("stopping mid-request discards the response", async () => {
  const clock = fakeClock();
  const states: AnalysisRunState[] = [];
  const api: AnalysisApi = {
    async start() {
      await settle();
      return analysis("pending");
    },
    async get() {
      return analysis("completed");
    },
  };

  const runner = createAnalysisRunner({
    recordingId: "b".repeat(32),
    api,
    onState: (state) => states.push(state),
    describeError: () => "failed",
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  runner.start();
  runner.stop();
  await settle();
  await settle();

  assert.deepEqual(
    states.map((state) => state.status),
    ["starting"],
    "the in-flight response must not reach the caller",
  );
});

test("a request failure becomes presentable copy, not an exception", async () => {
  const clock = fakeClock();
  const states: AnalysisRunState[] = [];
  const api: AnalysisApi = {
    async start() {
      throw new Error("connect ECONNREFUSED 127.0.0.1:8000");
    },
    async get() {
      throw new Error("unused");
    },
  };

  const runner = createAnalysisRunner({
    recordingId: "b".repeat(32),
    api,
    onState: (state) => states.push(state),
    describeError: () => messageForAnalysisError(undefined),
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  runner.start();
  await settle();

  const last = states.at(-1);
  assert.equal(last?.status, "error");
  assert.ok(last?.status === "error" && !last.message.includes("ECONNREFUSED"));
  assert.equal(clock.scheduled, 0);
});

// --- Error copy ------------------------------------------------------------

test("every analysis error code maps to safe copy", () => {
  const codes = [
    "ANALYSIS_NOT_CONFIGURED",
    "ANALYSIS_PROVIDER_UNAVAILABLE",
    "ANALYSIS_PROVIDER_TIMEOUT",
    "ANALYSIS_RATE_LIMITED",
    "ANALYSIS_PROVIDER_ERROR",
    "TRANSCRIPT_EMPTY",
    "ANALYSIS_NOT_FOUND",
    "RECORDING_NOT_FOUND",
  ];
  for (const code of codes) {
    const message = messageForAnalysisError(code);
    assert.ok(message.length > 0, code);
    assert.ok(!message.includes(code), `${code} leaked its code into the copy`);
  }
});

test("codes shared with the upload flow reuse that copy", () => {
  assert.equal(
    messageForAnalysisError("NETWORK_ERROR"),
    "We couldn't reach VocalLens. Check your connection and try again.",
  );
});

test("an unknown code falls back without exposing anything", () => {
  const message = messageForAnalysisError("SOME_NEW_CODE");
  assert.ok(message.length > 0);
  assert.ok(!message.includes("SOME_NEW_CODE"));
});

test("no server-authored text can reach the screen through an unknown code", () => {
  // Every one of these would pass the upload flow's presentability guard —
  // no path, no traceback, no exception class — while still naming a vendor.
  // Analysis copy is ours alone, so none of them can be reached at all.
  const generic = messageForAnalysisError("UNRECOGNISED");

  for (const leaky of [
    "deepgram: 401 Unauthorized",
    "invalid x-api-key sk-ant-123",
    "https://api.deepgram.com/v1/listen",
    "request 8f14e45f failed upstream",
  ]) {
    assert.notEqual(generic, leaky);
    assert.ok(!generic.includes(leaky));
  }
  assert.ok(!generic.toLowerCase().includes("deepgram"));
  assert.ok(!generic.toLowerCase().includes("claude"));
  assert.ok(!generic.includes("sk-ant"));
});

test("copy never names a provider, a model or a URL", () => {
  for (const message of Object.values(ANALYSIS_ERROR_MESSAGES)) {
    const lowered = message.toLowerCase();
    for (const forbidden of ["deepgram", "claude", "anthropic", "http", "api key"]) {
      assert.ok(
        !lowered.includes(forbidden),
        `"${message}" mentions ${forbidden}`,
      );
    }
  }
});

// --- Missing data is not zero ---------------------------------------------

test("a measured zero renders as zero", () => {
  const rows = pauseRows(
    metrics({
      pause_count: 0,
      total_pause_seconds: 0,
      longest_pause_seconds: null,
      mean_pause_seconds: null,
    }),
  );
  const pauses = rows.find((row) => row.label === "Pauses");

  assert.equal(pauses?.value, "0");
  assert.equal(pauses?.measured, true);
});

test("an unmeasured metric never renders as zero", () => {
  const rows = pauseRows(
    metrics({
      pause_count: null,
      total_pause_seconds: null,
      longest_pause_seconds: null,
      mean_pause_seconds: null,
    }),
  );

  for (const row of rows) {
    assert.equal(row.measured, false, row.label);
    assert.equal(row.value, NOT_MEASURED, row.label);
    assert.notEqual(row.value, "0");
  }
});

test("an unmeasured rate never renders as zero", () => {
  const rows = paceRows(
    metrics({
      speaking_duration_seconds: null,
      words_per_minute: null,
      articulation_rate_wpm: null,
      duration_source: null,
    }),
  );

  const wpm = rows.find((row) => row.label === "Words per minute");
  assert.equal(wpm?.value, NOT_MEASURED);
  assert.equal(wpm?.measured, false);

  // The word count is always measurable, so it stays a number.
  const words = rows.find((row) => row.label === "Words");
  assert.equal(words?.value, "120");
  assert.equal(words?.measured, true);
});

test("a word count of zero is still a measurement", () => {
  const rows = paceRows(metrics({ word_count: 0 }));
  const words = rows.find((row) => row.label === "Words");

  assert.equal(words?.value, "0");
  assert.equal(words?.measured, true);
});

test("absent filler statistics do not become zero", () => {
  const summary = fillerSummary(null);

  assert.equal(summary.measured, false);
  assert.equal(summary.hesitations, null);
  assert.equal(summary.discourseMarkers, null);
  assert.deepEqual(summary.terms, []);
  assert.ok(summary.note.length > 0, "the reason must be explained");
});

test("measured filler statistics keep their counts, including zero", () => {
  const summary = fillerSummary({
    hesitation_count: 0,
    discourse_marker_count: 2,
    total_count: 2,
    by_term: [{ term: "like", category: "discourse_marker", count: 2 }],
  });

  assert.equal(summary.measured, true);
  assert.equal(summary.hesitations, 0);
  assert.equal(summary.discourseMarkers, 2);
  assert.deepEqual(summary.terms, [
    { term: "like", count: 2, category: "discourse_marker" },
  ]);
});

test("the duration source is explained rather than assumed", () => {
  const fromFile = paceRows(metrics({ duration_source: "recording_duration" }));
  const fromWords = paceRows(metrics({ duration_source: "word_timings" }));

  assert.ok(fromFile.find((row) => row.label === "Speaking time")?.hint);
  assert.notEqual(
    fromFile.find((row) => row.label === "Speaking time")?.hint,
    fromWords.find((row) => row.label === "Speaking time")?.hint,
  );
});

// --- Provenance ------------------------------------------------------------

test("mock provenance is visible on the analysis", () => {
  const mock = analysis("completed", { provenance: MOCK_PROVENANCE });
  const real = analysis("completed", { provenance: REAL_PROVENANCE });

  assert.equal(mock.provenance.is_mock, true);
  assert.equal(real.provenance.is_mock, false);
});

test("a completed analysis without feedback is not a failure", () => {
  const completed = analysis("completed", {
    metrics: metrics(),
    feedback: null,
  });

  assert.equal(completed.status, "completed");
  assert.equal(isTerminalStatus(completed.status), true);
  assert.notEqual(completed.status, "failed");
  assert.equal(completed.error_code, null);
  assert.notEqual(completed.metrics, null);
});
