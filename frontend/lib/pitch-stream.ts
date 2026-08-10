/**
 * Turning a stream of raw estimates into something steady enough to read.
 *
 * Two problems, handled separately because they have different cures.
 *
 * **Wrong notes.** A detector emits an estimate for every window, including
 * windows that contain a cough, a consonant or a chair. Gating on clarity fixes
 * that: below the threshold the sample is marked unvoiced and carries no note
 * at all, so nothing is displayed rather than something wrong.
 *
 * **Jitter.** Even a steadily sung note wobbles by a few cents frame to frame,
 * which makes the readout unreadable. The cure is a **median** of the last few
 * voiced frames, not a mean: a median ignores a single octave-jumped outlier
 * entirely, where a mean would drag the display halfway towards it.
 *
 * The window is deliberately short (5 frames ≈ 165 ms at 30 Hz). Longer looks
 * calmer and starts to lag visibly behind the voice, which for a live display
 * is the worse failure — the number has to move when the singer does.
 *
 * Pure and dependency-free, so `node --test` can drive it with a scripted
 * sequence instead of a microphone.
 */

import { describeFrequency } from "./pitch.ts";
import { DEFAULT_CLARITY_THRESHOLD } from "./pitch-detector.ts";

/** One moment of the live stream, as the UI consumes it. */
export interface LivePitchSample {
  /** Milliseconds since the recording started. */
  timestamp: number;
  frequency: number | null;
  midi: number | null;
  note: string | null;
  cents: number | null;
  /** The detector's measured clarity, 0–1. Not an invented score. */
  confidence: number;
  voiced: boolean;
}

export const SMOOTHING_WINDOW = 5;

/** Consecutive unvoiced frames before the display gives up on the note. */
export const UNVOICED_FRAMES_TO_CLEAR = 3;

export interface RawEstimate {
  timestamp: number;
  frequency: number | null;
  clarity: number;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Smooths a live stream of raw estimates, one frame at a time.
 *
 * Stateful by necessity — it remembers the last few voiced frames — but it is
 * plain data, created and discarded with a recording session.
 */
export class PitchStream {
  private readonly recent: number[] = [];
  private unvoicedRun = 0;
  private lastVoiced: LivePitchSample | null = null;
  private readonly clarityThreshold: number;
  private readonly windowSize: number;

  // Written out rather than declared as constructor parameter properties:
  // Node's type-stripping test runner cannot rewrite those, and these modules
  // are tested directly under `node --test`.
  constructor(
    clarityThreshold: number = DEFAULT_CLARITY_THRESHOLD,
    windowSize: number = SMOOTHING_WINDOW,
  ) {
    this.clarityThreshold = clarityThreshold;
    this.windowSize = windowSize;
  }

  /** Feed one raw estimate; get back what should be displayed. */
  push(estimate: RawEstimate): LivePitchSample {
    const usable =
      estimate.frequency !== null &&
      Number.isFinite(estimate.frequency) &&
      estimate.clarity >= this.clarityThreshold;

    if (!usable) {
      this.unvoicedRun += 1;
      // Hold the previous note briefly. A consonant in the middle of a sung
      // word should not blank the display and immediately bring it back.
      if (
        this.lastVoiced !== null &&
        this.unvoicedRun < UNVOICED_FRAMES_TO_CLEAR
      ) {
        return { ...this.lastVoiced, timestamp: estimate.timestamp };
      }
      this.recent.length = 0;
      this.lastVoiced = null;
      return {
        timestamp: estimate.timestamp,
        frequency: null,
        midi: null,
        note: null,
        cents: null,
        confidence: estimate.clarity,
        voiced: false,
      };
    }

    this.unvoicedRun = 0;
    this.recent.push(estimate.frequency as number);
    if (this.recent.length > this.windowSize) this.recent.shift();

    const smoothed = median(this.recent);
    const reading = describeFrequency(smoothed);
    if (reading === null) {
      return {
        timestamp: estimate.timestamp,
        frequency: null,
        midi: null,
        note: null,
        cents: null,
        confidence: estimate.clarity,
        voiced: false,
      };
    }

    const sample: LivePitchSample = {
      timestamp: estimate.timestamp,
      frequency: reading.frequency,
      midi: reading.midi,
      note: reading.note,
      cents: reading.cents,
      confidence: estimate.clarity,
      voiced: true,
    };
    this.lastVoiced = sample;
    return sample;
  }

  reset(): void {
    this.recent.length = 0;
    this.unvoicedRun = 0;
    this.lastVoiced = null;
  }
}

/* --- Session summary ------------------------------------------------------
 *
 * A browser-side estimate, and labelled as one everywhere it is shown. It does
 * not share an algorithm or a definition with anything the backend computes,
 * so it must never be presented as the recording's analysis.
 */

export interface LiveSummary {
  /** Lowest and highest confidently voiced notes, or `null` if never voiced. */
  lowestNote: string | null;
  highestNote: string | null;
  /** The note held longest, by frame count. */
  mostFrequentNote: string | null;
  /** Mean deviation from the nearest note, in cents. */
  averageCents: number | null;
  /** Share of frames that were voiced, 0–1. `null` with no frames at all. */
  voicedFraction: number | null;
  voicedFrames: number;
  totalFrames: number;
}

export function summarise(samples: LivePitchSample[]): LiveSummary {
  const empty: LiveSummary = {
    lowestNote: null,
    highestNote: null,
    mostFrequentNote: null,
    averageCents: null,
    voicedFraction: null,
    voicedFrames: 0,
    totalFrames: samples.length,
  };
  if (samples.length === 0) return empty;

  const voiced = samples.filter(
    (sample): sample is LivePitchSample & { midi: number; note: string; cents: number } =>
      sample.voiced && sample.midi !== null && sample.note !== null && sample.cents !== null,
  );

  const voicedFraction = voiced.length / samples.length;
  if (voiced.length === 0) {
    return { ...empty, voicedFraction, totalFrames: samples.length };
  }

  let lowest = voiced[0];
  let highest = voiced[0];
  const counts = new Map<string, number>();
  let centsTotal = 0;

  for (const sample of voiced) {
    if (sample.midi < lowest.midi) lowest = sample;
    if (sample.midi > highest.midi) highest = sample;
    counts.set(sample.note, (counts.get(sample.note) ?? 0) + 1);
    centsTotal += sample.cents;
  }

  let mostFrequentNote: string | null = null;
  let best = 0;
  for (const [note, count] of counts) {
    if (count > best) {
      best = count;
      mostFrequentNote = note;
    }
  }

  return {
    lowestNote: lowest.note,
    highestNote: highest.note,
    mostFrequentNote,
    averageCents: centsTotal / voiced.length,
    voicedFraction,
    voicedFrames: voiced.length,
    totalFrames: samples.length,
  };
}
