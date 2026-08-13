"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getAudioAnalysis,
  getMusicalKey,
  getNoteBreakdown,
  getPitchTimeline,
  startAudioAnalysis,
} from "@/lib/api";
import { messageForAudioAnalysisError } from "@/lib/audio-analysis-errors";
import {
  createAnalysisRunner,
  type AudioAnalysisRunner,
  type AudioRunState,
} from "@/lib/analysis-runner";
import type { MusicalKey, NoteBreakdown, PitchPoint } from "@/types/api";

/**
 * React wrapper around the audio-analysis runner.
 *
 * The same runner the speech analysis uses — polling, backoff, not starting
 * twice, stopping cleanly — parameterised by the response type. Two copies of
 * that logic would be two places for the overlapping-request bug to live.
 *
 * The timeline, the note breakdown and the estimated key are fetched once,
 * after the analysis completes. All three are derived views of the same stored
 * measurement, so they are requested together rather than in cascading effects,
 * and a failure in any one leaves the others — and the summary numbers — intact.
 */

/** Points requested for the graph. More than a chart can draw is waste. */
export const GRAPH_POINTS = 600;

export interface AudioAnalysisController {
  state: AudioRunState;
  /** Start measuring. Ignored while a measurement is already in flight. */
  start: () => void;
  /** The timeline, once fetched. `null` until then. */
  timeline: PitchPoint[] | null;
  timelineError: string | null;
  /** The note breakdown, once fetched. `null` until then. */
  breakdown: NoteBreakdown | null;
  breakdownError: string | null;
  /**
   * The estimated key, once fetched. `null` until then.
   *
   * Note the two nulls: this is `null` while the request is in flight, and the
   * `key` field *inside* it is `null` when the measurement established no key.
   * The second is a successful answer and is not an error.
   */
  musicalKey: MusicalKey | null;
  musicalKeyError: string | null;
}

function describeError(error: unknown): string {
  if (typeof error === "string" || error === null || error === undefined) {
    // An `error_code` taken straight off a failed analysis record.
    return messageForAudioAnalysisError(error ?? undefined);
  }
  if (error instanceof ApiError) {
    return messageForAudioAnalysisError(error.code);
  }
  return messageForAudioAnalysisError(undefined);
}

export function useAudioAnalysis(recordingId: string): AudioAnalysisController {
  const [state, setState] = useState<AudioRunState>({ status: "idle" });
  const [timeline, setTimeline] = useState<PitchPoint[] | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [breakdown, setBreakdown] = useState<NoteBreakdown | null>(null);
  const [breakdownError, setBreakdownError] = useState<string | null>(null);
  const [musicalKey, setMusicalKey] = useState<MusicalKey | null>(null);
  const [musicalKeyError, setMusicalKeyError] = useState<string | null>(null);
  const runnerRef = useRef<AudioAnalysisRunner | null>(null);

  useEffect(() => {
    const runner = createAnalysisRunner({
      recordingId,
      api: { start: startAudioAnalysis, get: getAudioAnalysis },
      onState: setState,
      describeError,
    });
    runnerRef.current = runner;

    return () => {
      runner.stop();
      runnerRef.current = null;
    };
  }, [recordingId]);

  // Fetched when — and only when — a measurement finishes with points to draw.
  const completed = state.status === "completed";
  const pointCount = state.status === "completed" ? state.analysis.pitch_point_count : 0;

  useEffect(() => {
    if (!completed || pointCount === 0) return;

    const controller = new AbortController();

    getPitchTimeline(recordingId, GRAPH_POINTS, controller.signal)
      .then((response) => setTimeline(response.points))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // The graph is an extra; losing it must not take the numbers with it.
        setTimelineError(describeError(error));
      });

    getNoteBreakdown(recordingId, controller.signal)
      .then(setBreakdown)
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setBreakdownError(describeError(error));
      });

    getMusicalKey(recordingId, controller.signal)
      .then(setMusicalKey)
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // Only a genuine failure lands here. A recording whose notes settled no
        // key answers 200 with `key: null`, which is a measurement, not this.
        setMusicalKeyError(describeError(error));
      });

    return () => controller.abort();
  }, [recordingId, completed, pointCount]);

  const start = useCallback(() => {
    runnerRef.current?.start();
  }, []);

  return {
    state,
    start,
    timeline,
    timelineError,
    breakdown,
    breakdownError,
    musicalKey,
    musicalKeyError,
  };
}
