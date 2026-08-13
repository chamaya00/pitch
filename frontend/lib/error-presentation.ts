/**
 * What a failure looks like to the person in front of it.
 *
 * Every error boundary in `app/` renders **only** what this module returns. The
 * functions below receive the framework's error object and deliberately discard
 * it, which is the whole point: an exception message can carry a stack trace, a
 * filesystem path, a database DSN, a provider key or somebody's owner id, and
 * none of that is ever a sentence worth showing a singer. Keeping the decision
 * here rather than in the components means "the error never reaches the screen"
 * is one testable function rather than a promise about three files.
 *
 * `digest` is not shown either. Next.js supplies it as a correlation hash for
 * server-side errors, and it is a real debugging aid — but it is an internal
 * identifier, and this codebase does not put internal identifiers on screen
 * (see `schemas/identity.py` on why the owner id is never returned). A person
 * who cannot act on a value should not be shown it.
 *
 * Three kinds, because they are three different situations and telling them
 * apart is most of the value:
 *
 * - `route` — a page broke. The rest of the app is fine, retrying may work.
 * - `notFound` — the address is wrong. Nothing is broken; retrying is pointless.
 * - `global` — the shell itself broke, so nothing on the page can be trusted.
 *
 * Pure and dependency-free so it can be tested with `node --test`, which is how
 * every other piece of copy in `lib/` is tested.
 */

export type FailureKind = "route" | "notFound" | "global";

export interface FailurePresentation {
  /** The heading. Says what happened, not what went wrong internally. */
  title: string;
  /** One or two sentences: what this means, and what to do next. */
  body: string;
  /** Label for the action that re-attempts the failed render, if offered. */
  retryLabel: string | null;
  /** Label for the escape hatch back to somewhere that works. */
  homeLabel: string;
}

const PRESENTATIONS: Readonly<Record<FailureKind, FailurePresentation>> = {
  route: {
    title: "Something went wrong on this page",
    body: "This part of VocalLens failed to load. Your recordings and your key are unaffected — nothing has been lost. Trying again often works.",
    retryLabel: "Try again",
    homeLabel: "Go to the home page",
  },
  notFound: {
    title: "That page doesn't exist",
    body: "The address you followed doesn't match anything in VocalLens. Nothing has gone wrong and nothing has been lost — the link was probably mistyped or out of date.",
    // Nothing to retry: the address is wrong, so re-attempting it would fail
    // in exactly the same way. Offering a retry here would be pretending.
    retryLabel: null,
    homeLabel: "Go to the home page",
  },
  global: {
    title: "VocalLens couldn't start",
    body: "The page failed to load properly. Your recordings and your key are stored safely and are unaffected. Reloading usually fixes it.",
    retryLabel: "Reload the page",
    homeLabel: "Go to the home page",
  },
};

/**
 * The failure state for a broken page.
 *
 * Takes the error and ignores it. The parameter exists so the call site reads
 * honestly — the boundary *is* handed an error — and so this function is the
 * single place a future change would have to go through to start showing one.
 */
export function presentRouteFailure(_error?: unknown): FailurePresentation {
  return PRESENTATIONS.route;
}

/** The failure state for a broken application shell. See above; same rule. */
export function presentGlobalFailure(_error?: unknown): FailurePresentation {
  return PRESENTATIONS.global;
}

/** The state for an address that matches nothing. No error object exists. */
export function presentNotFound(): FailurePresentation {
  return PRESENTATIONS.notFound;
}

/** Every presentation, for tests that assert a property of all of them. */
export function allPresentations(): readonly FailurePresentation[] {
  return Object.values(PRESENTATIONS);
}
