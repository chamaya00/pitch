"use client";

import { presentGlobalFailure } from "@/lib/error-presentation";

/**
 * The last boundary: the application shell itself failed.
 *
 * `global-error.tsx` replaces the root layout, so it must supply its own
 * `<html>` and `<body>` — and, more importantly, it must not assume anything
 * the root layout provides. That is why this file looks unlike every other
 * component in the project:
 *
 * **No Tailwind classes.** The utility classes and the design tokens both come
 * from `globals.css`, which `layout.tsx` imports. If the layout is what broke,
 * relying on its stylesheet is relying on the thing that failed. The palette
 * below is inline and self-contained.
 *
 * **No `next/font`, no `SiteHeader`, no `SiteFooter`, no `next/link`.** A
 * system font stack always resolves, and a plain anchor performs a real
 * navigation rather than a client-side transition through a router that may be
 * part of the problem.
 *
 * **No providers.** There are none to miss — this codebase has no React context
 * anywhere, and the theme is plain `prefers-color-scheme` — but the rule is
 * written down so the next person adding a provider knows this file must keep
 * working without it.
 *
 * Both themes are handled by a media query rather than by a class, so the page
 * is correct with no JavaScript beyond React itself. Nothing animates, so there
 * is nothing for reduced motion to disable.
 *
 * The error object is discarded exactly as it is in `error.tsx`; see
 * `lib/error-presentation.ts`.
 */

const STYLES = `
  :root {
    color-scheme: light dark;
    --vl-bg: #f7f7f8;
    --vl-surface: #ffffff;
    --vl-border: #d9d9de;
    --vl-fg: #14141a;
    --vl-muted: #5c5c68;
    --vl-accent: #4f46e5;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --vl-bg: #0b0b0f;
      --vl-surface: #131319;
      --vl-border: #2a2a35;
      --vl-fg: #ececf1;
      --vl-muted: #9a9aa8;
      --vl-accent: #8b7cf6;
    }
  }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    background: var(--vl-bg);
    color: var(--vl-fg);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height: 1.6;
  }
  .vl-wrap {
    max-width: 40rem;
    margin: 0 auto;
    padding: 5rem 1.5rem;
    box-sizing: border-box;
  }
  .vl-card {
    background: var(--vl-surface);
    border: 1px solid var(--vl-border);
    border-radius: 0.75rem;
    padding: 1.5rem;
  }
  .vl-title { font-size: 1.125rem; font-weight: 500; margin: 0; }
  .vl-body { font-size: 0.875rem; color: var(--vl-muted); margin: 0.5rem 0 0; }
  .vl-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1.5rem; }
  .vl-action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 0.375rem;
    padding: 0.625rem 1.25rem;
    font-size: 0.875rem;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    text-decoration: none;
    border: 1px solid var(--vl-border);
    background: var(--vl-surface);
    color: var(--vl-fg);
  }
  .vl-action--primary {
    background: var(--vl-accent);
    border-color: var(--vl-accent);
    color: #ffffff;
  }
  :focus-visible { outline: 2px solid var(--vl-accent); outline-offset: 2px; }
`;

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const failure = presentGlobalFailure(error);

  return (
    <html lang="en">
      <body>
        <style dangerouslySetInnerHTML={{ __html: STYLES }} />
        <div className="vl-wrap">
          <div className="vl-card" role="alert">
            <h1 className="vl-title">{failure.title}</h1>
            <p className="vl-body">{failure.body}</p>
            <div className="vl-actions">
              {failure.retryLabel && (
                <button
                  type="button"
                  className="vl-action vl-action--primary"
                  onClick={reset}
                >
                  {failure.retryLabel}
                </button>
              )}
              {/* Deliberately a plain anchor, not `next/link`. This boundary
                  exists because the shell failed; a client-side transition
                  through the router is exactly what must not be relied on
                  here, and a full navigation is the recovery. */}
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
              <a className="vl-action" href="/">
                {failure.homeLabel}
              </a>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
