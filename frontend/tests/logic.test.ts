/**
 * Pure-logic tests.
 *
 * Run with Node's built-in test runner and TypeScript type-stripping, so the
 * project gains no test-framework dependency:
 *
 *     npm run test
 *
 * Only dependency-free modules are covered here. UI behaviour is verified in a
 * real browser instead, per the project's testing strategy.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  acceptAttribute,
  extensionOf,
  hasSupportedExtension,
  validateFileForUpload,
} from "../lib/audio-validation.ts";
import {
  formatBytes,
  formatChannels,
  formatDuration,
  formatDurationLimit,
  formatSampleRate,
} from "../lib/format.ts";
import {
  isPresentableMessage,
  messageForErrorCode,
  UPLOAD_ERROR_MESSAGES,
} from "../lib/upload-errors.ts";

// --- Extension validation --------------------------------------------------

test("extensionOf reads the final suffix, lower-cased", () => {
  assert.equal(extensionOf("take.wav"), ".wav");
  assert.equal(extensionOf("TAKE.WAV"), ".wav");
  assert.equal(extensionOf("my.song.take.mp3"), ".mp3");
  assert.equal(extensionOf("song.mp3.exe"), ".exe");
});

test("extensionOf returns empty for names without a usable extension", () => {
  assert.equal(extensionOf("recording"), "");
  assert.equal(extensionOf(".mp3"), ""); // no stem
  assert.equal(extensionOf("take."), "");
  assert.equal(extensionOf(""), "");
});

test("supported extensions are matched case-insensitively", () => {
  for (const name of ["a.wav", "a.WAV", "a.Wav", "a.mp3", "a.MP3"]) {
    assert.equal(hasSupportedExtension(name), true, name);
  }
});

test("unsupported extensions are rejected", () => {
  for (const name of [
    "a.m4a",
    "a.exe",
    "a.flac",
    "a.ogg",
    "song.mp3.exe",
    "recording",
    ".mp3",
  ]) {
    assert.equal(hasSupportedExtension(name), false, name);
  }
});

test("the supported list can come from the server", () => {
  assert.equal(hasSupportedExtension("a.flac", [".flac"]), true);
  assert.equal(hasSupportedExtension("a.wav", [".flac"]), false);
});

// --- File validation -------------------------------------------------------

test("a supported file within the size limit passes", () => {
  const result = validateFileForUpload(
    { name: "take.wav", size: 1000 },
    { maxBytes: 5000 },
  );
  assert.deepEqual(result, { ok: true });
});

test("a file exactly at the limit passes", () => {
  const result = validateFileForUpload(
    { name: "take.wav", size: 5000 },
    { maxBytes: 5000 },
  );
  assert.equal(result.ok, true);
});

test("a file one byte over the limit is rejected", () => {
  const result = validateFileForUpload(
    { name: "take.wav", size: 5001 },
    { maxBytes: 5000 },
  );
  assert.equal(result.ok, false);
  assert.equal(result.reason, "FILE_TOO_LARGE");
});

test("an unsupported extension is rejected before size is considered", () => {
  const result = validateFileForUpload(
    { name: "take.exe", size: 999_999_999 },
    { maxBytes: 5000 },
  );
  assert.equal(result.reason, "UNSUPPORTED_FORMAT");
});

test("size is not checked when the server's limit is unknown", () => {
  const result = validateFileForUpload({ name: "take.wav", size: 10 ** 12 });
  assert.equal(result.ok, true, "the server stays authoritative on size");
});

test("accept attribute lists extensions and their MIME types", () => {
  const accept = acceptAttribute([".mp3", ".wav"]);
  assert.ok(accept.includes(".mp3"));
  assert.ok(accept.includes("audio/mpeg"));
  assert.ok(accept.includes("audio/wav"));
});

// --- Error mapping ---------------------------------------------------------

test("every backend upload code maps to a friendly message", () => {
  const codes = [
    "INVALID_FILENAME",
    "UNSUPPORTED_FORMAT",
    "FORMAT_MISMATCH",
    "FILE_TOO_LARGE",
    "AUDIO_TOO_SHORT",
    "AUDIO_TOO_LONG",
    "CORRUPTED_AUDIO",
    "VALIDATION_ERROR",
    "RATE_LIMITED",
    "INTERNAL_ERROR",
    "NETWORK_ERROR",
  ];
  for (const code of codes) {
    const message = messageForErrorCode(code);
    assert.ok(message.length > 0, code);
    assert.ok(!message.includes(code), `${code} leaked its code into the copy`);
  }
});

test("mapped copy is used in preference to the server's wording", () => {
  assert.equal(
    messageForErrorCode("UNSUPPORTED_FORMAT", "Only WAV, MP3 files are supported."),
    UPLOAD_ERROR_MESSAGES.UNSUPPORTED_FORMAT,
  );
});

test("an unknown code falls back to the server message when presentable", () => {
  assert.equal(
    messageForErrorCode("SOME_NEW_CODE", "That recording is unusual."),
    "That recording is unusual.",
  );
});

test("an unknown code with no message falls back to generic copy", () => {
  const message = messageForErrorCode("SOME_NEW_CODE");
  assert.ok(message.length > 0);
  assert.ok(!message.includes("SOME_NEW_CODE"));
});

test("internal-looking server text is never displayed", () => {
  for (const leaky of [
    "Traceback (most recent call last):",
    "MutagenError: bad header",
    "failed reading /var/lib/vocallens/x.wav",
    "open /tmp/vocallens-upload-abc failed",
    "<html><body>502 Bad Gateway",
    "",
    "   ",
  ]) {
    assert.equal(isPresentableMessage(leaky), false, leaky);
    const shown = messageForErrorCode("UNRECOGNISED", leaky);
    assert.notEqual(shown, leaky);
  }
});

test("ordinary server prose is allowed through", () => {
  assert.equal(
    isPresentableMessage("This file is named '.mp3' but contains WAV audio."),
    true,
  );
});

// --- Formatting ------------------------------------------------------------

test("byte sizes are human readable", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1024), "1.0 KB");
  assert.equal(formatBytes(1024 * 1024), "1.0 MB");
  assert.equal(formatBytes(52_428_800), "50.0 MB");
  assert.equal(formatBytes(-1), "—");
  assert.equal(formatBytes(Number.NaN), "—");
});

test("durations read naturally either side of a minute", () => {
  assert.equal(formatDuration(1.5), "1.5s");
  assert.equal(formatDuration(59.4), "59.4s");
  assert.equal(formatDuration(60), "1:00");
  assert.equal(formatDuration(95), "1:35");
  assert.equal(formatDuration(300), "5:00");
  assert.equal(formatDuration(-2), "—");
});

test("duration rounding never produces a sixty-second minute", () => {
  assert.equal(formatDuration(119.7), "2:00");
});

test("sample rates are shown in kHz", () => {
  assert.equal(formatSampleRate(8000), "8 kHz");
  assert.equal(formatSampleRate(44100), "44.1 kHz");
  assert.equal(formatSampleRate(0), "—");
});

test("channel counts are named", () => {
  assert.equal(formatChannels(1), "Mono");
  assert.equal(formatChannels(2), "Stereo");
  assert.equal(formatChannels(6), "6 channels");
});

test("duration limits are phrased for a sentence", () => {
  assert.equal(formatDurationLimit(300), "5 minutes");
  assert.equal(formatDurationLimit(60), "1 minute");
  assert.equal(formatDurationLimit(90), "90 seconds");
});

test("being rate-limited reads as a pause, not as a rejected recording", () => {
  const message = messageForErrorCode("RATE_LIMITED");

  assert.match(message, /wait a moment/i);
  assert.match(message, /nothing you've already recorded is affected/i);
  // It is our limit, so it must not blame the file, the audio or the person.
  for (const wrong of ["invalid", "unsupported", "corrupt", "too large", "rejected"]) {
    assert.ok(
      !message.toLowerCase().includes(wrong),
      `"${wrong}" blames the recording for a limit we imposed`,
    );
  }
});

test("the rate-limit message never exposes the limit or the window", () => {
  const message = messageForErrorCode("RATE_LIMITED");

  for (const leak of ["per hour", "per minute", "quota", "3600", "requests per"]) {
    assert.ok(!message.toLowerCase().includes(leak), `"${leak}" describes the defence`);
  }
});
