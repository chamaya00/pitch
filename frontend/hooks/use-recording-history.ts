"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getRecordingHistory } from "@/lib/api";
import { appendPage, firstPage, type LoadedHistory } from "@/lib/history";

export type HistoryState =
  | { status: "loading" }
  | { status: "ready"; history: LoadedHistory }
  | { status: "error"; message: string };

/**
 * The caller's recording history, a page at a time.
 *
 * `reload` exists so the list can be brought up to date after an upload or an
 * analysis finishes, without a poller. Nothing here polls: history changes
 * because *this* page did something, and a background request every few seconds
 * would be traffic with no question behind it.
 *
 * Three states, never collapsed into two. An empty history and a failed request
 * look identical if "no items" is the only thing rendered, and one of those is
 * a problem the reader can act on.
 *
 * `loadMore` appends the next page and is the reason Step 10.13 exists: before
 * it, everything past the first page was unreachable from the browser. Two
 * things about it are deliberate.
 *
 * **Loading more never destroys what is on screen.** A failed "show older"
 * leaves the loaded recordings where they are and reports the failure beside
 * them, rather than replacing a working list with an error.
 *
 * **A request in flight blocks another.** Not for throughput — because two
 * clicks would fetch the same cursor twice, and the second answer would arrive
 * as a repeat of the first.
 */
export function useRecordingHistory(limit?: number): {
  state: HistoryState;
  reload: () => void;
  loadMore: () => void;
  loadingMore: boolean;
  moreError: string | null;
} {
  const [state, setState] = useState<HistoryState>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  /** Read inside `loadMore` without making it a dependency of every render. */
  const inFlight = useRef(false);
  /** The state as it stands now, for `loadMore` to read without re-creating itself. */
  const latest = useRef<HistoryState>(state);
  useEffect(() => {
    latest.current = state;
  }, [state]);

  const reload = useCallback(() => {
    setMoreError(null);
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    getRecordingHistory(limit, null, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setState({ status: "ready", history: firstPage(page) });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          message:
            error instanceof Error
              ? error.message
              : "Your recordings could not be loaded.",
        });
      });

    return () => controller.abort();
  }, [limit, nonce]);

  const loadMore = useCallback(() => {
    // Read through a ref rather than starting the request inside a `setState`
    // updater: React may run an updater more than once, and a fetch started in
    // there would be sent twice.
    const current = latest.current;
    if (inFlight.current) return;
    if (current.status !== "ready" || current.history.nextCursor === null) return;

    inFlight.current = true;
    setLoadingMore(true);
    setMoreError(null);

    getRecordingHistory(limit, current.history.nextCursor)
      .then((page) => {
        setState((existing) =>
          existing.status === "ready"
            ? { status: "ready", history: appendPage(existing.history, page) }
            : existing,
        );
      })
      .catch((error: unknown) => {
        setMoreError(
          error instanceof Error
            ? error.message
            : "Older recordings could not be loaded.",
        );
      })
      .finally(() => {
        inFlight.current = false;
        setLoadingMore(false);
      });
  }, [limit]);

  return { state, reload, loadMore, loadingMore, moreError };
}
