"use client";

import Link from "next/link";
import { Button, buttonClassName } from "@/components/ui/button";
import { presentRouteFailure } from "@/lib/error-presentation";

/**
 * The route-level error boundary.
 *
 * React unmounts the whole segment when a render throws, so before this file
 * existed a single bad render replaced the page with Next.js's default screen —
 * which in production is an unstyled "Application error: a client-side
 * exception has occurred", and in development is a stack trace. Neither is
 * something to show somebody who came here to look at their singing.
 *
 * **Deliberately thin.** It presents a failure and offers a way out. It does
 * not retry a request, inspect a status code, know anything about identity,
 * credentials, rate limits or recordings, and does not log. Expected API
 * failures — a 404 for a recording, a 429, a provider being down — are still
 * handled where they always were, by `lib/*-errors.ts` and the panel that made
 * the call, and they never reach this boundary. If they started to, that would
 * be a bug in the panel, not something to paper over here.
 *
 * **The error object is never rendered.** It is handed to
 * `presentRouteFailure`, which discards it and returns fixed copy. An exception
 * message can contain a stack trace, a filesystem path, a database DSN or a
 * provider key, and the safest way to guarantee none of that reaches the screen
 * is for the screen never to depend on it.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const failure = presentRouteFailure(error);

  return (
    <div className="mx-auto max-w-3xl px-6 py-20">
      <div
        role="alert"
        className="rounded-xl border border-danger/40 bg-danger/5 p-6 sm:p-8"
      >
        <h1 className="text-lg font-medium text-danger">{failure.title}</h1>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted">
          {failure.body}
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          {failure.retryLabel && (
            <Button onClick={reset}>{failure.retryLabel}</Button>
          )}
          {/* A link rather than a button: this is navigation, and it is also
              the escape hatch for the case where retrying keeps failing. */}
          <Link href="/" className={buttonClassName("secondary")}>
            {failure.homeLabel}
          </Link>
        </div>
      </div>
    </div>
  );
}
