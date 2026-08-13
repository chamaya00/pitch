/**
 * Audio-analysis error codes, in language a person can act on.
 *
 * Same discipline as `analysis-errors.ts`: components branch on nothing, they
 * render whatever this returns, and the server's own wording is never used —
 * only codes we recognise map to copy we wrote.
 *
 * `INSUFFICIENT_PITCH_SIGNAL` is the interesting one. It is not a bug and not
 * really a failure: a whisper, a noisy room, a spoken monotone or an
 * instrumental recording all produce it, and the honest answer is "we could not
 * measure this", not "something went wrong".
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

import { UPLOAD_ERROR_MESSAGES } from "./upload-errors.ts";

export const AUDIO_ANALYSIS_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  INSUFFICIENT_PITCH_SIGNAL:
    "Not measured — we could not detect enough reliable pitch information. This happens with a whisper, a noisy room, or speech held at a flat pitch.",
  AUDIO_UNSUPPORTED: "We couldn't read the audio in this recording.",
  AUDIO_TOO_SHORT: "This recording is too short to measure.",
  AUDIO_ANALYSIS_FAILED: "We couldn't measure this recording. Please try again.",
  AUDIO_ANALYSIS_NOT_FOUND: "This recording's audio hasn't been measured yet.",
  RECORDING_NOT_FOUND: "That recording could no longer be found.",
};

const FALLBACK_MESSAGE = "We couldn't measure this recording. Please try again.";

export function messageForAudioAnalysisError(code: string | null | undefined): string {
  if (code && code in AUDIO_ANALYSIS_ERROR_MESSAGES) {
    return AUDIO_ANALYSIS_ERROR_MESSAGES[code];
  }
  // Codes shared with the upload flow — network, validation, internal — already
  // have copy. Reuse it rather than letting two versions drift.
  if (code && code in UPLOAD_ERROR_MESSAGES) {
    return UPLOAD_ERROR_MESSAGES[code];
  }
  return FALLBACK_MESSAGE;
}

/**
 * Whether a failure means "nothing to measure here" rather than "it broke".
 *
 * The UI shows the first as an ordinary outcome and the second as an error, so
 * the distinction lives here — with the codes — rather than in a component.
 */
export function isNotMeasurable(code: string | null | undefined): boolean {
  return code === "INSUFFICIENT_PITCH_SIGNAL" || code === "AUDIO_TOO_SHORT";
}
