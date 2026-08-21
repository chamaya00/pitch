"use client";

import { useEffect, useState } from "react";
import { getSongCompatibility } from "@/lib/api";
import type { SongCompatibility } from "@/types/api";

export type SongCompatibilityState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; result: SongCompatibility }
  | { status: "error"; message: string };

/**
 * One recording placed against one song reference.
 *
 * Deliberately not a runner, for the same reason `useComparison` is not: the
 * result is derived on the server from two stored values, so there is nothing
 * to poll and no background work to wait on. Re-issued only when the pair
 * changes, with the in-flight request aborted when it does — so clicking
 * through songs quickly cannot land an earlier answer under a later heading.
 *
 * A **refusal is not an error**. The server answers `200` with
 * `comparable: false` and a `recording_status`, which arrives here as `ready`;
 * the card renders the reason. `error` is reserved for a request that did not
 * complete — including the `404` a reference deleted in another tab produces.
 */
export function useSongCompatibility(
  recordingId: string,
  referenceId: string | null,
): SongCompatibilityState {
  const pair = `${recordingId} ${referenceId}`;
  // Stored *with the pair it belongs to*, so a result for a different pair
  // reads as loading during render rather than being cleared by an effect —
  // the technique `useComparison` documents at length.
  const [result, setResult] = useState<{
    pair: string;
    state: SongCompatibilityState;
  } | null>(null);

  useEffect(() => {
    if (referenceId === null) return;

    const controller = new AbortController();

    getSongCompatibility(recordingId, referenceId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setResult({ pair, state: { status: "ready", result: value } });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setResult({
          pair,
          state: {
            status: "error",
            message:
              error instanceof Error
                ? error.message
                : "This song could not be compared with the recording.",
          },
        });
      });

    return () => controller.abort();
  }, [recordingId, referenceId, pair]);

  if (referenceId === null) return { status: "idle" };
  return result !== null && result.pair === pair ? result.state : { status: "loading" };
}
