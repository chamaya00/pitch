"use client";

import { useEffect, useState } from "react";
import { getPublicConfig } from "@/lib/api";
import type { PublicConfig } from "@/types/api";

/**
 * Fetch the server's upload limits.
 *
 * Returns `null` until they arrive, and stays `null` if the API is unreachable.
 * Callers must cope with that rather than substituting guessed numbers: the
 * server enforces the real limits either way, and stating a wrong one is worse
 * than stating none.
 */
export function usePublicConfig(): PublicConfig | null {
  const [config, setConfig] = useState<PublicConfig | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getPublicConfig(controller.signal)
      .then(setConfig)
      .catch(() => {
        /* Limits are a nicety; the upload flow works without them. */
      });
    return () => controller.abort();
  }, []);

  return config;
}
