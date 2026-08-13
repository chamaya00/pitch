"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import type { HealthResponse } from "@/types/api";

export type BackendHealthState =
  | { status: "loading" }
  | { status: "online"; data: HealthResponse }
  | { status: "offline"; message: string };

/** Probe the API health endpoint once from the browser. */
export function useBackendHealth(): BackendHealthState {
  const [state, setState] = useState<BackendHealthState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    getHealth(controller.signal)
      .then((data) => setState({ status: "online", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "offline",
          message:
            error instanceof Error ? error.message : "Unknown error.",
        });
      });

    return () => controller.abort();
  }, []);

  return state;
}
