/**
 * The browser side of live pitch: microphone in, notes out.
 *
 * **Everything in this file runs locally.** The microphone stream never leaves
 * the page: no audio is sent to a speech-to-text provider, to Claude, or to
 * the VocalLens API while recording. The only thing that can ever be uploaded
 * is the finished WAV, and only when the person explicitly asks for it to be
 * analysed.
 *
 * Shape of the thing:
 *
 * ```
 *   getUserMedia ─▶ MediaStreamSource ─▶ AudioWorklet ──postMessage──▶ main
 *                                        (capture only)                 │
 *                                                       ┌───────────────┘
 *                                          detectPitch ─┴─▶ PitchStream ─▶ onFrame
 * ```
 *
 * One copy of the audio. The worklet analyses nothing; detection runs on the
 * main thread over the same samples that get written to the WAV, so the
 * recording you play back is the recording that was measured.
 *
 * Three deliberate constraints:
 *
 * - **No React here.** `onFrame` fires ~30 times a second; a `setState` at that
 *   rate would re-render the tree 30 times a second for a number that changes
 *   in one DOM node. Consumers write to a ref/DOM directly.
 * - **No `MediaRecorder`.** It produces WebM/Opus or MP4, and the backend
 *   accepts WAV and MP3 by content. Raw PCM plus our own WAV header keeps one
 *   format end to end (see `lib/wav.ts`).
 * - **Nothing is left running.** Tracks stopped, nodes disconnected, context
 *   closed, object URLs revoked — a browser tab that keeps the recording
 *   indicator lit after you pressed stop is a bug, not a detail.
 */

import {
  detectPitch,
  MAX_DETECTABLE_HZ,
  MIN_DETECTABLE_HZ,
} from "./pitch-detector.ts";
import {
  PitchStream,
  summarise,
  type LivePitchSample,
  type LiveSummary,
} from "./pitch-stream.ts";
import {
  microphoneErrorCode,
  type MicrophoneErrorCode,
} from "./microphone-errors.ts";
import {
  concatFloat32,
  encodeWav,
  recordingFilename,
  WAV_MIME_TYPE,
} from "./wav.ts";

/** Where the worklet is served from. Copied verbatim, not bundled. */
const WORKLET_URL = "/pcm-capture-worklet.js";

/**
 * Samples the detector looks at. At 48 kHz this is ~43 ms, which holds two
 * periods of anything down to ~47 Hz — comfortably below any voice we search
 * for — while staying short enough that the readout tracks the voice.
 */
export const DETECTION_WINDOW = 2048;

/** At most one detection per this many ms: ~30 readings a second. */
export const DETECTION_INTERVAL_MS = 33;

/**
 * Cap on retained frames. At ~30 Hz this is about ten minutes, longer than the
 * server's duration limit, and bounded so a forgotten tab cannot grow forever.
 */
export const MAX_HISTORY_FRAMES = 20000;

export type RecorderState =
  | "idle"
  | "requesting"
  | "recording"
  | "paused"
  | "stopped";

/** A finished local recording, ready to play back or upload. */
export interface LiveRecording {
  blob: Blob;
  filename: string;
  /** Object URL for playback. The engine revokes it on `dispose`. */
  playbackUrl: string;
  durationSeconds: number;
  sampleRate: number;
  sizeBytes: number;
  samples: LivePitchSample[];
  summary: LiveSummary;
}

export interface LivePitchEngineOptions {
  /** Fired ~30×/s. Must not call `setState`. */
  onFrame?: (sample: LivePitchSample) => void;
  /** Fired ~30×/s with elapsed recording time in seconds. */
  onElapsed?: (seconds: number) => void;
  onStateChange?: (state: RecorderState) => void;
  onError?: (code: MicrophoneErrorCode) => void;
  /** Fired once when the duration cap stops the recording by itself. */
  onLimitReached?: () => void;
  /** Hard stop, in seconds. Defaults to the server's limit when known. */
  maxDurationSeconds?: number;
}

export class LivePitchEngine {
  private readonly options: LivePitchEngineOptions;

  private state: RecorderState = "idle";
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;

  /** Captured PCM, kept as blocks; concatenated once, at stop. */
  private blocks: Float32Array[] = [];
  private capturedFrames = 0;

