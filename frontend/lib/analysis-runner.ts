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

import type { AnalysisResponse, AnalysisStatus } from "../types/api";

/** Statuses that mean the analysis is still being worked on. */
const ACTIVE_STATUSES: readonly AnalysisStatus[] = [
  "pending",
  "transcribing",
  "analyzing",
];

export function isTerminalStatus(status: AnalysisStatus): boolean {
  return status === "completed" || status === "failed";
}

export function isActiveStatus(status: AnalysisStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}

/** What the user is told while each stage runs. */
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

export type AnalysisRunState =
  | { status: "idle" }
  /** The `POST` is in flight; no analysis record is known yet. */
  | { status: "starting" }
  /** Queued, transcribing or analyzing. */
  | { status: "running"; analysis: AnalysisResponse }
  | { status: "completed"; analysis: AnalysisResponse }
  /** The analysis itself failed. The request that reported it did not. */
  | { status: "failed"; analysis: AnalysisResponse; message: string }
  /** The request failed — network, 404, an unreadable response. */
  | { status: "error"; message: string };

export interface AnalysisApi {
  start(recordingId: string, signal?: AbortSignal): Promise<AnalysisResponse>;
  get(recordingId: string, signal?: AbortSignal): Promise<AnalysisResponse>;
}

export interface RunnerOptions {
  recordingId: string;
  api: AnalysisApi;
  onState: (state: AnalysisRunState) => void;
  /** Maps an error to presentable copy. Components never see a raw error. */
  describeError: (error: unknown) => string;
  /** Injectable so tests can drive time instead of waiting for it. */
  setTimer?: (callback: () => void, ms: number) => number;
  clearTimer?: (handle: number) => void;
}

export interface AnalysisRunner {
  /** Begin an analysis. A no-op while one is already in flight. */
  start: () => void;
  /** Stop polling and abort any in-flight request. Safe to call repeatedly. */
  stop: () => void;
  getState: () => AnalysisRunState;
}

export function createAnalysisRunner(options: RunnerOptions): AnalysisRunner {
  const {
    recordingId,
    api,
    onState,
    describeError,
    setTimer = (callback, ms) =>
      setTimeout(callback, ms) as unknown as number,
    clearTimer = (handle) => clearTimeout(handle),
  } = options;

  let state: AnalysisRunState = { status: "idle" };
  let stopped = false;
  let timer: number | null = null;
  let controller: AbortController | null = null;
  let attempt = 0;

  function publish(next: AnalysisRunState): void {
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

  function settle(analysis: AnalysisResponse): void {
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

    getState(): AnalysisRunState {
      return state;
    },
  };
}
