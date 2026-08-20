/**
 * Melodic key estimation, in the browser, over the live pitch stream.
 *
 * **The same mathematics as `backend/app/services/audio_analysis/key.py`, in a
 * second runtime.** It exists twice for one reason: microphone audio never
 * leaves the page, so the estimate cannot be computed on the server without
 * breaking the guarantee that Live Vocal Practice is built on. That is the
 * rule `lib/pitch.ts` and `audio_analysis/pitch.py` already follow — *the
 * implementations are separate on purpose and the mathematics is not* — and
 * `fixtures/key-parity.json` is what stops the two drifting: one table of
 * profiles and verdicts, asserted by `tests/live-key.test.ts` here and by
 * `backend/tests/test_audio_key.py` there.
 *
 * ```
 *   counts[midi mod 12] ─▶ shares ─▶ 24 correlations ─▶ key | refusal
 * ```
 *
 * Every constant below is copied from `key.py` and pinned by a test. None of
 * them was re-derived here, because re-deriving them is exactly how two
 * implementations of one measurement stop being one measurement. The reasoning
 * for each is in `key.py` and in `docs/audio-analysis.md`; the short version is
 * that the obvious implementation reports a confident key for a hum, and three
 * gates in front of the correlation are what stop it:
 *
 * - **enough distinct pitch classes** — a three-note arpeggio scores a *higher*
 *   margin than real singing, and only this gate catches it;
 * - **enough voiced time** — five classes inside half a second are not five
 *   notes;
 * - **a margin over the next candidate of any kind** — never a raw correlation,
 *   which a single held note takes to +0.684.
 *
 * What this module does **not** do: commit, hold or display anything. A verdict
 * per tick is not a readout — a label that changes twice a second is worse than
 * no label — and the hysteresis and dwell that turn one into the other live in
 * `live-practice.ts`, where the session state is.
 *
 * Pure and dependency-free apart from the note names, so `node --test` drives
 * all of it without a microphone.
 */

import { NOTE_NAMES } from "./pitch.ts";

const SEMITONES = 12;

export type KeyMode = "major" | "minor";

/**
 * Why no key was reported. The same three the backend returns, spelled the
 * same, so the parity table can assert one string against both.
 */
export type LiveKeyUnmeasuredReason =
  | "TOO_FEW_PITCH_CLASSES"
  | "TOO_LITTLE_VOICED_TIME"
  | "AMBIGUOUS";

/** One published pair of key profiles, named so a result can cite it. */
export interface KeyProfileSet {
  name: string;
  /** Expected relative weight of each scale degree, starting at the tonic. */
  major: readonly number[];
  minor: readonly number[];
}

/**
 * Temperley's revised profiles (2001), from the Kostka–Payne corpus.
 *
 * The set the backend ships, chosen there on one measurement: it separates
 * music from noise by about 12× where Krumhansl–Kessler manages about 3×, and
 * that band is what lets the confidence gate sit clear of the noise floor
 * without discarding real melodies. Not re-argued here — see `key.py`.
 */
export const TEMPERLEY: KeyProfileSet = {
  name: "temperley",
  major: [0.748, 0.06, 0.488, 0.082, 0.67, 0.46, 0.096, 0.715, 0.104, 0.366, 0.057, 0.4],
  minor: [0.712, 0.084, 0.474, 0.618, 0.049, 0.46, 0.105, 0.747, 0.404, 0.067, 0.133, 0.33],
};

export const DEFAULT_PROFILE_SET = TEMPERLEY;

/** Share of voiced time below which a pitch class does not count as *used*. */
export const MIN_PITCH_CLASS_SHARE = 2.0;

/** Distinct pitch classes required before any key is reported. */
export const MIN_DISTINCT_PITCH_CLASSES = 5;

/** Pitched seconds required before any key is reported. */
export const MIN_VOICED_SECONDS = 1.0;

/** The margin the best candidate must hold over the next one, of any kind. */
export const MIN_KEY_CONFIDENCE = 0.05;

