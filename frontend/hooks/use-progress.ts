"use client";

import { useEffect, useState } from "react";
import { getProgressHistory } from "@/lib/api";
import type { ProgressHistory } from "@/types/api";

export type ProgressState =
  | { status: "loading" }
  | { status: "ready"; history: ProgressHistory }
  | { status: "error"; message: string };

/**
 * Fetch the caller's measurements over time.
 *
 * One read, no polling: history changes because this browser recorded and
 * measured something, not on its own. The metric selector switches between
 * series already in this response rather than re-fetching, so choosing a
 * different measurement costs nothing and cannot land results out of order.
 *
 * An **empty** history is a successful `ready`, not an error. The two look the
 * same only if "no points" is all that is rendered, and one of them is a
 * problem the reader can act on.
 */
export function useProgress(limit?: number): {
  state: ProgressState;
  reload: () => void;
} {
  const [state, setState] = useState<ProgressState>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    getProgressHistory(limit, controller.signal)
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
              : "Your measurements could not be loaded.",
        });
      });

    return () => controller.abort();
  }, [limit, nonce]);

  return { state, reload: () => setNonce((value) => value + 1) };
}
