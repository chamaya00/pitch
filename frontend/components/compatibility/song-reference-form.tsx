"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  PITCH_CLASSES,
  referenceDraftProblem,
  referenceNoteOptions,
} from "@/lib/song-compatibility";
import { midiToNoteName } from "@/lib/pitch";
import type { KeyMode, SongReferenceInput } from "@/types/api";

interface SongReferenceFormProps {
  onSubmit: (input: SongReferenceInput) => void;
  saving: boolean;
}

const NOTE_OPTIONS = referenceNoteOptions();

/** C4 and C5: an octave in the middle of the picker, so neither end is a cliff. */
const DEFAULT_LOW = 60;
const DEFAULT_HIGH = 72;

/**
 * Describe a song by its range.
 *
 * **Every field here is an assertion, and the form says so before it is
 * filled.** Nothing is uploaded, nothing is decoded and nothing is measured;
 * the numbers that come out of this form are the numbers that went into it.
 * That is stated in the copy rather than buried in a tooltip, because it is the
 * one thing a reader has to understand to read the result correctly.
 *
 * The notes are `<select>`s over the names this project writes, which is what
 * makes the flats question disappear: the API refuses `Db4` rather than
 * rewriting it, and a picker cannot produce one. They are also keyboard-
 * operable, screen-reader-labelled and mobile-native for free — the reasoning
 * `TargetNotePicker` records.
 *
 * The key is optional and says why. The transposition arithmetic never needs
 * one; it buys naming the result "in B major" instead of only "down three
 * semitones".
 */
export function SongReferenceForm({ onSubmit, saving }: SongReferenceFormProps) {
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [lowestMidi, setLowestMidi] = useState(DEFAULT_LOW);
  const [highestMidi, setHighestMidi] = useState(DEFAULT_HIGH);
  const [tonic, setTonic] = useState("");
  const [mode, setMode] = useState<KeyMode>("major");
  const [touched, setTouched] = useState(false);

  const problem = referenceDraftProblem(title, lowestMidi, highestMidi);
  const lowestNote = midiToNoteName(lowestMidi);
  const highestNote = midiToNoteName(highestMidi);

  const submit = () => {
    setTouched(true);
    if (problem !== null || lowestNote === null || highestNote === null) return;
    onSubmit({
      title: title.trim(),
      artist: artist.trim() === "" ? null : artist.trim(),
      lowest_note: lowestNote,
      highest_note: highestNote,
      key: tonic === "" ? null : { tonic, mode },
    });
    setTitle("");
    setArtist("");
    setTonic("");
    setTouched(false);
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="reference-title" className="text-xs text-muted">
            Song
          </label>
          <input
            id="reference-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={200}
            aria-invalid={touched && problem?.field === "title"}
            aria-describedby={
              touched && problem?.field === "title" ? "reference-title-error" : undefined
            }
            className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-1.5 text-sm"
          />
          {touched && problem?.field === "title" && (
            <p id="reference-title-error" className="mt-1 text-xs text-danger">
              {problem.message}
            </p>
          )}
        </div>
        <div>
          <label htmlFor="reference-artist" className="text-xs text-muted">
            Artist <span className="text-muted">(optional)</span>
          </label>
          <input
            id="reference-artist"
            value={artist}
            onChange={(event) => setArtist(event.target.value)}
            maxLength={200}
            className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-1.5 text-sm"
          />
        </div>
      </div>

      <fieldset>
        <legend className="text-xs text-muted">The song&apos;s range</legend>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label htmlFor="reference-lowest" className="text-xs text-muted">
              Lowest
            </label>
            <select
              id="reference-lowest"
              value={lowestMidi}
              onChange={(event) => setLowestMidi(Number(event.target.value))}
              className="rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-sm"
            >
              {NOTE_OPTIONS.map((option) => (
                <option key={option.midi} value={option.midi}>
                  {option.note}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="reference-highest" className="text-xs text-muted">
              Highest
            </label>
            <select
              id="reference-highest"
              value={highestMidi}
              onChange={(event) => setHighestMidi(Number(event.target.value))}
              className="rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-sm"
            >
              {NOTE_OPTIONS.map((option) => (
                <option key={option.midi} value={option.midi}>
                  {option.note}
                </option>
              ))}
            </select>
          </div>
        </div>
        {touched && problem?.field === "range" && (
          <p role="alert" className="mt-2 text-xs text-danger">
            {problem.message}
          </p>
        )}
      </fieldset>

      <fieldset>
        <legend className="text-xs text-muted">
          Key <span className="text-muted">(optional)</span>
        </legend>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <select
            aria-label="Key of the song"
            value={tonic}
            onChange={(event) => setTonic(event.target.value)}
            className="rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-sm"
          >
            <option value="">Not given</option>
            {PITCH_CLASSES.map((pitchClass) => (
              <option key={pitchClass} value={pitchClass}>
                {pitchClass}
              </option>
            ))}
          </select>
          <select
            aria-label="Major or minor"
            value={mode}
            disabled={tonic === ""}
            onChange={(event) => setMode(event.target.value as KeyMode)}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm disabled:opacity-50"
          >
            <option value="major">major</option>
            <option value="minor">minor</option>
          </select>
          <p className="max-w-prose text-xs leading-relaxed text-muted">
            Only used to name the key a shift would land in. The shift itself
            never needs one.
          </p>
        </div>
      </fieldset>

      <Button type="submit" variant="secondary" disabled={saving}>
        {saving ? "Saving…" : "Save this song"}
      </Button>
    </form>
  );
}