  private readonly window = new Float32Array(DETECTION_WINDOW);
  private windowFilled = 0;
  private lastDetectionAt = -Infinity;

  private pitchStream = new PitchStream();
  private history: LivePitchSample[] = [];

  private playbackUrl: string | null = null;
  private limitNotified = false;

  constructor(options: LivePitchEngineOptions = {}) {
    this.options = options;
  }

  getState(): RecorderState {
    return this.state;
  }

  getHistory(): LivePitchSample[] {
    return this.history;
  }

  /**
   * Ask for the microphone and start listening.
   *
   * Resolves once audio is flowing, or reports a code through `onError` and
   * returns to `idle`. It never throws at the caller: a declined permission
   * prompt is an ordinary outcome, not an exception to handle.
   */
  async start(): Promise<void> {
    if (this.state === "recording" || this.state === "requesting") return;

    this.reset();
    this.setState("requesting");

    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof AudioContext === "undefined"
    ) {
      this.fail("MIC_UNSUPPORTED");
      return;
    }
    // `isSecureContext` is false on plain http, where getUserMedia is simply
    // absent in some browsers and rejects in others. Say the useful thing.
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      this.fail("MIC_INSECURE_CONTEXT");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // All three off on purpose. Automatic gain control rewrites the
          // level the analysis is taken from, and noise suppression is a
          // spectral process that moves pitch around. For a measurement tool
          // the untouched signal is the correct one.
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
        },
      });
    } catch (cause) {
      this.fail(microphoneErrorCode(cause));
      return;
    }

    // The person may have pressed stop while the permission prompt was open.
    // Read through the accessor: `setState` above has moved it on, which
    // narrowing from the guard at the top of this method cannot see.
    if (this.getState() !== "requesting") {
      for (const track of stream.getTracks()) track.stop();
      return;
    }

    try {
      const context = new AudioContext();
      await context.audioWorklet.addModule(WORKLET_URL);
      // Resuming matters on Safari, where a context can start suspended even
      // inside a click handler.
      if (context.state === "suspended") await context.resume();

      const source = context.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(context, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
        channelCount: 1,
        channelCountMode: "explicit",
      });
      worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
        this.consume(event.data);
      };
      source.connect(worklet);
      // No connection to `context.destination`: routing the microphone to the
      // speakers is how you get feedback howl in a room without headphones.

      this.stream = stream;
      this.context = context;
      this.source = source;
      this.worklet = worklet;
      this.setState("recording");
    } catch {
      for (const track of stream.getTracks()) track.stop();
      await this.teardown();
      this.fail("MIC_UNSUPPORTED");
    }
  }

  /**
   * Stop capturing without giving up the microphone.
   *
   * The source is disconnected from the worklet, so no samples are recorded
   * while paused — pausing discards audio rather than hiding it.
   */
  pause(): void {
    if (this.state !== "recording" || !this.source || !this.worklet) return;
    this.source.disconnect(this.worklet);
    this.pitchStream.reset();
    this.windowFilled = 0;
    this.setState("paused");
  }

  resume(): void {
    if (this.state !== "paused" || !this.source || !this.worklet) return;
    this.source.connect(this.worklet);
    this.setState("recording");
  }

  /**
   * Finish, release the microphone, and hand back a WAV.
   *
   * Returns `null` when nothing was captured — a recording of zero samples is
   * not a recording, and a 44-byte WAV would only fail validation later.
   */
  async stop(): Promise<LiveRecording | null> {
    if (this.state === "idle" || this.state === "stopped") return null;

    const wasRequesting = this.state === "requesting";
    this.setState("stopped");

    // Ask the worklet for its partial block before pulling the graph apart, so
    // the last fraction of a second is not silently dropped.
    this.worklet?.port.postMessage("stop");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const sampleRate = this.context?.sampleRate ?? 0;
    const blocks = this.blocks;
    const frames = this.capturedFrames;

    await this.teardown();

    if (wasRequesting || frames === 0 || sampleRate <= 0) return null;

    const samples = concatFloat32(blocks);
    this.blocks = [];
    const bytes = encodeWav(samples, sampleRate);
    // `slice` gives a plain ArrayBuffer, which BlobPart accepts on every
    // target; passing the view directly trips TS when it is a SharedArrayBuffer.
    const blob = new Blob([bytes.slice().buffer as ArrayBuffer], {
      type: WAV_MIME_TYPE,
    });

    this.revokePlayback();
    this.playbackUrl = URL.createObjectURL(blob);

    return {
      blob,
      filename: recordingFilename(),
      playbackUrl: this.playbackUrl,
      durationSeconds: frames / sampleRate,
      sampleRate,
      sizeBytes: blob.size,
      samples: this.history,
      summary: summarise(this.history),
    };
  }

  /** Release everything. Safe to call more than once. */
  async dispose(): Promise<void> {
    await this.teardown();
    this.revokePlayback();
    this.blocks = [];
    this.history = [];
  }

  // --- Internals -----------------------------------------------------------

  private consume(block: Float32Array): void {
    if (this.state !== "recording") return;

    this.blocks.push(block);
    this.capturedFrames += block.length;

    const sampleRate = this.context?.sampleRate ?? 0;
    const elapsedMs = sampleRate > 0 ? (this.capturedFrames / sampleRate) * 1000 : 0;

    this.slideWindow(block);
    this.maybeDetect(elapsedMs);
    this.options.onElapsed?.(elapsedMs / 1000);

    const limit = this.options.maxDurationSeconds;
    if (limit !== undefined && !this.limitNotified && elapsedMs / 1000 >= limit) {
      this.limitNotified = true;
      this.options.onLimitReached?.();
    }
  }

  /** Keep the most recent `DETECTION_WINDOW` samples, oldest shifted out. */
  private slideWindow(block: Float32Array): void {
    if (block.length >= DETECTION_WINDOW) {
      this.window.set(block.subarray(block.length - DETECTION_WINDOW));
      this.windowFilled = DETECTION_WINDOW;
      return;
    }
    const keep = DETECTION_WINDOW - block.length;
    this.window.copyWithin(0, block.length);
    this.window.set(block, keep);
    this.windowFilled = Math.min(DETECTION_WINDOW, this.windowFilled + block.length);
  }

  private maybeDetect(elapsedMs: number): void {
    if (this.windowFilled < DETECTION_WINDOW) return;
    if (elapsedMs - this.lastDetectionAt < DETECTION_INTERVAL_MS) return;
    this.lastDetectionAt = elapsedMs;

    const sampleRate = this.context?.sampleRate ?? 0;
    const estimate = detectPitch(this.window, sampleRate, {
      minHz: MIN_DETECTABLE_HZ,
      maxHz: MAX_DETECTABLE_HZ,
    });

    const sample = this.pitchStream.push({
      timestamp: elapsedMs,
      frequency: estimate.frequency,
      clarity: estimate.clarity,
    });

    this.history.push(sample);
    if (this.history.length > MAX_HISTORY_FRAMES) this.history.shift();

    this.options.onFrame?.(sample);
  }

  private reset(): void {
    this.blocks = [];
    this.capturedFrames = 0;
    this.windowFilled = 0;
    this.window.fill(0);
    this.lastDetectionAt = -Infinity;
    this.pitchStream = new PitchStream();
    this.history = [];
    this.limitNotified = false;
    this.revokePlayback();
  }

  private async teardown(): Promise<void> {
    if (this.worklet) {
      this.worklet.port.onmessage = null;
      this.worklet.disconnect();
      this.worklet = null;
    }
    this.source?.disconnect();
    this.source = null;

    // Stopping every track is what turns the browser's recording indicator
    // off. Closing the context releases the audio device.
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;

    const context = this.context;
    this.context = null;
    if (context && context.state !== "closed") {
      try {
        await context.close();
      } catch {
        /* Already closing. Nothing useful to do or to say. */
      }
    }
  }

  private revokePlayback(): void {
    if (this.playbackUrl !== null) {
      URL.revokeObjectURL(this.playbackUrl);
      this.playbackUrl = null;
    }
  }

  private setState(state: RecorderState): void {
    if (this.state === state) return;
    this.state = state;
    this.options.onStateChange?.(state);
  }

  private fail(code: MicrophoneErrorCode): void {
    this.setState("idle");
    this.options.onError?.(code);
  }
}
