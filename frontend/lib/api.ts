import { API_V1 } from "@/lib/config";
import type {
  ApiErrorBody,
  HealthResponse,
  PublicConfig,
  Recording,
} from "@/types/api";

/** Error thrown for any non-2xx API response or transport failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code = "REQUEST_FAILED") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "message" in value &&
    typeof (value as ApiErrorBody).message === "string"
  );
}

/** Perform a JSON request against the versioned API and parse the response. */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_V1}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "Could not reach the VocalLens API.",
      0,
      "NETWORK_ERROR",
    );
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isApiErrorBody(body)) {
      throw new ApiError(body.message, response.status, body.error_code);
    }
    throw new ApiError(
      `Request failed with status ${response.status}.`,
      response.status,
    );
  }

  return (await response.json()) as T;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health", { signal, cache: "no-store" });
}

export function getPublicConfig(signal?: AbortSignal): Promise<PublicConfig> {
  return apiFetch<PublicConfig>("/config", { signal, cache: "no-store" });
}

export interface UploadHandle {
  /** Resolves with the stored recording, or rejects with an `ApiError`. */
  readonly result: Promise<Recording>;
  /** Abort the in-flight upload. Safe to call after completion. */
  cancel: () => void;
}

/**
 * Upload a recording, reporting real byte-level progress.
 *
 * Uses `XMLHttpRequest` rather than `fetch` for one reason: `fetch` exposes no
 * upload progress events, and showing an invented percentage would be a lie.
 * Everything else — the error envelope, the `ApiError` type — matches
 * `apiFetch`, so callers handle failures identically.
 */
export function uploadRecording(
  file: File,
  onProgress?: (fraction: number) => void,
): UploadHandle {
  const request = new XMLHttpRequest();

  const result = new Promise<Recording>((resolve, reject) => {
    const body = new FormData();
    body.append("file", file);

    request.open("POST", `${API_V1}/recordings`);
    request.setRequestHeader("Accept", "application/json");
    request.responseType = "text";

    if (onProgress) {
      request.upload.addEventListener("progress", (event) => {
        // `lengthComputable` is false for some proxies; report nothing rather
        // than guessing, and let the UI fall back to an indeterminate state.
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.min(event.loaded / event.total, 1));
        }
      });
    }

    request.addEventListener("load", () => {
      const parsed = parseJson(request.responseText);

      if (request.status === 201) {
        if (isRecording(parsed)) {
          resolve(parsed);
        } else {
          reject(
            new ApiError(
              "The server returned an unexpected response.",
              request.status,
              "UNEXPECTED_RESPONSE",
            ),
          );
        }
        return;
      }

      if (isApiErrorBody(parsed)) {
        reject(new ApiError(parsed.message, request.status, parsed.error_code));
        return;
      }
      reject(
        new ApiError(
          "The server returned an unexpected response.",
          request.status,
          "UNEXPECTED_RESPONSE",
        ),
      );
    });

    request.addEventListener("error", () => {
      reject(
        new ApiError("Could not reach the VocalLens API.", 0, "NETWORK_ERROR"),
      );
    });

    request.addEventListener("timeout", () => {
      reject(new ApiError("The upload timed out.", 0, "NETWORK_ERROR"));
    });

    request.addEventListener("abort", () => {
      reject(new ApiError("The upload was cancelled.", 0, "UPLOAD_CANCELLED"));
    });

    request.send(body);
  });

  return {
    result,
    cancel: () => request.abort(),
  };
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function isRecording(value: unknown): value is Recording {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<Recording>;
  return (
    typeof candidate.recording_id === "string" &&
    typeof candidate.original_filename === "string" &&
    typeof candidate.format === "string" &&
    typeof candidate.duration_seconds === "number" &&
    typeof candidate.sample_rate === "number" &&
    typeof candidate.channels === "number" &&
    typeof candidate.size_bytes === "number"
  );
}
