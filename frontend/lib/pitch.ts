/**
 * Musical conversions for the live pitch display.
 *
 * Deterministic arithmetic, no audio and no browser APIs, so it can be tested
 * with `node --test`. Everything is anchored to A4 = 440 Hz and MIDI note 69,
 * which is the equal-temperament reference the rest of the feature assumes.
 *
 * Every entry point rejects input that cannot describe a pitch — zero, a
 * negative, `NaN`, `Infinity`. A frequency that cannot exist must not become a
 * note name: silence would otherwise render as a confident C-something, which
 * is exactly the failure this feature has to avoid.
 */

export const A4_FREQUENCY = 440;
export const A4_MIDI = 69;
const SEMITONES_PER_OCTAVE = 12;
const CENTS_PER_SEMITONE = 100;

/** Sharps only. Enharmonic spelling needs a key, which live audio does not give us. */
export const NOTE_NAMES = [
  "C",
  "C#",
  "D",
  "D#",
  "E",
  "F",
  "F#",
  "G",
  "G#",
  "A",
  "A#",
  "B",
] as const;

/** Frequencies outside this range are not a sung or spoken pitch. */
export const MIN_FREQUENCY = 20;
export const MAX_FREQUENCY = 5000;

function isUsableFrequency(frequency: unknown): frequency is number {
  return (
    typeof frequency === "number" &&
    Number.isFinite(frequency) &&
    frequency >= MIN_FREQUENCY &&
    frequency <= MAX_FREQUENCY
  );
}

/**
 * Exact (fractional) MIDI number for a frequency, or `null` if there isn't one.
 *
 * Fractional on purpose: the whole point of the cents readout is the distance
 * between the sung pitch and the nearest note, and rounding here would throw
 * that away before it could be measured.
 */
export function frequencyToMidi(frequency: number | null): number | null {
  if (!isUsableFrequency(frequency)) return null;
  return (
    A4_MIDI +
    SEMITONES_PER_OCTAVE * Math.log2(frequency / A4_FREQUENCY)
  );
}

export function midiToFrequency(midi: number | null): number | null {
  if (typeof midi !== "number" || !Number.isFinite(midi)) return null;
  return A4_FREQUENCY * 2 ** ((midi - A4_MIDI) / SEMITONES_PER_OCTAVE);
}

/** Scientific pitch notation for a MIDI number, e.g. 69 → `A4`. */
export function midiToNoteName(midi: number | null): string | null {
  if (typeof midi !== "number" || !Number.isFinite(midi)) return null;
  const rounded = Math.round(midi);
  const name = NOTE_NAMES[((rounded % 12) + 12) % 12];
  const octave = Math.floor(rounded / 12) - 1;
  return `${name}${octave}`;
}

/**
 * How far a frequency sits from the nearest equal-tempered note, in cents.
 *
 * Always within ±50: beyond that the *next* note is nearer, and quoting a
 * larger deviation would describe the wrong note.
 */
export function centsFromNearestNote(frequency: number | null): number | null {
  const midi = frequencyToMidi(frequency);
  if (midi === null) return null;
  return (midi - Math.round(midi)) * CENTS_PER_SEMITONE;
}

export interface PitchReading {
  frequency: number;
  midi: number;
  note: string;
  cents: number;
}

/** Everything the display needs about one frequency, or `null` if unusable. */
export function describeFrequency(
  frequency: number | null,
): PitchReading | null {
  const midi = frequencyToMidi(frequency);
  if (midi === null || frequency === null) return null;

  const note = midiToNoteName(midi);
  const cents = centsFromNearestNote(frequency);
  if (note === null || cents === null) return null;

  return { frequency, midi, note, cents };
}

/** Formats cents the way a tuner does: signed, whole numbers, `0` unsigned. */
export function formatCents(cents: number | null): string {
  if (cents === null || !Number.isFinite(cents)) return "—";
  const rounded = Math.round(cents);
  if (rounded === 0) return "0 cents";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} cents`;
}

export function formatFrequency(frequency: number | null): string {
  if (frequency === null || !Number.isFinite(frequency)) return "—";
  return `${frequency.toFixed(1)} Hz`;
}
