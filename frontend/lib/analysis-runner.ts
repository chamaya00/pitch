/**
 * The analysis lifecycle: start it, watch it, stop cleanly.
 *
 * Deliberately plain TypeScript rather than a hook. Polling has the properties
 * that are easy to get wrong and hard to test through React — no overlapping
 * requests, no loop left running after unmount, no second job started by an
 * impatient click — so the logic lives here where a test can drive it directly
 * with an injected clock. `use-analysis.ts` is a thin wrapper over it.
 *
 * There is no progress percentage anywhere in this file. The server reports a
 * *stage*, not a fraction, and inventing one would be a lie told with a
 * progress bar.
 */

import type {
  AnalysisResponse,
  AnalysisStatus,
  AudioAnalysisResponse,
  AudioAnalysisStatus,
} from "../types/api";

/**
 * Anything this runner can drive.
 *
 * Both backend pipelines — speech and audio — report the same lifecycle in the
 * same shape, so they get the same polling engine rather than two copies of the
 * parts that are easy to get wrong. What differs between them is the *content*
 * of a finished record, and that is the type parameter.
 */
export interface Pollable {
  status: string;
  error_code: string | null;
}

/**
 * Statuses that mean work is still going on, across all three things this
 * runner drives: the speech analysis, the audio analysis, and audio feedback.
 *
 * One list rather than one per pipeline. A status missing from it stops the
 * polling loop silently — the caller sees "running" forever and no request is
 * ever made again — so the failure mode of splitting this is worse than the
 * cost of a shared list.
 */
const ACTIVE_STATUSES: readonly string[] = [
  "pending",
  "transcribing",
  "analyzing",
  "generating",
];

export function isTerminalStatus(status: string): boolean {
  return status === "completed" || status === "failed";
}

export function isActiveStatus(status: string): boolean {
  return ACTIVE_STATUSES.includes(status);
}

/** What the user is told while each stage of the *speech* analysis runs. */
export const STAGE_MESSAGES: Readonly<Record<AnalysisStatus, string>> = {
  pending: "Preparing your recording…",
  transcribing: "Transcribing your speech…",
  analyzing: "Generating your feedback…",
  completed: "Analysis complete.",
  failed: "Analysis failed.",
};

export function stageMessage(status: AnalysisStatus): string {
  return STAGE_MESSAGES[status];
}

/**
 * The *audio* analysis stages. Shorter, because there is no provider to wait
 * on: the server decodes the file and measures it, and that is the whole job.
 */
export const AUDIO_STAGE_MESSAGES: Readonly<Record<AudioAnalysisStatus, string>> = {
  pending: "Preparing your recording…",
  analyzing: "Measuring pitch and loudness…",
  completed: "Measurement complete.",
  failed: "Measurement failed.",
};

export function audioStageMessage(status: AudioAnalysisStatus): string {
  return AUDIO_STAGE_MESSAGES[status];
}

/**
 * Delay before the next poll, in milliseconds.
 *
 * Starts responsive and eases off, so a short analysis feels immediate and a
 * long one does not hammer the API. Attempt 0 is the first poll after the
 * `POST` returned.
 */
export const FIRST_POLL_DELAY_MS = 600;
export const MAX_POLL_DELAY_MS = 5000;

export function pollDelay(attempt: number): number {
  const delay = FIRST_POLL_DELAY_MS * 1.6 ** Math.max(attempt, 0);
  return Math.round(Math.min(delay, MAX_POLL_DELAY_MS));
}

export type RunState<T extends Pollable> =
  | { status: "idle" }
  /** The `POST` is in flight; no analysis record is known yet. */
  | { status: "starting" }
  /** Queued or being worked on. */
  | { status: "running"; analysis: T }
  | { status: "completed"; analysis: T }
  /** The analysis itself failed. The request that reported it did not. */
  | { status: "failed"; analysis: T; message: string }
  /** The request failed — network, 404, an unreadable response. */
  | { status: "error"; message: string };

export interface RunnerApi<T extends Pollable> {
  start(recordingId: string, signal?: AbortSignal): Promise<T>;
  get(recordingId: string, signal?: AbortSignal): Promise<T>;
}

