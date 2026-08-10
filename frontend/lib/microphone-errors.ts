/**
 * Microphone failures, in words a person can act on.
 *
 * Same rule as the upload and analysis error maps: components branch on a
 * code, never on a message, and the message never carries anything the browser
 * or the platform said. `DOMException` names ("NotAllowedError") and vendor
 * strings are diagnostics, not copy.
 */

export type MicrophoneErrorCode =
  | "MIC_PERMISSION_DENIED"
  | "MIC_NOT_FOUND"
  | "MIC_IN_USE"
  | "MIC_UNSUPPORTED"
  | "MIC_INSECURE_CONTEXT"
  | "MIC_FAILED";

export const MICROPHONE_ERROR_MESSAGES: Record<MicrophoneErrorCode, string> = {
  MIC_PERMISSION_DENIED:
    "Microphone access was blocked. Allow it in your browser's site settings, then try again.",
  MIC_NOT_FOUND: "No microphone was found. Connect one and try again.",
  MIC_IN_USE:
    "Your microphone is busy in another app or tab. Close it and try again.",
  MIC_UNSUPPORTED:
    "This browser cannot record audio. Try a recent version of Chrome, Edge, Firefox or Safari.",
  MIC_INSECURE_CONTEXT:
    "Recording needs a secure connection. Open this page over HTTPS and try again.",
  MIC_FAILED: "Recording could not start. Try again.",
};

export function messageForMicrophoneError(code: string): string {
  if (code in MICROPHONE_ERROR_MESSAGES) {
    return MICROPHONE_ERROR_MESSAGES[code as MicrophoneErrorCode];
  }
  return MICROPHONE_ERROR_MESSAGES.MIC_FAILED;
}

/**
 * Map whatever `getUserMedia` threw onto one of our codes.
 *
 * The thrown value is inspected here and discarded here; nothing about it
 * reaches the UI.
 */
export function microphoneErrorCode(cause: unknown): MicrophoneErrorCode {
  const name =
    typeof cause === "object" && cause !== null && "name" in cause
      ? String((cause as { name: unknown }).name)
      : "";

  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "MIC_PERMISSION_DENIED";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "MIC_NOT_FOUND";
    case "NotReadableError":
    case "TrackStartError":
      return "MIC_IN_USE";
    case "SecurityError":
      return "MIC_INSECURE_CONTEXT";
    case "OverconstrainedError":
    case "NotSupportedError":
      return "MIC_UNSUPPORTED";
    default:
      return "MIC_FAILED";
  }
}
