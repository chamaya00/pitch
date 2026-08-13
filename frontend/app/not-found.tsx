import type { Metadata } from "next";
import Link from "next/link";
import { buttonClassName } from "@/components/ui/button";
import { presentNotFound } from "@/lib/error-presentation";

export const metadata: Metadata = {
  title: "Page not found — VocalLens",
};

/**
 * The App Router's not-found state.
 *
 * Kept visually distinct from `error.tsx` on purpose, because they are
 * different situations and conflating them is unkind: a broken page is
 * something we did and might recover from, while a wrong address is neither
 * broken nor recoverable by retrying. So this one is not styled as an alert,
 * carries no danger colour, and offers no "try again" — re-requesting the same
 * missing address would fail identically, and offering it would be pretending.
 *
 * It renders inside the root layout, so the header, footer, fonts and theme all
 * come along and the page still looks like VocalLens rather than like a dead
 * end. The address itself is not echoed: repeating an arbitrary URL back into
 * the page is how a 404 becomes a reflected-content problem, and it tells the
 * reader nothing they did not just type.
 */
export default function NotFound() {
  const failure = presentNotFound();

  return (
    <div className="mx-auto max-w-3xl px-6 py-20">
      <div className="rounded-xl border border-border bg-surface p-6 sm:p-8">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          404
        </p>
        <h1 className="mt-2 text-lg font-medium">{failure.title}</h1>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted">
          {failure.body}
        </p>
        <div className="mt-6">
          <Link href="/" className={buttonClassName("secondary")}>
            {failure.homeLabel}
          </Link>
        </div>
      </div>
    </div>
  );
}
