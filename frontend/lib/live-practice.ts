/**
 * Live practice: reading the detector's output, not producing it.
 *
 * **There is one pitch detector in this product and this is not it.** Every
 * value here is derived from the `LivePitchSample` stream that
 * `lib/live-pitch-engine.ts` already produces — NSDF detection, clarity gating
 * and median smoothing all happen upstream and are untouched. What this module
 * adds is the arithmetic a practice display needs: where a needle sits, whether
 * a pitch reads flat or sharp, how consistent the last few seconds were, and
 * what range the session has covered so far.
 *
 * Pure and dependency-free apart from the note conversions, so `node --test`
 * can drive all of it with a scripted sequence instead of a microphone.
 */

import { midiToNoteName, NOTE_NAMES } from "./pitch.ts";
import type { LivePitchSample } from "./pitch-stream.ts";

/** The meter's ends, in cents. Beyond ±50 the next note is nearer. */
export const METER_RANGE_CENTS = 50;

/** Within this many cents of a note, a pitch reads as in tune. */
export const IN_TUNE_CENTS = 25;

/**
 * Voiced frames the rolling consistency window holds — about 4 seconds at the
 * detector's ~30 frames a second. Long enough to mean something, short enough
 * that fixing your pitch is visible while you are still singing.
 */
export const CONSISTENCY_WINDOW = 128;

/**
 * Voiced frames required before a consistency figure is shown at all.
 *
 * About a second. Below this the number swings between 0% and 100% on single
 * frames, and a figure that unstable is worse than none — so it reads
 * "Not enough yet", which is a different statement from 0%.
 */
export const MIN_CONSISTENCY_FRAMES = 30;

/**
 * Consecutive voiced frames a pitch must hold to count towards the session
 * range, and how far it may drift and still count as the same held pitch.
 *
 * The same rule the backend applies to a saved recording, and for the same
 * reason: a frequency touched for a few frames while crossing between two notes
 * is not a note anyone sang, and letting it define the bottom of a range is how
 * a range becomes fiction.
 */
export const MIN_RANGE_FRAMES = 5;
export const RANGE_CONTINUITY_SEMITONES = 1;

/** Where the needle sits, and whether the real value ran off the end. */
export interface MeterReading {
  /** −1 (50 cents flat) to +1 (50 cents sharp). Always within range. */
  position: number;
  /** True when the true deviation was beyond the meter's ends. */
  clamped: boolean;
}

/**
 * Position a needle for a cents deviation.
 *
 * Clamps the *display* only. The true value stays available for the text
 * readout, because a needle pinned at the end must not be read as "exactly 50
 * cents sharp".
 */
export function meterReading(cents: number | null): MeterReading | null {
  if (cents === null || !Number.isFinite(cents)) return null;
  const clamped = Math.max(-METER_RANGE_CENTS, Math.min(METER_RANGE_CENTS, cents));
  return { position: clamped / METER_RANGE_CENTS, clamped: clamped !== cents };
}

/**
 * How the pitch sits against the note, in words.
 *
 * Text, not colour, and not only a needle position: flat and sharp have to
 * reach someone who cannot see the meter at all.
 */
export function tuningLabel(cents: number | null): string | null {
  if (cents === null || !Number.isFinite(cents)) return null;
  const rounded = Math.round(cents);
  if (Math.abs(rounded) <= 3) return "In tune";
  return rounded < 0 ? `${Math.abs(rounded)} cents flat` : `${rounded} cents sharp`;
}

/** `"flat"`, `"sharp"` or `"in-tune"` — for styling that is not the only cue. */
export type TuningDirection = "flat" | "sharp" | "in-tune";

export function tuningDirection(cents: number | null): TuningDirection | null {
  if (cents === null || !Number.isFinite(cents)) return null;
  if (Math.abs(cents) <= 3) return "in-tune";
  return cents < 0 ? "flat" : "sharp";
}

/** What the practice card shows, refreshed a few times a second — not per frame. */
export interface LiveStats {
  /**
   * Share of the last {@link CONSISTENCY_WINDOW} voiced frames within
   * {@link IN_TUNE_CENTS} of the nearest note, 0–1.
   *
   * `null` until {@link MIN_CONSISTENCY_FRAMES} voiced frames exist. That is
   * "not enough data yet", which is not zero and must never be shown as 0%.
   */
  consistency: number | null;
  /** Voiced frames in the rolling window. Lets the UI say why it is waiting. */
  windowFrames: number;
  /** Voiced frames since the session began. */
  voicedFrames: number;
  totalFrames: number;
  /** Lowest and highest *held* pitch this session. `null` until one is held. */
  lowestNote: string | null;
  highestNote: string | null;
  lowestMidi: number | null;
  highestMidi: number | null;
  /** Whole semitones between them. `null` when there is no range yet. */
  semitoneSpan: number | null;
}

export const EMPTY_STATS: LiveStats = {
  consistency: null,
  windowFrames: 0,
  voicedFrames: 0,
  totalFrames: 0,
  lowestNote: null,
  highestNote: null,
  lowestMidi: null,
  highestMidi: null,
  semitoneSpan: null,
};

/**
 * Accumulates a practice session, one frame at a time.
 *
 * Stateful, and deliberately outside React: `push` is called ~30 times a
 * second from the detector's subscription, and `snapshot` is read a few times a
 * second by whatever wants to render. Nothing here triggers a render on its own.
 */
