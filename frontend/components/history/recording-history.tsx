"use client";

import { useCallback, useState } from "react";
import { ComparisonPanel } from "@/components/comparison/comparison-panel";
import { HistoryRow } from "@/components/history/history-row";
import { Button } from "@/components/ui/button";
import { useRecordingHistory } from "@/hooks/use-recording-history";
import { historyAnnouncement } from "@/lib/history";

/** Two recordings, no more: a comparison of three is a table nobody can read. */
const COMPARE_LIMIT = 2;

/**
 * The recordings this browser has uploaded.
 *
 * Whose they are is decided on the server from an anonymous identifier this
 * browser stores; there is no account, no password and no recovery, and the
 * footnote says exactly that rather than implying otherwise. Somebody who
 * clears their site data or opens VocalLens elsewhere gets a new identity and
 * an empty list — which is a real limitation, not a bug, and is better stated
 * than discovered.
 *
 * Four states, all rendered distinctly. "Loading", "you have none" and "we
 * could not fetch them" are three different things, and only the last is
 * something the reader can do anything about.
 *
 * **What is on screen is not necessarily everything.** This section used to say
 * "Everything you have uploaded from this browser" while rendering one page of
 * it; measured with 137 recordings, it showed 50 and announced "50 recordings".
 * The list now says how much of the history it is showing, and the rest is one
 * button away — see Step 10.13.
 */
export function RecordingHistory() {
  const { state, reload, loadMore, loadingMore, moreError } =
    useRecordingHistory();
  /** The recordings ticked for comparison, in the order they were ticked. */
  const [selected, setSelected] = useState<string[]>([]);
  /** The pair actually being compared. Separate from `selected` so changing a
   *  tick does not silently re-run the comparison under the reader. */
  const [comparing, setComparing] = useState<[string, string] | null>(null);

  const toggle = useCallback((recordingId: string) => {
    setSelected((current) =>
      current.includes(recordingId)
        ? current.filter((id) => id !== recordingId)
        : current.length < COMPARE_LIMIT
          ? [...current, recordingId]
          : current,
    );
  }, []);

  const canCompare = selected.length === COMPARE_LIMIT;

  return (
    <section
      aria-labelledby="history-heading"
      className="border-t border-border pt-10"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="history-heading" className="text-sm font-medium">
            Your recordings
          </h2>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            What you have uploaded from this browser, newest first. Open one to
            see its transcript and measurements.
          </p>
        </div>
        {state.status === "ready" && state.history.items.length > 0 && (
          <Button variant="secondary" onClick={reload}>
            Refresh
          </Button>
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement(state, loadingMore)}
      </p>

      {state.status === "loading" && (
        <p className="mt-6 text-sm text-muted">Loading your recordings…</p>
      )}

      {state.status === "error" && (
        <div
          role="alert"
          className="mt-6 rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">
            Couldn&apos;t load your recordings
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
          <div className="mt-4">
            <Button variant="secondary" onClick={reload}>
              Try again
            </Button>
          </div>
        </div>
      )}

      {state.status === "ready" && state.history.items.length === 0 && (
        <p className="mt-6 max-w-prose text-sm leading-relaxed text-muted">
          Nothing here yet. Recordings appear once you upload one — a take you
          record but never submit stays in your browser and is not listed.
        </p>
      )}

      {state.status === "ready" && state.history.items.length > 1 && (
        <div className="mt-6 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-5 py-4">
          <p className="text-sm text-muted">
            {selected.length === 0
              ? "Tick two recordings to compare their measurements."
              : selected.length === 1
                ? "Tick one more recording to compare."
                : "Two recordings selected."}
          </p>
          <div className="ml-auto flex flex-wrap gap-2">
            {selected.length > 0 && (
              <Button
                variant="secondary"
                onClick={() => {
                  setSelected([]);
                  setComparing(null);
                }}
              >
                Clear selection
              </Button>
            )}
            <Button
              disabled={!canCompare}
              onClick={() => canCompare && setComparing([selected[0], selected[1]])}
            >
              Compare recordings
            </Button>
          </div>
        </div>
      )}

      {comparing !== null && (
        <div className="mt-4">
          <ComparisonPanel
            // A new pair gets a new panel, so no comparison state survives from
            // the previous one.
            key={comparing.join("-")}
            leftId={comparing[0]}
            rightId={comparing[1]}
            onClose={() => setComparing(null)}
          />
        </div>
      )}

      {state.status === "ready" && state.history.items.length > 0 && (
        <ul className="fade-in mt-6 space-y-3">
          {state.history.items.map((item) => (
            <HistoryRow
              key={item.recording.recording_id}
              item={item}
              selected={selected.includes(item.recording.recording_id)}
              selectable={selected.length < COMPARE_LIMIT}
              onToggleSelected={toggle}
            />
          ))}
        </ul>
      )}

      {state.status === "ready" && state.history.nextCursor !== null && (
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            onClick={loadMore}
            disabled={loadingMore}
            aria-describedby="history-more-note"
          >
            {loadingMore ? "Loading…" : "Show older recordings"}
          </Button>
          <p id="history-more-note" className="text-xs text-muted">
            {state.history.items.length} shown so far — there are older ones.
          </p>
        </div>
      )}

      {/* A failed "show older" must not take the loaded list away with it. */}
      {moreError !== null && (
        <p role="alert" className="mt-3 text-xs text-danger">
          Older recordings couldn&apos;t be loaded. {moreError}
        </p>
      )}

      <p className="mt-6 max-w-prose text-xs leading-relaxed text-muted">
        Your recordings are linked to an anonymous identifier stored in this
        browser — not to an account. There is no password and no way to recover
        it: clearing your site data, or opening VocalLens in another browser,
        starts a new empty history.
      </p>
    </section>
  );
}

function announcement(
  state: ReturnType<typeof useRecordingHistory>["state"],
  loadingMore: boolean,
): string {
  switch (state.status) {
    case "loading":
      return "Loading your recordings.";
    case "error":
      return `Your recordings could not be loaded. ${state.message}`;
    case "ready":
      return loadingMore
        ? "Loading older recordings."
        : historyAnnouncement(state.history);
  }
}
