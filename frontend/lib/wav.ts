/**
 * Writing captured microphone audio as a WAV file.
 *
 * Why this exists at all: the backend accepts WAV and MP3 only, and validates
 * by content rather than by filename. `MediaRecorder` produces WebM/Opus (or
 * MP4/AAC on Safari) — neither is accepted, and neither can be transcoded in
 * the browser without pulling in an encoder. So the recorder never uses
 * `MediaRecorder`. It taps the same audio graph the pitch detector already
 * runs on, keeps the raw PCM, and writes a canonical 16-bit mono WAV here.
 *
 * That choice keeps one format end to end: what you hear on playback is the
 * same bytes the backend analyses, and no server change was needed to accept
 * a recording made in the browser.
 *
 * 16-bit signed little-endian PCM, one channel, no extension chunks — the
 * least surprising thing any decoder can be handed.
 *
 * Pure: `Float32Array` in, `Uint8Array` out. No browser APIs, so the header
 * arithmetic is testable under `node --test`.
 */

const BITS_PER_SAMPLE = 16;
const CHANNELS = 1;
const HEADER_BYTES = 44;
const PCM_FORMAT_TAG = 1;

/** Int16 range is asymmetric; clamping to it avoids wrapping a loud peak. */
const INT16_MAX = 32767;
const INT16_MIN = -32768;

export const WAV_MIME_TYPE = "audio/wav";

/**
 * Join captured blocks into one buffer.
 *
 * The capture loop keeps a list of small blocks rather than growing one array,
 * because reallocating a multi-megabyte buffer on the audio path is how a live
 * display starts stuttering.
 */
export function concatFloat32(blocks: readonly Float32Array[]): Float32Array {
  let total = 0;
  for (const block of blocks) total += block.length;
  const merged = new Float32Array(total);
  let offset = 0;
  for (const block of blocks) {
    merged.set(block, offset);
    offset += block.length;
  }
  return merged;
}

/**
 * Encode mono float samples in [-1, 1] as a complete WAV file.
 *
 * Samples outside the range are clamped, not scaled: rescaling the whole
 * recording because one frame clipped would quietly change every measurement
 * taken from it.
 */
export function encodeWav(
  samples: Float32Array,
  sampleRate: number,
): Uint8Array {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    throw new RangeError("sampleRate must be a positive finite number");
  }
  const rate = Math.round(sampleRate);
  const bytesPerSample = BITS_PER_SAMPLE / 8;
  const dataBytes = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(HEADER_BYTES + dataBytes);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true); // size of everything after this field
  writeAscii(view, 8, "WAVE");

  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk length
  view.setUint16(20, PCM_FORMAT_TAG, true);
  view.setUint16(22, CHANNELS, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * CHANNELS * bytesPerSample, true); // byte rate
  view.setUint16(32, CHANNELS * bytesPerSample, true); // block align
  view.setUint16(34, BITS_PER_SAMPLE, true);

  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);

  let offset = HEADER_BYTES;
  for (let i = 0; i < samples.length; i++) {
    const sample = samples[i];
    const scaled = Number.isFinite(sample) ? Math.round(sample * INT16_MAX) : 0;
    view.setInt16(offset, clamp(scaled), true);
    offset += bytesPerSample;
  }

  return new Uint8Array(buffer);
}

function clamp(value: number): number {
  if (value > INT16_MAX) return INT16_MAX;
  if (value < INT16_MIN) return INT16_MIN;
  return value;
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i++) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

/** Seconds of audio in a sample count. `null` when the rate is unusable. */
export function durationOf(
  sampleCount: number,
  sampleRate: number,
): number | null {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) return null;
  return sampleCount / sampleRate;
}

/** A filename for a recording made now. Display metadata only — the server
 * names the stored file itself. */
export function recordingFilename(date: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  return `vocallens-recording-${stamp}.wav`;
}