export class LivePracticeStats {
  /** Rolling window of "was this frame in tune", oldest first. */
  private readonly window: boolean[] = [];
  private voiced = 0;
  private total = 0;

  private lowest: number | null = null;
  private highest: number | null = null;

  /** The run of consecutive, continuous voiced frames being accumulated. */
  private run: number[] = [];

  push(sample: LivePitchSample): void {
    this.total += 1;

    if (!sample.voiced || sample.midi === null || sample.cents === null) {
      // A gap breaks the run: a "held" pitch either side of silence is two
      // pitches, and joining them would let a run qualify that never happened.
      this.run = [];
      return;
    }

    this.voiced += 1;
    this.window.push(Math.abs(sample.cents) <= IN_TUNE_CENTS);
    if (this.window.length > CONSISTENCY_WINDOW) this.window.shift();

    this.trackRange(sample.midi);
  }

  /**
   * Admit a frame to the range only once its run has been held long enough.
   *
   * The first {@link MIN_RANGE_FRAMES} frames of a run are buffered; when the
   * run qualifies they are admitted together and every later frame in the same
   * run is admitted as it arrives.
   */
  private trackRange(midi: number): void {
    const previous = this.run[this.run.length - 1];
    const continues =
      previous === undefined ||
      Math.abs(midi - previous) <= RANGE_CONTINUITY_SEMITONES;

    if (!continues) this.run = [];
    this.run.push(midi);

    if (this.run.length < MIN_RANGE_FRAMES) return;
    if (this.run.length === MIN_RANGE_FRAMES) {
      for (const held of this.run) this.admit(held);
      return;
    }
    this.admit(midi);
  }

  private admit(midi: number): void {
    if (this.lowest === null || midi < this.lowest) this.lowest = midi;
    if (this.highest === null || midi > this.highest) this.highest = midi;
  }

  snapshot(): LiveStats {
    const inTune = this.window.reduce((count, value) => count + (value ? 1 : 0), 0);
    return {
      consistency:
        this.window.length >= MIN_CONSISTENCY_FRAMES ? inTune / this.window.length : null,
      windowFrames: this.window.length,
      voicedFrames: this.voiced,
      totalFrames: this.total,
      lowestNote: midiToNoteName(this.lowest),
      highestNote: midiToNoteName(this.highest),
      lowestMidi: this.lowest,
      highestMidi: this.highest,
      semitoneSpan:
        this.lowest !== null && this.highest !== null
          ? Math.round(this.highest - this.lowest)
          : null,
    };
  }

  reset(): void {
    this.window.length = 0;
    this.run = [];
    this.voiced = 0;
    this.total = 0;
    this.lowest = null;
    this.highest = null;
  }
}

/* --- Target note ---------------------------------------------------------- */

/** Notes offerable as a target: C2 to C6, the range the detector searches. */
export const TARGET_MIDI_LOW = 36;
export const TARGET_MIDI_HIGH = 84;

export interface TargetOption {
  midi: number;
  note: string;
}

export function targetOptions(): TargetOption[] {
  const options: TargetOption[] = [];
  for (let midi = TARGET_MIDI_LOW; midi <= TARGET_MIDI_HIGH; midi++) {
    const note = midiToNoteName(midi);
    if (note !== null) options.push({ midi, note });
  }
  return options;
}

export interface TargetComparison {
  targetNote: string;
  /** What is actually being sung. Never the target — see below. */
  detectedNote: string;
  /** Signed cents from the *target*, not from the nearest note. */
  centsFromTarget: number;
  /** Whole semitones between the detected note and the target. 0 when on it. */
  semitonesFromTarget: number;
  /** True only when the detected note *is* the target note. */
  onTarget: boolean;
}

/**
 * Compare what is being sung with what was asked for.
 *
 * The distinction this function exists to protect: **a detected pitch is not a
 * correct pitch.** Singing B3 against a C4 target produces a reading of B3 and
 * −100 cents, not "C4, slightly flat". `onTarget` is true only when the nearest
 * note to the sung pitch is the target itself, so an octave error or a
 * neighbouring semitone can never be reported as success.
 *
 * `null` when nothing is voiced — there is nothing to compare.
 */
export function compareToTarget(
  sample: LivePitchSample,
  targetMidi: number,
): TargetComparison | null {
  if (!sample.voiced || sample.midi === null) return null;
  const targetNote = midiToNoteName(targetMidi);
  const detectedNote = sample.note ?? midiToNoteName(sample.midi);
  if (targetNote === null || detectedNote === null) return null;

  const centsFromTarget = (sample.midi - targetMidi) * 100;
  return {
    targetNote,
    detectedNote,
    centsFromTarget,
    semitonesFromTarget: Math.round(sample.midi) - targetMidi,
    onTarget: Math.round(sample.midi) === targetMidi,
  };
}

/** How far off the target is, in words. Text, not colour. */
export function targetLabel(comparison: TargetComparison | null): string | null {
  if (comparison === null) return null;
  if (comparison.onTarget) return tuningLabel(comparison.centsFromTarget);

  const semitones = Math.abs(comparison.semitonesFromTarget);
  const direction = comparison.semitonesFromTarget < 0 ? "below" : "above";
  if (semitones === 12) return `An octave ${direction} the target`;
  return `${semitones} ${semitones === 1 ? "semitone" : "semitones"} ${direction} the target`;
}

/** Note letter without the octave, for a compact selector. */
export function pitchClass(midi: number): string {
  return NOTE_NAMES[((midi % 12) + 12) % 12];
}
