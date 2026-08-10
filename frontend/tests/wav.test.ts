/**
 * WAV encoder tests.
 *
 * The header is 44 bytes of arithmetic that no one reads twice, and a wrong
 * byte rate or a wrong data length produces a file that plays back at the
 * wrong speed or is rejected by the server — both a long way from where the
 * mistake was made. So the bytes are asserted directly.
 *
 * The round-trip test matters most: the samples that come back out of the
 * encoded file are the samples the analysis was taken from.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  concatFloat32,
  durationOf,
  encodeWav,
  recordingFilename,
} from "../lib/wav.ts";

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(offset, offset + length));
}

function view(bytes: Uint8Array): DataView {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

test("the header describes 16-bit mono PCM at the given rate", () => {
  const samples = new Float32Array(100);
  const bytes = encodeWav(samples, 48000);
  const data = view(bytes);

  assert.equal(ascii(bytes, 0, 4), "RIFF");
  assert.equal(ascii(bytes, 8, 4), "WAVE");
  assert.equal(ascii(bytes, 12, 4), "fmt ");
  assert.equal(ascii(bytes, 36, 4), "data");

  assert.equal(data.getUint32(16, true), 16, "fmt chunk length");
  assert.equal(data.getUint16(20, true), 1, "PCM format tag");
  assert.equal(data.getUint16(22, true), 1, "channels");
  assert.equal(data.getUint32(24, true), 48000, "sample rate");
  assert.equal(data.getUint32(28, true), 48000 * 2, "byte rate");
  assert.equal(data.getUint16(32, true), 2, "block align");
  assert.equal(data.getUint16(34, true), 16, "bits per sample");
});

test("the declared sizes match the file that was produced", () => {
  const samples = new Float32Array(1234);
  const bytes = encodeWav(samples, 44100);
  const data = view(bytes);

  assert.equal(bytes.length, 44 + 1234 * 2);
  assert.equal(data.getUint32(4, true), bytes.length - 8, "RIFF size");
  assert.equal(data.getUint32(40, true), 1234 * 2, "data size");
});

test("samples survive the round trip", () => {
  const source = new Float32Array([0, 0.5, -0.5, 0.25, -0.25]);
  const bytes = encodeWav(source, 16000);
  const data = view(bytes);

  for (let i = 0; i < source.length; i++) {
    const decoded = data.getInt16(44 + i * 2, true) / 32767;
    assert.ok(
      Math.abs(decoded - source[i]) < 1e-4,
      `sample ${i}: wrote ${source[i]}, read back ${decoded}`,
    );
  }
});

test("peaks are clamped rather than rescaling the recording", () => {
  const bytes = encodeWav(new Float32Array([2, -2, 1, -1]), 16000);
  const data = view(bytes);
  assert.equal(data.getInt16(44, true), 32767);
  assert.equal(data.getInt16(46, true), -32768);
  assert.equal(data.getInt16(48, true), 32767);
  assert.equal(data.getInt16(50, true), -32767);
});

test("a non-finite sample becomes silence, not a wrapped value", () => {
  const bytes = encodeWav(
    new Float32Array([Number.NaN, Number.POSITIVE_INFINITY]),
    16000,
  );
  const data = view(bytes);
  assert.equal(data.getInt16(44, true), 0);
  assert.equal(data.getInt16(46, true), 0);
});

test("an empty recording still produces a valid, empty file", () => {
  const bytes = encodeWav(new Float32Array(0), 48000);
  assert.equal(bytes.length, 44);
  assert.equal(view(bytes).getUint32(40, true), 0);
});

test("an impossible sample rate is refused rather than written", () => {
  for (const rate of [0, -48000, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.throws(() => encodeWav(new Float32Array(4), rate), RangeError);
  }
});

test("captured blocks concatenate in order", () => {
  const merged = concatFloat32([
    new Float32Array([1, 2]),
    new Float32Array([]),
    new Float32Array([3, 4, 5]),
  ]);
  assert.deepEqual(Array.from(merged), [1, 2, 3, 4, 5]);
  assert.equal(concatFloat32([]).length, 0);
});

test("duration is frames over rate, and null when the rate is unusable", () => {
  assert.equal(durationOf(48000, 48000), 1);
  assert.equal(durationOf(24000, 48000), 0.5);
  assert.equal(durationOf(0, 48000), 0);
  assert.equal(durationOf(48000, 0), null);
  assert.equal(durationOf(48000, Number.NaN), null);
});

test("the generated filename is a .wav the server will accept", () => {
  const name = recordingFilename(new Date(2026, 0, 2, 3, 4, 5));
  assert.equal(name, "vocallens-recording-20260102-030405.wav");
  assert.ok(name.endsWith(".wav"));
});
