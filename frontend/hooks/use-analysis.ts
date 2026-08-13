"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAnalysis, startAnalysis } from "@/lib/api";
import { messageForAnalysisError } from "@/lib/analysis-errors";
import {
  createAnalysisRunner,
  type AnalysisRunner,
  type AnalysisRunState,
} from "@/lib/analysis-runner";

/**
 * React wrapper around the analysis runner.
 *
 * Everything interesting — polling, backoff, not starting twice — lives in
 * `lib/analysis-runner.ts`, which is testable without a renderer. This hook
 * only owns the React-shaped concerns: one runner per recording, and stopping
 * it on unmount so nothing tries to set state afterwards.
 *
 * The hook does not reset its state when `recordingId` changes; callers give
 * the panel a `key` so a new recording gets a new component instead. That
 * keeps the effect free of a synchronous `setState` and its cascading render.
 */
export interface AnalysisController {
  state: AnalysisRunState;
  /** Start the analysis. Ignored while one is already in flight. */
  start: () => void;
}

function describeError(error: unknown): string {
  if (typeof error === "string" || error === null || error === undefined) {
    // An `error_code` taken straight off a failed analysis record.
    return messageForAnalysisError(error ?? undefined);
  }
  if (error instanceof ApiError) {
    return messageForAnalysisError(error.code);
  }
  return messageForAnalysisError(undefined);
}

export function useAnalysis(recordingId: string): AnalysisController {
  const [state, setState] = useState<AnalysisRunState>({ status: "idle" });
  const runnerRef = useRef<AnalysisRunner | null>(null);

  useEffect(() => {
    const runner = createAnalysisRunner({
      recordingId,
      api: { start: startAnalysis, get: getAnalysis },
      onState: setState,
      describeError,
    });
    runnerRef.current = runner;

    return () => {
      runner.stop();
      runnerRef.current = null;
    };
  }, [recordingId]);

  const start = useCallback(() => {
    runnerRef.current?.start();
  }, []);

  return { state, start };
}
