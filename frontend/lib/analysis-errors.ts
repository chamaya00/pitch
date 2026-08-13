/**
 * Backend analysis error codes translated into language a speaker understands.
 *
 * Same discipline as `upload-errors.ts`, and it defers to that module for every
 * code the two flows share, so there is one answer per code rather than two
 * that can drift. Components call `messageForAnalysisError` and render whatever
 * comes back — they never branch on a code themselves.
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

import { UPLOAD_ERROR_MESSAGES } from "./upload-errors.ts";

/** Codes an analysis can produce, mirroring the backend's vocabulary. */
export const ANALYSIS_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  ANALYSIS_NOT_CONFIGURED:
    "Speech analysis isn't switched on for this server yet.",
  ANALYSIS_PROVIDER_UNAVAILABLE:
    "The analysis service is temporarily unavailable. Please try again shortly.",
  ANALYSIS_PROVIDER_TIMEOUT:
    "The analysis service took too long to respond. Please try again.",
  ANALYSIS_RATE_LIMITED:
    "Too many analyses are running right now. Please try again in a moment.",
  ANALYSIS_PROVIDER_ERROR:
    "We couldn't finish analysing this recording. Please try again.",
  TRANSCRIPT_EMPTY:
    "We couldn't hear any speech in this recording. Try a clearer take.",
  ANALYSIS_NOT_FOUND: "This recording hasn't been analysed yet.",
  RECORDING_NOT_FOUND: "That recording could no longer be found.",
};

const FALLBACK_MESSAGE =
  "We couldn't analyse this recording. Please try again.";

/**
 * Resolve the message to display for a failed analysis.
 *
 * Only our own copy is ever returned. Unlike the upload flow, this one never
 * falls back to the server's wording for an unrecognised code — an analysis
 * failure originates at a third-party provider, and a message we did not write
 * is a message whose contents we cannot vouch for. The upload flow's
 * presentability guard is not enough on its own here: a string like
 * "deepgram: 401 Unauthorized" contains no path, no traceback and no exception
 * class, so it passes that check and still names the vendor.
 *
 * An unrecognised code therefore gets generic copy. That is a small loss of
 * detail and a complete removal of a leak.
 */
export function messageForAnalysisError(code: string | null | undefined): string {
  if (code && code in ANALYSIS_ERROR_MESSAGES) {
    return ANALYSIS_ERROR_MESSAGES[code];
  }

  // Codes shared with the upload flow — network, validation, internal — already
  // have copy. Reuse it rather than restating it here and letting the two drift.
  if (code && code in UPLOAD_ERROR_MESSAGES) {
    return UPLOAD_ERROR_MESSAGES[code];
  }

  return FALLBACK_MESSAGE;
}
