/**
 * History presentation logic.
 *
 * One rule carries most of the weight: **`null` is not `pending` and not a
 * failure.** A recording nobody has analysed has to read as "nobody asked",
 * because the alternative tells somebody their recording failed when it never
 * ran.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NOT_RUN,
  audioLabel,
  formatWhen,
  hasBeenAnalysed,
  speechLabel,
} from "../lib/history.ts";
import type { RecordingHistoryItem } from "../types/api.ts";

function item(overrides: Partial<RecordingHistoryItem> = {}): RecordingHistoryItem {
  return {
    recording: {
      recording_id: "a".repeat(32),
      original_filename: "take.wav",
      format: "wav",
      duration_seconds: 2,
      sample_rate: 22050,
      channels: 1,
      size_bytes: 4096,
      bits_per_sample: 16,
      created_at: "2026-08-11T10:30:00Z",
    },
    speech_status: null,
    audio_status: null,
    feedback_status: null,
    last_analysed_at: null,
    ...overrides,
  };
}

// --- null is its own state -------------------------------------------------

test("a recording nobody analysed reads as not run, in neither tone of failure", () => {
  assert.deepEqual(speechLabel(null), NOT_RUN);
  assert.deepEqual(audioLabel(null), NOT_RUN);
  assert.equal(NOT_RUN.tone, "absent");
  assert.notEqual(NOT_RUN.tone, "bad");
});

test("not-run and failed are never the same label", () => {
  assert.notDeepEqual(speechLabel(null), speechLabel("failed"));
  assert.notDeepEqual(audioLabel(null), audioLabel("failed"));
});

test("not-run and pending are never the same label", () => {
  assert.notDeepEqual(speechLabel(null), speechLabel("pending"));
  assert.notDeepEqual(audioLabel(null), audioLabel("pending"));
});

// --- Known statuses --------------------------------------------------------

test("every speech status has its own wording", () => {
  const statuses = ["pending", "transcribing", "analyzing", "completed", "failed"];
  const words = statuses.map((status) => speechLabel(status).text);

  assert.equal(new Set(words).size, statuses.length);
});

test("a completed analysis reads as good and a failed one as bad", () => {
  assert.equal(speechLabel("completed").tone, "good");
  assert.equal(speechLabel("failed").tone, "bad");
  assert.equal(audioLabel("completed").tone, "good");
  assert.equal(audioLabel("failed").tone, "bad");
});

test("an in-flight analysis is neither good nor bad", () => {
  for (const status of ["pending", "transcribing", "analyzing"]) {
    assert.equal(speechLabel(status).tone, "running");
  }
});

test("an unrecognised status is shown as itself rather than guessed at", () => {
  const label = speechLabel("something-new");

  assert.equal(label.text, "something-new");
  assert.equal(label.tone, "absent", "an unknown status must not be coloured as a failure");
});

// --- Row summary -----------------------------------------------------------

test("a recording with no analyses has not been analysed", () => {
  assert.equal(hasBeenAnalysed(item()), false);
});

test("either analysis is enough to count", () => {
  assert.equal(hasBeenAnalysed(item({ speech_status: "completed" })), true);
  assert.equal(hasBeenAnalysed(item({ audio_status: "failed" })), true);
});

// --- Dates -----------------------------------------------------------------

test("a timestamp renders as something, and not as the raw string", () => {
  const rendered = formatWhen("2026-08-11T10:30:00Z");

  assert.notEqual(rendered, "2026-08-11T10:30:00Z");
  assert.match(rendered, /2026/);
});

test("an unparseable date says so rather than rendering NaN", () => {
  assert.equal(formatWhen("not a date"), "Unknown date");
  assert.equal(formatWhen(""), "Unknown date");
});
