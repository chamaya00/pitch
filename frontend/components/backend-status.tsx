"use client";

import { useBackendHealth } from "@/hooks/use-backend-health";

/**
 * Connection notice.
 *
 * Renders nothing while the API is reachable: a permanent "online" badge is
 * developer chrome, not product. It appears only when the API cannot be
 * reached, where it explains why uploading is about to fail.
 */
export function BackendStatus() {
  const health = useBackendHealth();

  if (health.status !== "offline") return null;

  return (
    <p
      role="status"
      className="mb-4 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-warning"
    >
      <span
        aria-hidden
        className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full bg-warning"
      />
      <span>
        VocalLens can&apos;t reach its analysis service right now, so uploads
        will fail. Please try again shortly.
      </span>
    </p>
  );
}
