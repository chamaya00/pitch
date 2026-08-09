"use client";

import { useBackendHealth } from "@/hooks/use-backend-health";

const DOT = "inline-block h-2 w-2 rounded-full";

/**
 * Small connectivity indicator. In Phase 0 it is the only live link between the
 * frontend and the API, and it doubles as a smoke test during development.
 */
export function BackendStatus() {
  const health = useBackendHealth();

  if (health.status === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted">
        <span className={`${DOT} animate-pulse bg-muted`} aria-hidden />
        Checking API…
      </p>
    );
  }

  if (health.status === "offline") {
    return (
      <p className="flex items-center gap-2 text-sm text-danger" role="status">
        <span className={`${DOT} bg-danger`} aria-hidden />
        API unavailable — {health.message}
      </p>
    );
  }

  return (
    <p className="flex items-center gap-2 text-sm text-muted" role="status">
      <span className={`${DOT} bg-positive`} aria-hidden />
      API online · v{health.data.version} · {health.data.environment}
    </p>
  );
}
