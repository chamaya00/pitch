"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAudioFeedback, startAudioFeedback } from "@/lib/api";
import { messageForAudioAnalysisError } from "@/lib/audio-analysis-errors";
import { createAnalysisRunner, type Runner, type RunState } from "@/lib/analysis-runner";
import type { AudioFeedbackState } from "@/types/api";

/**
 * React wrapper around audio-feedback generation.
 *
 * The same polling runner the two analyses use, parameterised again. Its
 * guarantees are what matter here more than anywhere else: **no overlapping
 * requests, and no second job from an impatient click.** Every duplicate start
 * is a paid provider call.
 *
 * Nothing is requested on mount. A model is consulted when someone asks, not
 * because a page rendered — which is also why this hook exposes `start` and
 * does nothing until it is called.
 */
export type AudioFeedbackRunState = RunState<AudioFeedbackState>;

export interface AudioFeedbackController {
  state: AudioFeedbackRunState;
  /** Ask for feedback. Ignored while a request is already in flight. */
  start: () => void;
}

function describeError(error: unknown): string {
  if (typeof error === "string" || error === null || error === undefined) {
    return messageForAudioAnalysisError(error ?? undefined);
  }
  if (error instanceof ApiError) {
    return messageForAudioAnalysisError(error.code);
  }
  return messageForAudioAnalysisError(undefined);
}

export function useAudioFeedback(recordingId: string): AudioFeedbackController {
  const [state, setState] = useState<AudioFeedbackRunState>({ status: "idle" });
  const runnerRef = useRef<Runner<AudioFeedbackState> | null>(null);

  useEffect(() => {
    const runner = createAnalysisRunner<AudioFeedbackState>({
      recordingId,
      api: { start: startAudioFeedback, get: getAudioFeedback },
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
