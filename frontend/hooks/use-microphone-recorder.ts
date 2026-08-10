"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  LivePitchEngine,
  type LiveRecording,
  type RecorderState,
} from "@/lib/live-pitch-engine";
import type { MicrophoneErrorCode } from "@/lib/microphone-errors";
import type { LivePitchSample } from "@/lib/pitch-stream";

/**
 * React's share of the live recorder: lifecycle, errors, and the finished file.
 *
 * Note what is *not* here. Pitch frames arrive about thirty times a second and
 * never touch React state — components subscribe to them and write to the DOM
 * themselves. Re-rendering a page thirty times a second to move one number is
 * how a live display starts dropping frames, and the number in question does
 * not participate in layout.
 *
 * Elapsed time is state, but only changes when the whole second does, so the
 * timer costs one render a second rather than thirty.
 */
export interface MicrophoneRecorder {
  state: RecorderState;
  errorCode: MicrophoneErrorCode | null;
  elapsedSeconds: number;
  /** Set once `stop()` has produced a file. */
  recording: LiveRecording | null;
  /** True when the duration limit ended the recording rather than the user. */
  stoppedAtLimit: boolean;
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  /** Throw the take away and return to the start. */
  discard: () => void;
  /** Register a per-frame listener. Returns an unsubscribe function. */
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void;
}

export function useMicrophoneRecorder(
  maxDurationSeconds?: number,
): MicrophoneRecorder {
  const [state, setState] = useState<RecorderState>("idle");
  const [errorCode, setErrorCode] = useState<MicrophoneErrorCode | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [recording, setRecording] = useState<LiveRecording | null>(null);
  const [stoppedAtLimit, setStoppedAtLimit] = useState(false);

  const engineRef = useRef<LivePitchEngine | null>(null);
  const listenersRef = useRef(new Set<(sample: LivePitchSample) => void>());
  const wholeSecondRef = useRef(0);
  const mountedRef = useRef(true);

  const subscribe = useCallback(
    (listener: (sample: LivePitchSample) => void) => {
      const listeners = listenersRef.current;
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    [],
  );

  const stop = useCallback(() => {
    const engine = engineRef.current;
    if (!engine) return;
    void engine.stop().then((result) => {
      if (!mountedRef.current) return;
      setRecording(result);
    });
  }, []);

  // Built on first use rather than on mount: constructing it costs nothing,
  // but a recorder that exists before anyone asked to record is a device
  // waiting to be opened by accident.
  const engine = useCallback((): LivePitchEngine => {
    if (engineRef.current) return engineRef.current;
    engineRef.current = new LivePitchEngine({
      maxDurationSeconds,
      onFrame: (sample) => {
        for (const listener of listenersRef.current) listener(sample);
      },
      onElapsed: (seconds) => {
        const whole = Math.floor(seconds);
        if (whole === wholeSecondRef.current) return;
        wholeSecondRef.current = whole;
        if (mountedRef.current) setElapsedSeconds(whole);
      },
      onStateChange: (next) => {
        if (mountedRef.current) setState(next);
      },
      onError: (code) => {
        if (mountedRef.current) setErrorCode(code);
      },
      onLimitReached: () => {
        if (mountedRef.current) setStoppedAtLimit(true);
        stop();
      },
    });
    return engineRef.current;
  }, [maxDurationSeconds, stop]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Unmounting must release the device. Anything else leaves the browser's
      // recording indicator lit on a page that is no longer there.
      void engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, []);

  const start = useCallback(() => {
    setErrorCode(null);
    setRecording(null);
    setStoppedAtLimit(false);
    setElapsedSeconds(0);
    wholeSecondRef.current = 0;
    void engine().start();
  }, [engine]);

  const pause = useCallback(() => engineRef.current?.pause(), []);
  const resume = useCallback(() => engineRef.current?.resume(), []);

  const discard = useCallback(() => {
    void engineRef.current?.dispose();
    engineRef.current = null;
    setRecording(null);
    setErrorCode(null);
    setStoppedAtLimit(false);
    setElapsedSeconds(0);
    wholeSecondRef.current = 0;
    setState("idle");
  }, []);

  return {
    state,
    errorCode,
    elapsedSeconds,
    recording,
    stoppedAtLimit,
    start,
    pause,
    resume,
    stop,
    discard,
    subscribe,
  };
}
