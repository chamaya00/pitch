/**
 * Backend error codes translated into language a singer understands.
 *
 * Centralised on purpose: the codes are a contract, and the wording is a
 * product decision. Components render whatever this returns and never inspect
 * a code themselves, so changing a message means editing one file.
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

/** Codes the upload flow can produce. Mirrors the backend's vocabulary. */
export const UPLOAD_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  INVALID_FILENAME: "Please choose a file with a valid name.",
  UNSUPPORTED_FORMAT: "Only WAV and MP3 files are supported.",
  FORMAT_MISMATCH:
    "The file extension doesn't match its actual audio format.",
  FILE_TOO_LARGE: "This file is larger than the allowed upload size.",
  AUDIO_TOO_SHORT: "This recording is too short to analyse.",
  AUDIO_TOO_LONG: "This recording is longer than the allowed duration.",
  CORRUPTED_AUDIO:
    "We couldn't read this audio file. Please try another recording.",
  VALIDATION_ERROR: "Please choose an audio file.",
  RECORDING_NOT_FOUND: "That recording could no longer be found.",
  // Our own limit, not a provider's, and not a fault of the recording. Phrased
  // so nobody reads it as "your file was rejected": the upload did not happen,
  // and everything already stored is untouched.
  RATE_LIMITED:
    "You've made a lot of requests in a short time. Please wait a moment and try again — nothing you've already recorded is affected.",
  INTERNAL_ERROR: "Something went wrong on our side. Please try again.",
  NETWORK_ERROR:
    "We couldn't reach VocalLens. Check your connection and try again.",
};

const FALLBACK_MESSAGE =
  "We couldn't upload that file. Please try again.";

/**
 * Resolve the message to display for a failed upload.
 *
 * Prefers our own wording so the copy stays consistent and no raw server or
 * exception text can reach the screen. An unrecognised code falls back to a
 * generic sentence rather than showing the code itself.
 */
export function messageForErrorCode(
  code: string | undefined,
  serverMessage?: string,
): string {
  if (code && code in UPLOAD_ERROR_MESSAGES) {
    return UPLOAD_ERROR_MESSAGES[code];
  }
  if (serverMessage && isPresentableMessage(serverMessage)) {
    return serverMessage;
  }
  return FALLBACK_MESSAGE;
}

/**
 * Guard against showing anything that reads like an internal detail.
 *
 * The backend is careful not to leak paths or exception text, but this is the
 * last stop before a string reaches a user, so it is checked here too.
 */
export function isPresentableMessage(message: string): boolean {
  const trimmed = message.trim();
  if (trimmed.length === 0 || trimmed.length > 300) return false;

  const suspicious = [
    "Traceback",
    "/usr/",
    "/var/",
    "/tmp/",
    "\\Users\\",
    "at Object.",
    "<html",
  ];
  if (suspicious.some((needle) => trimmed.includes(needle))) return false;

  // Any exception class name — `MutagenError`, `StorageWriteError`,
  // `ValueError: ...` — is internal detail, whatever punctuation follows it.
  return !/\b[A-Za-z_]\w*(Error|Exception)\b/.test(trimmed);
}
