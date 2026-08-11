import { API_V1 } from "@/lib/config";
import { captureOwnerToken, ownerHeaders } from "@/lib/owner";
import type {
  AnalysisResponse,
  ApiErrorBody,
  AudioAnalysisResponse,
  AudioFeedbackState,
  HealthResponse,
  NoteBreakdown,
  PitchTimeline,
  PublicConfig,
  Recording,
  RecordingComparison,
  RecordingHistory,
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
      // The owner header goes on every request. A caller that passes its own
      // wins, which is what lets a test drive two identities against one app.
      headers: {
        Accept: "application/json",
        ...ownerHeaders(),
        ...init?.headers,
      },
    });
  } catch (cause) {
    // An aborted request is a caller's own decision — a poller cleaning up on
    // unmount, say — not a failure to report to the user.
    if (init?.signal?.aborted || (cause as Error | null)?.name === "AbortError") {
      throw new ApiError("The request was cancelled.", 0, "REQUEST_ABORTED");
    }
    throw new ApiError(
      "Could not reach the VocalLens API.",
      0,
      "NETWORK_ERROR",
    );
  }

  // Before anything else, including the error path: a first request that
  // fails still minted an identity, and dropping it would mint another.
  captureOwnerToken(response.headers);

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

/**
 * Ask the server to analyse a stored recording.
 *
 * Returns as soon as the analysis record exists — transcription runs in the
 * background — so the response is normally `pending`. Repeating the call is
 * safe: the server returns an analysis that is already running or finished
 * rather than starting a second one.
 */
export function startAnalysis(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return apiFetch<AnalysisResponse>(`/recordings/${recordingId}/analysis`, {
    method: "POST",
    signal,
  });
}

/**
 * Read a recording's analysis: its progress while it runs, its results after.
 *
 * A *failed* analysis resolves normally with `status: "failed"` — the request
 * succeeded, the analysis did not. Only a genuine request failure rejects.
 */
export function getAnalysis(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return apiFetch<AnalysisResponse>(`/recordings/${recordingId}/analysis`, {
    signal,
    cache: "no-store",
  });
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
    for (const [name, value] of Object.entries(ownerHeaders())) {
      request.setRequestHeader(name, value);
    }
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
      // `XMLHttpRequest` has no `Headers`; adapt it to the same shape so the
      // capture logic has one implementation rather than two.
      captureOwnerToken({
        get: (name: string) => request.getResponseHeader(name),
      });
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

/* --- Deterministic audio analysis ------------------------------------------ */

/**
 * Start measuring a recording's audio, or return the measurement that exists.
 *
 * Separate from `startAnalysis`, which transcribes. Both are safe to repeat:
 * the server returns an analysis already queued, running or finished rather
 * than starting a second one.
 */
export function startAudioAnalysis(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AudioAnalysisResponse> {
  return apiFetch<AudioAnalysisResponse>(`/recordings/${recordingId}/audio-analysis`, {
    method: "POST",
    signal,
  });
}

export function getAudioAnalysis(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AudioAnalysisResponse> {
  return apiFetch<AudioAnalysisResponse>(`/recordings/${recordingId}/audio-analysis`, {
    signal,
    cache: "no-store",
  });
}

/**
 * The pitch timeline, decimated server-side to `maxPoints`.
 *
 * A graph a few hundred pixels wide cannot show thirteen thousand points, and
 * asking for them all would cost a megabyte to draw the same line.
 */
export function getPitchTimeline(
  recordingId: string,
  maxPoints: number,
  signal?: AbortSignal,
): Promise<PitchTimeline> {
  return apiFetch<PitchTimeline>(
    `/recordings/${recordingId}/audio-analysis/pitch?max_points=${maxPoints}`,
    { signal, cache: "no-store" },
  );
}

/**
 * The note breakdown: where the pitched time went, aggregated by note.
 *
 * Aggregated server-side on purpose. The timeline behind it runs to thousands
 * of points, and downloading them to build a table of a few rows would be a
 * megabyte spent on arithmetic the server already did.
 */
export function getNoteBreakdown(
  recordingId: string,
  signal?: AbortSignal,
): Promise<NoteBreakdown> {
  return apiFetch<NoteBreakdown>(`/recordings/${recordingId}/audio-analysis/notes`, {
    signal,
    cache: "no-store",
  });
}

/* --- Audio feedback -------------------------------------------------------- */

/**
 * Ask for an interpretation of a recording's measured audio.
 *
 * Safe to repeat: feedback already written, or already being written, comes
 * back as-is rather than starting a second provider call.
 */
export function startAudioFeedback(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AudioFeedbackState> {
  return apiFetch<AudioFeedbackState>(
    `/recordings/${recordingId}/audio-analysis/feedback`,
    { method: "POST", signal },
  );
}

export function getAudioFeedback(
  recordingId: string,
  signal?: AbortSignal,
): Promise<AudioFeedbackState> {
  return apiFetch<AudioFeedbackState>(
    `/recordings/${recordingId}/audio-analysis/feedback`,
    { signal, cache: "no-store" },
  );
}

/* --- Recording history ------------------------------------------------------ */

/**
 * The caller's own recordings, newest first.
 *
 * Whose recordings those are is decided entirely on the server, from the owner
 * header this client attaches. There is no parameter that could name a
 * different owner, which is the point: the browser is not trusted to filter
 * anything.
 */
export function getRecordingHistory(
  limit?: number,
  signal?: AbortSignal,
): Promise<RecordingHistory> {
  const query = limit === undefined ? "" : `?limit=${limit}`;
  return apiFetch<RecordingHistory>(`/recordings${query}`, {
    signal,
    cache: "no-store",
  });
}

/**
 * Compare two of the caller's own recordings.
 *
 * Ownership is decided on the server from the owner header; a recording
 * belonging to somebody else comes back as `not_found`, identically to an id
 * that was never real. A refusal is a successful response with
 * `comparable: false`, so only a genuine request failure rejects.
 */
export function compareRecordings(
  leftId: string,
  rightId: string,
  signal?: AbortSignal,
): Promise<RecordingComparison> {
  const query = new URLSearchParams({ left_id: leftId, right_id: rightId });
  return apiFetch<RecordingComparison>(`/recordings/compare?${query}`, {
    signal,
    cache: "no-store",
  });
}
