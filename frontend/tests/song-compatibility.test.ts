/**
 * Song-compatibility wording.
 *
 * The rules under test are the ones that turn two ranges into a claim:
 *
 * - a typed number is never presented as a measured one, and the difference is
 *   in **words** rather than in styling;
 * - a count of notes is never worded as a distance in semitones;
 * - a gap of nothing is not rendered as `0 semitones`;
 * - a song that does not fit gets a shortfall and no suggestion;
 * - the three standing caveats have real sentences, because they appear on
 *   every result and are not fine print;
 * - nothing here reads as a verdict about whether somebody can sing a song.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PITCH_CLASSES,
  REFERENCE_MIDI_HIGH,
  REFERENCE_MIDI_LOW,
  caveatMessage,
  caveatMessages,
  fitRows,
  keyLabel,
  landingLabel,
  rangeLabel,
  referenceDraftProblem,
  referenceLabel,
  referenceNoteOptions,
  refusalMessage,
  sourceLabel,
  transpositionNote,
  transpositionSentence,
  windowLabel,
} from "../lib/song-compatibility.ts";
import type {
  CompatibilityRecordingStatus,
  RangeFit,
  SongCompatibility,
  Transposition,
} from "../types/api.ts";

function fit(overrides: Partial<RangeFit> = {}): RangeFit {
  return {
    overlap_note_count: 13,
    reference_note_count: 13,
    percent_of_reference_range: 100,
    semitones_above_top_note: 0,
    semitones_below_bottom_note: 0,
    ...overrides,
  };
}

function shift(overrides: Partial<Transposition> = {}): Transposition {
  return {
    possible: true,
    semitones: 0,
    lowest_workable_semitones: 0,
    highest_workable_semitones: 0,
    shortfall_semitones: null,
    resulting_lowest_note: "C4",
    resulting_highest_note: "C5",
    resulting_key: null,
    ...overrides,
  };
}

// --- Provenance -------------------------------------------------------------

test("the two kinds of number are told apart in words, not in styling", () => {
  assert.match(sourceLabel("measured"), /Measured from this recording/);
  assert.match(sourceLabel("asserted"), /not measured/);
  assert.notEqual(sourceLabel("measured"), sourceLabel("asserted"));
});

test("a range reads as the two notes it was given", () => {
  assert.equal(
    rangeLabel({
      lowest_note: "G2",
      highest_note: "C5",
      semitone_span: 29,
      source: "measured",
    }),
    "G2 — C5",
  );
});

test("a key reads as its tonic and mode, and no key reads as nothing", () => {
  assert.equal(keyLabel({ tonic: "C#", mode: "minor" }), "C# minor");
  assert.equal(keyLabel(null), null);
});

// --- The fit ----------------------------------------------------------------

test("the fit is four components and no fifth summarising them", () => {
  const rows = fitRows(fit());
  assert.equal(rows.length, 4);
  for (const row of rows) {
    assert.doesNotMatch(row.label.toLowerCase(), /score|rating|grade|overall/);
  }
});

test("a count of notes is worded as a count, not as a distance", () => {
  const rows = fitRows(fit({ overlap_note_count: 7, reference_note_count: 13 }));
  const overlap = rows.find((row) => row.label === "Song notes inside your range");
  assert.equal(overlap?.value, "7 of 13");
  assert.doesNotMatch(overlap?.value ?? "", /semitone/);
});

test("the share says it is a share of the song, not of the singer", () => {
  const rows = fitRows(fit({ percent_of_reference_range: 52 }));
  const share = rows.find((row) => row.label === "Share of the song in reach");
  assert.equal(share?.value, "52%");
  assert.match(share?.hint ?? "", /share of the song's range/);
});

test("a gap of nothing is not rendered as a zero", () => {
  const rows = fitRows(fit());
  const above = rows.find((row) => row.label === "Above your top note");
  assert.equal(above?.value, "Nothing");
});

test("a gap of one semitone is singular and a gap of two is not", () => {
  const one = fitRows(fit({ semitones_above_top_note: 1 }));
  const two = fitRows(fit({ semitones_above_top_note: 2 }));
  assert.equal(one.find((r) => r.label === "Above your top note")?.value, "1 semitone");
  assert.equal(two.find((r) => r.label === "Above your top note")?.value, "2 semitones");
});

// --- The transposition ------------------------------------------------------

test("a song that already fits is not told to move", () => {
  assert.match(transpositionSentence(shift()), /No shift needed/);
});

test("a downward shift reads as down, and an upward one as up", () => {
  assert.match(transpositionSentence(shift({ semitones: -3 })), /down 3 semitones/);
  assert.match(transpositionSentence(shift({ semitones: 5 })), /up 5 semitones/);
});

test("a shift of one semitone is singular", () => {
  assert.match(transpositionSentence(shift({ semitones: -1 })), /down 1 semitone,/);
});

test("a song that does not fit names the shortfall and suggests nothing", () => {
  const sentence = transpositionSentence(
    shift({
      possible: false,
      semitones: null,
      lowest_workable_semitones: null,
      highest_workable_semitones: null,
      shortfall_semitones: 4,
      resulting_lowest_note: null,
      resulting_highest_note: null,
    }),
  );
  assert.match(sentence, /4 semitones more/);
  assert.doesNotMatch(sentence, /shift(ed)? (down|up)/);
});

test("nothing lands anywhere when no shift fits", () => {
  assert.equal(
    landingLabel(
      shift({
        possible: false,
        semitones: null,
        resulting_lowest_note: null,
        resulting_highest_note: null,
      }),
    ),
    null,
  );
});

test("a landing reads as the range the song would then sit in", () => {
  assert.equal(
    landingLabel(shift({ resulting_lowest_note: "E3", resulting_highest_note: "A4" })),
    "E3 — A4",
  );
});

test("one workable shift says nothing extra; several say so", () => {
  assert.equal(windowLabel(shift()), null);
  assert.match(
    windowLabel(
      shift({ lowest_workable_semitones: -24, highest_workable_semitones: 0 }),
    ) ?? "",
    /-24 to 0 semitones all fit/,
  );
});

// --- Refusals ---------------------------------------------------------------

test("each refusal names a different thing the reader can do about it", () => {
  const statuses: CompatibilityRecordingStatus[] = [
    "analysis_missing",
    "analysis_in_progress",
    "analysis_failed",
    "insufficient_pitch_signal",
  ];
  const messages = statuses.map(refusalMessage);
  assert.equal(new Set(messages).size, statuses.length);
  for (const message of messages) assert.ok(message.length > 20);
});

test("no reliable pitch is explained rather than blamed", () => {
  const message = refusalMessage("insufficient_pitch_signal");
  assert.match(message, /whisper|noisy room|instrumental/);
  assert.doesNotMatch(message.toLowerCase(), /wrong|bad|poor/);
});

// --- Caveats ----------------------------------------------------------------

test("the three standing caveats are real sentences", () => {
  for (const caveat of [
    "reference_range_asserted",
    "detected_range_is_this_recording",
    "not_a_statement_of_ability",
  ]) {
    const message = caveatMessage(caveat);
    assert.notEqual(message, caveat);
    assert.ok(message.length > 40, caveat);
  }
});

test("the caveat about ability refuses the claim rather than hedging it", () => {
  assert.match(caveatMessage("not_a_statement_of_ability"), /does not say whether/);
});

test("a caveat nobody has wording for falls back to its own name", () => {
  assert.equal(caveatMessage("something_new"), "something_new");
});

test("a result's caveats come through in the order they arrived", () => {
  const result = {
    comparable: true,
    recording_status: "ready",
    recording_range: null,
    reference_range: null,
    reference: null,
    fit: null,
    transposition: null,
    caveats: ["not_a_statement_of_ability", "reference_range_asserted"],
  } as unknown as SongCompatibility;
  const [first, second] = caveatMessages(result);
  assert.equal(first, caveatMessage("not_a_statement_of_ability"));
  assert.equal(second, caveatMessage("reference_range_asserted"));
});

// --- Describing a song ------------------------------------------------------

test("the note picker offers only names this project writes", () => {
  const options = referenceNoteOptions();
  assert.equal(options[0]?.midi, REFERENCE_MIDI_LOW);
  assert.equal(options[options.length - 1]?.midi, REFERENCE_MIDI_HIGH);
  for (const option of options) assert.doesNotMatch(option.note, /b/);
});

test("the picker is wider than the practice target range", () => {
  // It bounds what somebody may *type* about a song, not what the detector
  // searches — a bass part below C2 is a real thing to describe.
  assert.ok(REFERENCE_MIDI_LOW < 36);
  assert.ok(REFERENCE_MIDI_HIGH > 84);
});

test("every tonic offered is spelled with a sharp or nothing", () => {
  assert.equal(PITCH_CLASSES.length, 12);
  for (const pitchClass of PITCH_CLASSES) assert.match(pitchClass, /^[A-G]#?$/);
});

test("a song with no name is refused before a round trip", () => {
  assert.equal(referenceDraftProblem("  ", 60, 72)?.field, "title");
});

test("a range the wrong way round is refused before a round trip", () => {
  assert.equal(referenceDraftProblem("A song", 72, 60)?.field, "range");
});

test("a range of one note is not a problem", () => {
  assert.equal(referenceDraftProblem("A song", 69, 69), null);
});

test("a song with no artist is labelled by its title alone", () => {
  assert.equal(referenceLabel("A song", null), "A song");
  assert.equal(referenceLabel("A song", "  "), "A song");
  assert.equal(referenceLabel("A song", "Somebody"), "A song — Somebody");
});

test("the note under a transposition does not claim a shift exists when none does", () => {
  // It was rendered underneath "no shift brings all of it inside" until the
  // card was read on screen rather than in a test.
  const fits = transpositionNote(shift());
  const does_not = transpositionNote(
    shift({ possible: false, semitones: null, shortfall_semitones: 4,
            lowest_workable_semitones: null, highest_workable_semitones: null,
            resulting_lowest_note: null, resulting_highest_note: null }),
  );
  assert.match(fits, /says a shift exists/);
  assert.doesNotMatch(does_not, /a shift exists/);
  // Both still refuse the same claim.
  assert.match(fits, /not that the result is singable/);
  assert.match(does_not, /not a statement about whether you could sing/);
});
