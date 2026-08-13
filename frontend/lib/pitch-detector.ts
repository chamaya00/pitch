/**
 * Monophonic pitch detection, in the browser, on one window of samples.
 *
 * Method: the **normalised square difference function** (McLeod's MPM), with
 * parabolic interpolation around the chosen peak.
 *
 * Why this one. Plain autocorrelation is the obvious choice and is wrong here
 * in a specific way: its peaks grow at integer multiples of the true period, so
 * a sung note reliably reads an octave too low every few frames. The NSDF
 * normalises each lag by the energy actually overlapping at that lag, which
 * flattens that bias, and taking the *first* peak above a fraction of the
 * global maximum is what fixes the octave error rather than papering over it.
 *
 * The clarity value it returns is a real measurement — the height of the
 * normalised peak, 0 to 1 — not a confidence we invented. Below the threshold
 * the caller must show no note at all: a detector always emits *some* lag, and
 * silence would otherwise render as a confident wrong note.
 *
 * Pure and dependency-free: `Float32Array` in, numbers out, no audio context,
 * so `node --test` can drive it with synthetic tones.
 */

/** Search range. Below/above this is not a human voice worth reporting. */
export const MIN_DETECTABLE_HZ = 60;
export const MAX_DETECTABLE_HZ = 1200;

/**
 * Clarity below this is not a pitch. 0.9 is deliberately strict — a wrong note
 * shown confidently is worse than no note shown at all.
 */
export const DEFAULT_CLARITY_THRESHOLD = 0.9;

/** A peak must reach this share of the strongest peak to be chosen. */
const PEAK_ACCEPTANCE = 0.85;

/** Below this RMS the window is silence; skip the arithmetic entirely. */
export const SILENCE_RMS = 0.005;

export interface PitchEstimate {
  /** Hz, or `null` when no pitch could be established. */
  frequency: number | null;
  /** Normalised peak height, 0–1. Zero when nothing was found. */
  clarity: number;
  /** Root-mean-square level of the window, 0–1. */
  rms: number;
}

export function rootMeanSquare(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/**
 * Estimate the pitch of one window.
 *
 * `samples` should be a contiguous block of mono audio in [-1, 1]. Windows
 * shorter than two periods of the lowest searched frequency cannot support a
 * result and return `null` rather than a guess.
 */
export function detectPitch(
  samples: Float32Array,
  sampleRate: number,
  options: { minHz?: number; maxHz?: number } = {},
): PitchEstimate {
  const rms = rootMeanSquare(samples);
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    return { frequency: null, clarity: 0, rms };
  }
  if (rms < SILENCE_RMS) {
    return { frequency: null, clarity: 0, rms };
  }

  const minHz = options.minHz ?? MIN_DETECTABLE_HZ;
  const maxHz = options.maxHz ?? MAX_DETECTABLE_HZ;

  const minLag = Math.max(2, Math.floor(sampleRate / maxHz));
  const maxLag = Math.min(
    Math.floor(sampleRate / minHz),
    Math.floor(samples.length / 2),
  );
  if (maxLag <= minLag) {
    return { frequency: null, clarity: 0, rms };
  }

  // NSDF: 2 * sum(x[i] * x[i+lag]) / sum(x[i]^2 + x[i+lag]^2), per lag.
  const nsdf = new Float32Array(maxLag + 1);
  for (let lag = minLag; lag <= maxLag; lag++) {
    let correlation = 0;
    let energy = 0;
    const count = samples.length - lag;
    for (let i = 0; i < count; i++) {
      const a = samples[i];
      const b = samples[i + lag];
      correlation += a * b;
      energy += a * a + b * b;
    }
    nsdf[lag] = energy > 0 ? (2 * correlation) / energy : 0;
  }

  // Take the first peak that is tall enough, not the tallest overall: the
  // tallest is often at twice the true period, which is the octave error.
  let strongest = 0;
  for (let lag = minLag; lag <= maxLag; lag++) {
    if (nsdf[lag] > strongest) strongest = nsdf[lag];
  }
  if (strongest <= 0) {
    return { frequency: null, clarity: 0, rms };
  }

  const cutoff = strongest * PEAK_ACCEPTANCE;
  let chosenLag = -1;
  let rising = false;
  for (let lag = minLag + 1; lag < maxLag; lag++) {
    if (nsdf[lag] > nsdf[lag - 1]) {
      rising = true;
      continue;
    }
    // Falling again: the previous sample was a local maximum.
    if (rising && nsdf[lag - 1] >= cutoff) {
      chosenLag = lag - 1;
      break;
    }
    rising = false;
  }
  if (chosenLag < 0) {
    return { frequency: null, clarity: 0, rms };
  }

  // Parabolic interpolation through the three points around the peak. Without
  // it the period is quantised to whole samples, which at 48 kHz is worth tens
  // of cents in the vocal range — enough to make the cents readout useless.
  const previous = nsdf[chosenLag - 1];
  const current = nsdf[chosenLag];
  const next = nsdf[chosenLag + 1];
  const denominator = previous - 2 * current + next;
  const offset = denominator === 0 ? 0 : (0.5 * (previous - next)) / denominator;
  const refinedLag = chosenLag + (Number.isFinite(offset) ? offset : 0);

  if (refinedLag <= 0) {
    return { frequency: null, clarity: 0, rms };
  }

  const frequency = sampleRate / refinedLag;
  const clarity = Math.max(0, Math.min(1, current));

  if (frequency < minHz || frequency > maxHz) {
    return { frequency: null, clarity, rms };
  }

  return { frequency, clarity, rms };
}