/** One pitch class and the share of voiced time it carried, 0–100. */
export interface PitchClassShare {
  pitchClass: number;
  name: string;
  percentageOfVoicedTime: number;
}

/** One of the 24 keys, with how well the profile fitted it. */
export interface KeyCandidate {
  tonic: number;
  mode: KeyMode;
  correlation: number;
}

/** An answered key: which one, how far clear it stood, and what came second. */
export interface LiveKeyEstimate {
  tonic: string;
  mode: KeyMode;
  /** Margin over the next candidate. Not a percentage and not a probability. */
  confidence: number;
  alternative: { tonic: string; mode: KeyMode; confidence: number } | null;
}

/**
 * What one tick's worth of evidence supports.
 *
 * `key` and `unmeasuredReason` are mutually exclusive: exactly one is non-null,
 * which is the invariant the backend's model enforces and a test asserts here.
 * `ranked` travels with both because the commitment rules need the correlation
 * of a key that is *not* winning — hysteresis is a comparison against the
 * incumbent, and the incumbent may have slipped to third.
 */
export interface LiveKeyVerdict {
  key: LiveKeyEstimate | null;
  unmeasuredReason: LiveKeyUnmeasuredReason | null;
  pitchClasses: readonly PitchClassShare[];
  distinctPitchClasses: number;
  voicedSeconds: number;
  method: string;
  ranked: readonly KeyCandidate[];
}

/**
 * Fold twelve pitch-class counts into twelve shares of voiced time.
 *
 * Always twelve entries, in pitch-class order, including the unused ones at
 * zero: a key is decided as much by which classes are *absent* as by which are
 * present. An empty tuple is "nothing pitched was counted", which a caller must
 * render as such — never as twelve measured zeroes.
 */
export function pitchClassProfile(counts: readonly number[]): PitchClassShare[] {
  if (counts.length !== SEMITONES) {
    throw new RangeError(`a pitch-class profile has ${SEMITONES} counts, not ${counts.length}`);
  }
  const total = counts.reduce((sum, count) => sum + count, 0);
  if (total <= 0) return [];

  return counts.map((count, pitchClass) => ({
    pitchClass,
    name: NOTE_NAMES[pitchClass],
    // Of voiced time, the same denominator the note breakdown uses, so the
    // twelve shares sum to 100 without being normalised afterwards.
    percentageOfVoicedTime: (100 * count) / total,
  }));
}

/**
 * Decide which key a pitch-class profile best fits, or refuse to.
 *
 * The gates are checked before the correlation and in a fixed order — voiced
 * time, then distinct pitch classes, then the margin — so a profile failing
 * more than one is described by the most fundamental thing missing.
 */
export function estimateKey(
  shares: readonly PitchClassShare[],
  voicedSeconds: number,
  profiles: KeyProfileSet = DEFAULT_PROFILE_SET,
): LiveKeyVerdict {
  const distinct = shares.filter(
    (share) => share.percentageOfVoicedTime >= MIN_PITCH_CLASS_SHARE,
  ).length;
  const ranked = shares.length === SEMITONES ? rankedCandidates(shares, profiles) : [];

  const refuse = (reason: LiveKeyUnmeasuredReason): LiveKeyVerdict => ({
    key: null,
    unmeasuredReason: reason,
    pitchClasses: shares,
    distinctPitchClasses: distinct,
    voicedSeconds,
    method: profiles.name,
    ranked,
  });

  if (shares.length === 0 || voicedSeconds < MIN_VOICED_SECONDS) {
    return refuse("TOO_LITTLE_VOICED_TIME");
  }
  if (distinct < MIN_DISTINCT_PITCH_CLASSES) return refuse("TOO_FEW_PITCH_CLASSES");

  const [best, second, third] = ranked;
  const confidence = best.correlation - second.correlation;
  if (confidence < MIN_KEY_CONFIDENCE) return refuse("AMBIGUOUS");

  return {
    key: {
      tonic: NOTE_NAMES[best.tonic],
      mode: best.mode,
      confidence,
      // The runner-up travels with the answer so a close call is visible
      // rather than hidden behind one confident-looking label. Its own margin
      // is over the *third* candidate, by the same definition.
      alternative: {
        tonic: NOTE_NAMES[second.tonic],
        mode: second.mode,
        confidence: Math.max(0, second.correlation - third.correlation),
      },
    },
    unmeasuredReason: null,
    pitchClasses: shares,
    distinctPitchClasses: distinct,
    voicedSeconds,
    method: profiles.name,
    ranked,
  };
}

