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
  appendPage,
  audioLabel,
  firstPage,
  formatWhen,
  hasBeenAnalysed,
  historyAnnouncement,
  speechLabel,
} from "../lib/history.ts";
import type { RecordingHistory, RecordingHistoryItem } from "../types/api.ts";

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


// --- Paging (10.13) --------------------------------------------------------
//
// What this replaced: the list rendered one page and called it "everything you
// have uploaded from this browser". With 137 recordings it showed 50 and
// announced "50 recordings", and nothing on screen or in the response could
// have said otherwise.

function page(
  ids: string[],
  nextCursor: string | null,
): RecordingHistory {
  const items = ids.map((id) =>
    item({ recording: { ...item().recording, recording_id: id.padEnd(32, "0") } }),
  );
  return { items, count: items.length, limit: 2, next_cursor: nextCursor };
}

test("a first page carries the server's answer about what follows", () => {
  assert.equal(firstPage(page(["a", "b"], "cursor-1")).nextCursor, "cursor-1");
  assert.equal(firstPage(page(["a", "b"], null)).nextCursor, null);
});

test("a full page is not treated as evidence that more exists", () => {
  // Two items at a limit of two, and the server says that is all there is.
  const loaded = firstPage(page(["a", "b"], null));

  assert.equal(loaded.nextCursor, null);
  assert.equal(historyAnnouncement(loaded), "2 recordings.");
});

test("appending a page keeps the order and advances the cursor", () => {
  const loaded = appendPage(firstPage(page(["a", "b"], "c1")), page(["c", "d"], "c2"));

  assert.deepEqual(
    loaded.items.map((entry) => entry.recording.recording_id[0]),
    ["a", "b", "c", "d"],
  );
  assert.equal(loaded.nextCursor, "c2");
});

test("the same page appended twice does not duplicate a recording", () => {
  const first = firstPage(page(["a", "b"], "c1"));
  const once = appendPage(first, page(["c", "d"], "c2"));
  const twice = appendPage(once, page(["c", "d"], "c2"));

  assert.equal(twice.items.length, 4);
  const ids = twice.items.map((entry) => entry.recording.recording_id);
  assert.equal(new Set(ids).size, ids.length);
});

test("the last page ends the list", () => {
  const loaded = appendPage(firstPage(page(["a", "b"], "c1")), page(["c"], null));

  assert.equal(loaded.nextCursor, null);
  assert.equal(historyAnnouncement(loaded), "3 recordings.");
});

test("a partial list says it is partial, and never states a total it does not know", () => {
  const loaded = firstPage(page(["a", "b"], "c1"));

  assert.equal(historyAnnouncement(loaded), "2 recordings shown. More can be loaded.");
  // No invented total: the server was never asked how many there are.
  assert.doesNotMatch(historyAnnouncement(loaded), /of \d+/);
});

test("an empty history reads as empty, not as a failure", () => {
  assert.equal(historyAnnouncement(firstPage(page([], null))), "You have no recordings yet.");
});

test("one recording is singular", () => {
  assert.equal(historyAnnouncement(firstPage(page(["a"], null))), "1 recording.");
  assert.equal(
    historyAnnouncement(firstPage(page(["a"], "c1"))),
    "1 recording shown. More can be loaded.",
  );
});