/** The speech pipeline's specialisations, named as they always were. */
export type AnalysisRunState = RunState<AnalysisResponse>;
export type AnalysisApi = RunnerApi<AnalysisResponse>;
/** And the audio pipeline's. */
export type AudioRunState = RunState<AudioAnalysisResponse>;
export type AudioAnalysisApi = RunnerApi<AudioAnalysisResponse>;

export interface RunnerOptions<T extends Pollable> {
  recordingId: string;
  api: RunnerApi<T>;
  onState: (state: RunState<T>) => void;
  /** Maps an error to presentable copy. Components never see a raw error. */
  describeError: (error: unknown) => string;
  /** Injectable so tests can drive time instead of waiting for it. */
  setTimer?: (callback: () => void, ms: number) => number;
  clearTimer?: (handle: number) => void;
}

export interface Runner<T extends Pollable> {
  /** Begin an analysis. A no-op while one is already in flight. */
  start: () => void;
  /** Stop polling and abort any in-flight request. Safe to call repeatedly. */
  stop: () => void;
  getState: () => RunState<T>;
}

export type AnalysisRunner = Runner<AnalysisResponse>;
export type AudioAnalysisRunner = Runner<AudioAnalysisResponse>;

export function createAnalysisRunner<T extends Pollable>(
  options: RunnerOptions<T>,
): Runner<T> {
  const {
    recordingId,
    api,
    onState,
    describeError,
    setTimer = (callback, ms) =>
      setTimeout(callback, ms) as unknown as number,
    clearTimer = (handle) => clearTimeout(handle),
  } = options;

  let state: RunState<T> = { status: "idle" };
  let stopped = false;
  let timer: number | null = null;
  let controller: AbortController | null = null;
  let attempt = 0;

  function publish(next: RunState<T>): void {
    // After `stop()` nothing may reach the caller — that is what keeps a
    // React wrapper from setting state on an unmounted component.
    if (stopped) return;
    state = next;
    onState(next);
  }

  function clearTimerIfSet(): void {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function settle(analysis: T): void {
    if (analysis.status === "completed") {
      publish({ status: "completed", analysis });
      return;
    }
    if (analysis.status === "failed") {
      publish({
        status: "failed",
        analysis,
        message: describeError(analysis.error_code),
      });
      return;
    }
    publish({ status: "running", analysis });
  }

  function scheduleNextPoll(): void {
    if (stopped) return;
    const delay = pollDelay(attempt);
    attempt += 1;
    timer = setTimer(() => {
      timer = null;
      void poll();
    }, delay);
  }

  async function poll(): Promise<void> {
    if (stopped) return;

    controller = new AbortController();
    try {
      const analysis = await api.get(recordingId, controller.signal);
      if (stopped) return;

      settle(analysis);
      // Only schedule the next request once this one has resolved, so two
      // polls can never be in flight at the same time.
      if (isActiveStatus(analysis.status)) scheduleNextPoll();
    } catch (error) {
      if (stopped) return;
      publish({ status: "error", message: describeError(error) });
    } finally {
      controller = null;
    }
  }

  async function begin(): Promise<void> {
    controller = new AbortController();
    try {
      const analysis = await api.start(recordingId, controller.signal);
      if (stopped) return;

      settle(analysis);
      if (isActiveStatus(analysis.status)) scheduleNextPoll();
    } catch (error) {
      if (stopped) return;
      publish({ status: "error", message: describeError(error) });
    } finally {
      controller = null;
    }
  }

  return {
    start(): void {
      if (stopped) return;
      // A second click, or a re-render that calls through, must not start a
      // second analysis. The server is idempotent too, but a request nobody
      // needed is still a request nobody needed.
      if (state.status === "starting" || state.status === "running") return;

      attempt = 0;
      clearTimerIfSet();
      publish({ status: "starting" });
      void begin();
    },

    stop(): void {
      stopped = true;
      clearTimerIfSet();
      controller?.abort();
      controller = null;
    },

    getState(): RunState<T> {
      return state;
    },
  };
}