/**
 * All 24 keys, best correlation first.
 *
 * The ordering is total: ties break on tonic, then on major before minor. The
 * list is built in a fixed order and `Array.prototype.sort` is stable in every
 * engine this runs in, so the tie-break states the intended order rather than
 * leaving it as a property a later refactor could lose silently — the same
 * reasoning, and the same wording, as `key.py`.
 */
export function rankedCandidates(
  shares: readonly PitchClassShare[],
  profiles: KeyProfileSet = DEFAULT_PROFILE_SET,
): KeyCandidate[] {
  const observed = shares.map((share) => share.percentageOfVoicedTime);
  const candidates: KeyCandidate[] = [];
  for (const mode of ["major", "minor"] as const) {
    for (let tonic = 0; tonic < SEMITONES; tonic++) {
      candidates.push({
        correlation: correlation(observed, rotated(profiles[mode], tonic)),
        tonic,
        mode,
      });
    }
  }
  candidates.sort(
    (a, b) =>
      b.correlation - a.correlation ||
      a.tonic - b.tonic ||
      (a.mode === "minor" ? 1 : 0) - (b.mode === "minor" ? 1 : 0),
  );
  return candidates;
}

/**
 * The profile expressed in absolute pitch classes for one tonic.
 *
 * `weights` is written relative to its own tonic, so the weight expected at
 * pitch class *i* for a key on *tonic* is the profile's *(i − tonic)*-th degree.
 */
function rotated(weights: readonly number[], tonic: number): number[] {
  const absolute: number[] = [];
  for (let index = 0; index < SEMITONES; index++) {
    absolute.push(weights[(((index - tonic) % SEMITONES) + SEMITONES) % SEMITONES]);
  }
  return absolute;
}

/**
 * Pearson correlation between two twelve-value profiles.
 *
 * Zero when either side has no variance at all: a perfectly flat profile is
 * equally unlike every key, and "no relationship" is the honest answer where a
 * division by zero is a crash. Defensive and unreachable through
 * {@link estimateKey}, for the reason `key.py` gives at length — a flat profile
 * that cleared the pitch-class gate is refused on confidence anyway.
 */
export function correlation(observed: readonly number[], expected: readonly number[]): number {
  const meanObserved = observed.reduce((sum, value) => sum + value, 0) / SEMITONES;
  const meanExpected = expected.reduce((sum, value) => sum + value, 0) / SEMITONES;

  let covariance = 0;
  let varianceObserved = 0;
  let varianceExpected = 0;
  for (let index = 0; index < SEMITONES; index++) {
    const o = observed[index] - meanObserved;
    const e = expected[index] - meanExpected;
    covariance += o * e;
    varianceObserved += o * o;
    varianceExpected += e * e;
  }

  const denominator = Math.sqrt(varianceObserved * varianceExpected);
  if (denominator <= 0) return 0;
  return covariance / denominator;
}

/** The correlation a named key scored, for a comparison against an incumbent. */
export function correlationOf(
  ranked: readonly KeyCandidate[],
  tonic: string,
  mode: KeyMode,
): number | null {
  const pitchClass = NOTE_NAMES.indexOf(tonic as (typeof NOTE_NAMES)[number]);
  if (pitchClass < 0) return null;
  const found = ranked.find(
    (candidate) => candidate.tonic === pitchClass && candidate.mode === mode,
  );
  return found ? found.correlation : null;
}
