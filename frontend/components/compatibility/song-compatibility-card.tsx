"use client";

import { useState } from "react";
import { CompatibilityResult } from "@/components/compatibility/compatibility-result";
import { SongReferenceForm } from "@/components/compatibility/song-reference-form";
import { Button } from "@/components/ui/button";
import { useSongCompatibility } from "@/hooks/use-song-compatibility";
import { useSongReferences } from "@/hooks/use-song-references";
import { rangeLabel, referenceLabel } from "@/lib/song-compatibility";
import type { SongReferenceInput } from "@/types/api";

interface SongCompatibilityCardProps {
  recordingId: string;
}

/**
 * Whether a song fits the range this recording contained.
 *
 * Placed below the audio-analysis results, and derived from a measurement
 * already on the screen — the same placement and the same reasoning the musical
 * key card took relative to the note breakdown.
 *
 * **Not a dashboard.** The comparison being conceptually complex is a reason to
 * show fewer numbers, better labelled, rather than a reason to build a surface
 * for them.
 *
 * The one thing this card exists to keep visible: **one side was measured and
 * the other was typed.** Nothing here uploads, decodes or analyses a song. The
 * numbers a reader gets out are an arithmetic consequence of the numbers they
 * put in, and every state below says so — the empty one before they type
 * anything, and the result afterwards.
 */
export function SongCompatibilityCard({ recordingId }: SongCompatibilityCardProps) {
  const { references, loading, error, saving, add, remove } = useSongReferences();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const state = useSongCompatibility(recordingId, selectedId);

  const onSubmit = (input: SongReferenceInput) => {
    void add(input).then((created) => {
      if (created === null) return;
      setSelectedId(created.reference_id);
      setAdding(false);
    });
  };

  const onRemove = (referenceId: string) => {
    void remove(referenceId).then(() => {
      // Selecting a song that has just been deleted would ask the server about
      // an id it no longer has, which is a 404 rather than a result.
      setSelectedId((current) => (current === referenceId ? null : current));
    });
  };

  return (
    <section
      aria-labelledby="song-compatibility-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <div className="border-b border-border px-5 py-4">
        <h4 id="song-compatibility-heading" className="text-sm font-medium">
          Does a song fit this range?
        </h4>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          Describe a song by its lowest and highest note, and this places it
          against the range this recording contained. <strong className="font-medium">
          The song&apos;s numbers are the ones you type</strong> — nothing here
          measures them, and no audio of the song is involved at any point.
        </p>
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement(loading, saving, selectedId, state.status)}
      </p>

      <div className="border-b border-border px-5 py-4">
        {loading ? (
          <p className="text-sm text-muted">Loading your saved songs…</p>
        ) : (
          <>
            {references.length > 0 && (
              <ul className="space-y-1">
                {references.map((reference) => {
                  const selected = reference.reference_id === selectedId;
                  return (
                    <li
                      key={reference.reference_id}
                      className="flex flex-wrap items-center justify-between gap-2"
                    >
                      <button
                        type="button"
                        aria-pressed={selected}
                        onClick={() =>
                          setSelectedId(selected ? null : reference.reference_id)
                        }
                        className={`rounded-md px-2 py-1 text-left text-sm ${
                          selected
                            ? "bg-surface-raised font-medium"
                            : "text-muted hover:bg-surface-raised"
                        }`}
                      >
                        {referenceLabel(reference.title, reference.artist)}{" "}
                        <span className="font-mono text-xs">
                          {rangeLabel({
                            lowest_note: reference.lowest_note,
                            highest_note: reference.highest_note,
                            semitone_span: 0,
                            source: reference.source,
                          })}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove(reference.reference_id)}
                        disabled={saving}
                        className="rounded-md px-2 py-1 text-xs text-muted hover:text-danger disabled:opacity-50"
                      >
                        Remove
                        <span className="sr-only">
                          {" "}
                          {referenceLabel(reference.title, reference.artist)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {references.length === 0 && !adding && (
              <p className="max-w-prose text-sm leading-relaxed text-muted">
                No songs described yet. Add one to see how much of it falls
                inside the range this recording covered, and what shift would
                bring the rest in.
              </p>
            )}

            <div className="mt-3">
              {adding ? (
                <div className="rounded-lg border border-border p-4">
                  <SongReferenceForm onSubmit={onSubmit} saving={saving} />
                  <div className="mt-3">
                    <Button variant="secondary" onClick={() => setAdding(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <Button variant="secondary" onClick={() => setAdding(true)}>
                  Describe a song
                </Button>
              )}
            </div>

            {error !== null && (
              <p role="alert" className="mt-3 text-sm text-danger">
                {error}
              </p>
            )}
          </>
        )}
      </div>

      {state.status === "idle" && references.length > 0 && (
        <p className="px-5 py-4 text-sm text-muted">
          Choose a song above to compare it with this recording.
        </p>
      )}
      {state.status === "loading" && (
        <p className="px-5 py-4 text-sm text-muted">Comparing…</p>
      )}
      {state.status === "error" && (
        <p role="alert" className="px-5 py-4 text-sm text-danger">
          {state.message}
        </p>
      )}
      {state.status === "ready" && <CompatibilityResult result={state.result} />}
    </section>
  );
}

/**
 * One polite live region for the whole card.
 *
 * A single region for the flow rather than one per state, so a screen reader
 * hears what changed without the DOM churn of several — the pattern
 * `CapturePanel` established.
 */
function announcement(
  loading: boolean,
  saving: boolean,
  selectedId: string | null,
  status: string,
): string {
  if (loading) return "Loading your saved songs.";
  if (saving) return "Saving.";
  if (selectedId === null) return "No song chosen.";
  if (status === "loading") return "Comparing the song with this recording.";
  if (status === "error") return "The comparison could not be made.";
  return "The comparison is ready.";
}
