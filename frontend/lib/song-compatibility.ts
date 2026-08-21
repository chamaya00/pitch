/**
 * Turning a compatibility result into rows and sentences a person can read.
 *
 * Three rules this module exists to enforce, each of which has a test.
 *
 * **A typed number is never dressed as a measured one.** Every range carries a
 * `source`, and {@link sourceLabel} turns it into words rather than into a
 * colour: "measured from this recording" against "as you typed it". A reader
 * who cannot tell the two apart is reading a claim this product does not make.
 *
 * **A count is not a distance.** "13 of the song's 13 notes" and "1 semitone
 * above your top note" are different quantities, and the sentences here keep
 * them apart in words as the payload keeps them apart in field names.
 *
 * **No sentence here adds up to a verdict.** There is no score, no grade and no
 * "you can sing this". A shift that fits is arithmetic; whether the result is
 * singable depends on register transitions, breath and technique, none of which
 * this system measures.
 *
 * Pure and dependency-free apart from the note conversion it shares with the
 * live display, so it can be tested with `node --test`.
 */

import { midiToNoteName } from "./pitch.ts";
import type {
  CompatibilityRecordingStatus,
  NoteRange,
  RangeFit,
  RangeSource,
  ReferenceKey,
  SongCompatibility,
  Transposition,
} from "../types/api";

/**
 * Notes offerable when describing a song: C1 to C7.
 *
 * Wider than `TARGET_MIDI_LOW`/`HIGH` in `live-practice.ts`, and deliberately
 * so — those bound the range the *detector searches*, which is a fact about a
 * measurement. This bounds what somebody may type about a song, which is not a
 * measurement at all, and a bass part below C2 is a real thing to describe.
 */
export const REFERENCE_MIDI_LOW = 24;
export const REFERENCE_MIDI_HIGH = 96;

export interface NoteOption {
  midi: number;
  note: string;
}

export function referenceNoteOptions(): NoteOption[] {
  const options: NoteOption[] = [];
  for (let midi = REFERENCE_MIDI_LOW; midi <= REFERENCE_MIDI_HIGH; midi += 1) {
    const note = midiToNoteName(midi);
    if (note !== null) options.push({ midi, note });
  }
  return options;
}

