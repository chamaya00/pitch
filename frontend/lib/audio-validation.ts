/**
 * Client-side pre-checks for a chosen file.
 *
 * These exist purely for speed of feedback: catching a `.pdf` or a 400 MB file
 * before spending a long upload on a rejection the server would issue anyway.
 * **The backend is authoritative.** Nothing here decodes audio, reads headers
 * or inspects content — a file that passes these checks may still be rejected,
 * and that is expected, not a bug.
 *
 * Pure and dependency-free so it can be tested with `node --test`.
 */

export const DEFAULT_SUPPORTED_EXTENSIONS = [".mp3", ".wav"] as const;

export type ClientRejectionReason = "UNSUPPORTED_FORMAT" | "FILE_TOO_LARGE";

export interface ClientValidationResult {
  ok: boolean;
  reason?: ClientRejectionReason;
}

/** Lower-cased extension including the dot, or `""` when there is none. */
export function extensionOf(filename: string): string {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot <= 0 || lastDot === filename.length - 1) return "";
  return filename.slice(lastDot).toLowerCase();
}

/** Whether the name ends in an accepted extension. Case-insensitive. */
export function hasSupportedExtension(
  filename: string,
  supported: readonly string[] = DEFAULT_SUPPORTED_EXTENSIONS,
): boolean {
  const extension = extensionOf(filename);
  return extension !== "" && supported.includes(extension);
}

/**
 * Check a file before uploading it.
 *
 * `maxBytes` is omitted when the server's limits could not be fetched; the size
 * check is then skipped rather than guessed at, leaving the server to decide.
 */
export function validateFileForUpload(
  file: { name: string; size: number },
  options: {
    supportedExtensions?: readonly string[];
    maxBytes?: number;
  } = {},
): ClientValidationResult {
  const supported = options.supportedExtensions ?? DEFAULT_SUPPORTED_EXTENSIONS;

  if (!hasSupportedExtension(file.name, supported)) {
    return { ok: false, reason: "UNSUPPORTED_FORMAT" };
  }
  if (options.maxBytes !== undefined && file.size > options.maxBytes) {
    return { ok: false, reason: "FILE_TOO_LARGE" };
  }
  return { ok: true };
}

/** `accept` attribute value for the file input, from the server's list. */
export function acceptAttribute(
  supported: readonly string[] = DEFAULT_SUPPORTED_EXTENSIONS,
): string {
  const mimeTypes: Record<string, string> = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
  };
  const types = supported
    .map((extension) => mimeTypes[extension])
    .filter((value): value is string => Boolean(value));
  return [...supported, ...types].join(",");
}
