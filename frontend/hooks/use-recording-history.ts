"use client";

import { useCallback, useEffect, useState } from "react";
import { getRecordingHistory } from "@/lib/api";
import type { RecordingHistory } from "@/types/api";

export type HistoryState =
  | { status: "loading" }
  | { status: "ready"; history: RecordingHistory }
  | { status: "error"; message: string };

/**
 * The caller's recording history, fetched once and refreshable.
 *
 * `reload` exists so the list can be brought up to date after an upload or an
 * analysis finishes, without a poller. Nothing here polls: history changes
 * because *this* page did something, and a background request every few
 * seconds would be traffic with no question behind it.
 *
 * Three states, never collapsed into two. An empty history and a failed request
 * look identical if "no items" is the only thing rendered, and one of those is
 * a problem the reader can act on.
 */
export function useRecordingHistory(limit?: number): {
  state: HistoryState;
  reload: () => void;
} {
  const [state, setState] = useState<HistoryState>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();

    getRecordingHistory(limit, controller.signal)
      .then((history) => {
        if (controller.signal.aborted) return;
        setState({ status: "ready", history });
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

  return { state, reload };
}