/** Tonics offerable for a key. Sharps only, as everywhere else here. */
export const PITCH_CLASSES = [
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

/* --- Provenance ------------------------------------------------------------ */

/**
 * Where a range's numbers came from, in words.
 *
 * In words rather than only in styling, because a colour or a border is not
 * something a screen reader announces and not something that survives a
 * screenshot. This is the distinction the whole input model rests on.
 */
export function sourceLabel(source: RangeSource): string {
  return source === "measured"
    ? "Measured from this recording"
    : "As you typed it — not measured";
}

/** `Bb3 — A5`, with the two notes as written. */
export function rangeLabel(range: NoteRange): string {
  return `${range.lowest_note} — ${range.highest_note}`;
}

export function keyLabel(key: ReferenceKey | null): string | null {
  return key === null ? null : `${key.tonic} ${key.mode}`;
}

/* --- The fit --------------------------------------------------------------- */

export interface FitRow {
  label: string;
  value: string;
  hint: string;
}

/**
 * The components of the fit, each with its unit in its label.
 *
 * Four rows and no fifth summarising them. A single figure would have to weight
 * the gap at the top against the gap at the bottom against the overlap in the
 * middle, and nothing measured anywhere sets those weights — so the components
 * are the answer rather than an ingredient of one.
 */
export function fitRows(fit: RangeFit): FitRow[] {
  return [
    {
      label: "Song notes inside your range",
      value: `${fit.overlap_note_count} of ${fit.reference_note_count}`,
      hint: "Counting every semitone the song spans, both ends included.",
    },
    {
      label: "Share of the song in reach",
      value: `${Math.round(fit.percent_of_reference_range)}%`,
      hint: "A share of the song's range, not of yours.",
    },
    {
      label: "Above your top note",
      value: semitoneGap(fit.semitones_above_top_note),
      hint: "How far the song's highest note sits above the highest you reached.",
    },
    {
      label: "Below your bottom note",
      value: semitoneGap(fit.semitones_below_bottom_note),
      hint: "How far the song's lowest note sits below the lowest you reached.",
    },
  ];
}

/** `Nothing` rather than `0 semitones`: a gap of nothing is not a distance. */
function semitoneGap(semitones: number): string {
  if (semitones === 0) return "Nothing";
  return `${semitones} ${semitones === 1 ? "semitone" : "semitones"}`;
}

/* --- The transposition ----------------------------------------------------- */

/**
 * The shift, in a sentence.
 *
 * Three outcomes and no fourth: it already fits, it fits after a shift, or
 * nothing fits. The last one names the shortfall and stops — there is no
 * best-effort suggestion, because a shift that does not fit is not a shift.
 */
export function transpositionSentence(shift: Transposition): string {
  if (!shift.possible) {
    const short = shift.shortfall_semitones ?? 0;
    return (
      `This song spans ${short} ${short === 1 ? "semitone" : "semitones"} more ` +
      "than this recording covered, so no shift brings all of it inside."
    );
  }
  if (shift.semitones === 0) {
    return "It already sits inside the range this recording covered. No shift needed.";
  }
  const semitones = shift.semitones ?? 0;
  const direction = semitones < 0 ? "down" : "up";
  const size = Math.abs(semitones);
  return (
    `Shifted ${direction} ${size} ${size === 1 ? "semitone" : "semitones"}, ` +
    "it would sit inside the range this recording covered."
  );
}

/** Where the song would land, or `null` when nothing fits. */
export function landingLabel(shift: Transposition): string | null {
  if (!shift.possible) return null;
  if (shift.resulting_lowest_note === null || shift.resulting_highest_note === null) {
    return null;
  }
  return `${shift.resulting_lowest_note} — ${shift.resulting_highest_note}`;
}

/**
 * How many other shifts would also work.
 *
 * Shown because "down 12" reads as *the* answer when it is only the smallest of
 * several. `null` when there is exactly one, since "1 workable shift" says
 * nothing the recommendation has not already said.
 */
export function windowLabel(shift: Transposition): string | null {
  const low = shift.lowest_workable_semitones;
  const high = shift.highest_workable_semitones;
  if (!shift.possible || low === null || high === null || low === high) return null;
  return `${low} to ${high} semitones all fit; this is the smallest move.`;
}

/* --- Refusals and caveats -------------------------------------------------- */

/**
 * Why no comparison was made, in words the reader can act on.
 *
 * Each of these is a **successful** response, not an error: a client renders
 * "nobody has measured this yet" and "measuring it failed" differently, and an
 * error code would collapse them into one.
 */
export function refusalMessage(status: CompatibilityRecordingStatus): string {
  switch (status) {
    case "analysis_missing":
      return "This recording has not been measured yet. Measure the audio first, and the comparison will have a range to work from.";
    case "analysis_in_progress":
      return "This recording is still being measured. The comparison needs the finished range.";
    case "analysis_failed":
      return "Measuring this recording did not finish, so there is no detected range to compare against.";
    case "insufficient_pitch_signal":
      return "No reliable pitch was found in this recording, so it has no detected range. A whisper, a noisy room or an instrumental take all read this way.";
    case "ready":
      // Unreachable from a refusal, and handled rather than asserted away.
      return "This recording has a detected range.";
  }
}

/**
 * A caveat, spelled out.
 *
 * The first three are on every result, because they describe the method rather
 * than the inputs. They are not fine print and are not conditional, which is
 * why they are rendered as sentences rather than as a footnote.
 */
export function caveatMessage(caveat: string): string {
  switch (caveat) {
    case "reference_range_asserted":
      return "The song's two notes are what you typed. Nothing here measured them, so every figure derived from them follows from a number this system never checked.";
    case "detected_range_is_this_recording":
      return "Your range is what this one recording contained — bounded by what you performed, by your microphone and by the room. It is not the limit of your voice.";
    case "not_a_statement_of_ability":
      return "Range overlap does not say whether you can sing a song. Where the melody sits, breath, register transitions and technique are not measured here.";
    case "little_pitched_signal":
      return "Very little of this recording carried a pitch, so the range it produced rests on few frames.";
    case "narrow_detected_range":
      return "The detected range is narrower than an octave, which usually says more about how short the take was than about your voice.";
    default:
      return caveat;
  }
}

/** The caveats a result carries, as sentences, in the order they arrived. */
export function caveatMessages(result: SongCompatibility): string[] {
  return result.caveats.map(caveatMessage);
}

/* --- Describing a song ----------------------------------------------------- */

export interface ReferenceDraftProblem {
  field: "title" | "range";
  message: string;
}

/**
 * What is wrong with a half-filled form, or `null`.
 *
 * The same two rules the server enforces, checked here so the reader is told
 * before a round trip rather than after one. It is **not** the authority: the
 * server validates independently, and a client that skipped this could not
 * store a bad reference.
 */
export function referenceDraftProblem(
  title: string,
  lowestMidi: number,
  highestMidi: number,
): ReferenceDraftProblem | null {
  if (title.trim() === "") {
    return { field: "title", message: "Give the song a name so you can find it again." };
  }
  if (highestMidi < lowestMidi) {
    return {
      field: "range",
      message: "The highest note is below the lowest one.",
    };
  }
  return null;
}

/** `A song — Somebody`, or just the title when there is no artist. */
export function referenceLabel(title: string, artist: string | null): string {
  return artist === null || artist.trim() === "" ? title : `${title} — ${artist}`;
}
