"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  createCredential,
  deleteIdentity,
  getIdentity,
  revokeCredential,
} from "@/lib/api";
import {
  DELETE_CONFIRMATION,
  MAX_KEY_LABEL,
  deletionWarning,
  describeKey,
  formatIssued,
  holdsNothing,
  isDeletionConfirmed,
  recoveryKeyProblem,
  revocationProblem,
  revocationWarning,
  summarise,
} from "@/lib/identity";
import { clearOwnerToken, readOwnerToken, storeOwnerToken } from "@/lib/owner";
import type { CreatedCredential, Credential, Identity } from "@/types/api";

/**
 * Where the deletion report waits out the page reload.
 *
 * `sessionStorage` rather than a query parameter: the message is for this tab
 * only, must not survive being bookmarked or shared, and says how many files
 * were removed from somebody's account.
 */
const DELETION_MESSAGE_KEY = "vocallens.deleted";

function rememberDeletion(message: string): void {
  try {
    window.sessionStorage.setItem(DELETION_MESSAGE_KEY, message);
  } catch {
    // Storage disabled. The page still reloads into a correct empty state; only
    // the confirmation sentence is lost, which is not worth failing over.
  }
}

function takeRememberedDeletion(): string | null {
  try {
    const message = window.sessionStorage.getItem(DELETION_MESSAGE_KEY);
    if (message !== null) window.sessionStorage.removeItem(DELETION_MESSAGE_KEY);
    return message;
  } catch {
    return null;
  }
}

type State =
  | { status: "loading" }
  | { status: "ready"; identity: Identity }
  | { status: "error"; message: string };

/** What the keys section is doing. Only one of these can be true at a time. */
type KeyAction =
  | { kind: "idle" }
  | { kind: "adding" }
  | { kind: "working" }
  /** A key that has just been created and not yet acknowledged as saved. */
  | { kind: "created"; credential: CreatedCredential }
  | { kind: "confirming"; credential: Credential }
  | { kind: "failed"; message: string };

/**
 * Your recovery key, and the ability to delete everything.
 *
 * Until Step 7P a person's entire history sat behind a key held in one browser,
 * with no way to see it, move it, or remove what it owned. Clearing site data
 * lost everything silently and permanently — and the audio stayed on the server,
 * unreachable, so it was not even a privacy-preserving loss.
 *
 * Two affordances close that, and neither is authentication:
 *
 * **The key is shown.** The server stores only a hash and *cannot* return it, so
 * this browser is the only place it exists in the clear. Displaying it is the
 * whole recovery mechanism, which is why the copy says plainly what happens if
 * it is lost.
 *
 * **Everything can be deleted.** Rows and stored audio, irreversibly, confirmed
 * by typing rather than by a button somebody clicks past.
 *
 * The key is revealed only on request. It is a bearer credential, and leaving it
 * on screen is how it ends up in a screenshot or a shared window.
 */
export function IdentityPanel() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleted, setDeleted] = useState<string | null>(null);
  const [keys, setKeys] = useState<Credential[] | null>(null);
  const [keyAction, setKeyAction] = useState<KeyAction>({ kind: "idle" });
  const [label, setLabel] = useState("");

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    getIdentity(controller.signal)
      .then((identity) => {
        if (controller.signal.aborted) return;
        // Picked up here rather than in an effect of its own: reading storage
        // during render would run on the server, where it does not exist, and a
        // synchronous setState in an effect body triggers a cascading render.
        // Inside this callback it is neither.
        const message = takeRememberedDeletion();
        if (message !== null) setDeleted(message);
        setKeys(identity.credentials);
        setState({ status: "ready", identity });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          message:
            error instanceof Error ? error.message : "Your key could not be checked.",
        });
      });
    return () => controller.abort();
  }, [nonce]);

  const restore = () => {
    const problem = recoveryKeyProblem(keyInput);
    if (problem !== null) {
      setRestoreError(problem);
      return;
    }
    if (!storeOwnerToken(keyInput.trim())) {
      setRestoreError("This browser would not let us save the key.");
      return;
    }
    setRestoreError(null);
    setRestoring(false);
    setKeyInput("");
    setRevealed(false);
    setDeleted(null);
    // Everything on the page is scoped to the key, so a new key means a new
    // page. Reloading is honest and avoids a half-swapped view.
    window.location.reload();
  };

  const remove = () => {
    deleteIdentity()
      .then((report) => {
        clearOwnerToken();
        const message =
          `Deleted ${report.recordings} recording${report.recordings === 1 ? "" : "s"} and ${
            report.audio_files_deleted
          } stored file${report.audio_files_deleted === 1 ? "" : "s"}.` +
          (report.audio_files_failed > 0
            ? ` ${report.audio_files_failed} file${
                report.audio_files_failed === 1 ? "" : "s"
              } could not be removed from the server.`
            : "");
        // Every panel on this page — history, comparison, progress — is scoped
        // to the identity that no longer exists, so refreshing this one alone
        // would leave the page showing deleted recordings beside an empty key.
        // Reloading is the honest fix; the report is carried across so the
        // confirmation is not lost in the process.
        rememberDeletion(message);
        window.location.reload();
      })
      .catch((error: unknown) => {
        setState({
          status: "error",
          message:
            error instanceof Error ? error.message : "Nothing could be deleted.",
        });
      });
  };

  const addKey = () => {
    setKeyAction({ kind: "working" });
    createCredential(label.trim())
      .then((credential) => {
        setLabel("");
        // The key is in this response and nowhere else, ever again — so it is
        // held in state until the reader says they have saved it, rather than
        // shown in a toast that a stray click dismisses.
        setKeyAction({ kind: "created", credential });
      })
      .catch((error: unknown) => {
        setKeyAction({
          kind: "failed",
          message:
            error instanceof Error ? error.message : "The key could not be added.",
        });
      });
  };

  const revoke = (credential: Credential) => {
    setKeyAction({ kind: "working" });
    revokeCredential(credential.credential_id)
      .then((remaining) => {
        setKeys(remaining);
        setKeyAction({ kind: "idle" });
        // Revoking the key this browser holds is allowed, and leaves it holding
        // a value the server no longer knows. Reloading lands on a truthful
        // page — a fresh empty identity — instead of one that looks signed in.
        if (credential.current) {
          clearOwnerToken();
          window.location.reload();
        }
      })
      .catch((error: unknown) => {
        setKeyAction({
          kind: "failed",
          message:
            error instanceof Error ? error.message : "The key could not be removed.",
        });
      });
  };

  const storedKey = readOwnerToken();
  const busy = keyAction.kind === "working";

  return (
    <section
      aria-labelledby="identity-heading"
      className="border-t border-border pt-10"
    >
      <h2 id="identity-heading" className="text-sm font-medium">
        Your key
      </h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
        Your recordings are linked to a key stored in this browser — not to an
        account. There is no password, and VocalLens stores only a scrambled copy
        of the key, so it cannot send you a new one. Save it and you can pick up
        your history on another device; lose it and the history is gone.
      </p>

      <p aria-live="polite" className="sr-only">
        {announcement(state, deleted)}
      </p>

      {state.status === "loading" && (
        <p className="mt-6 text-sm text-muted">Checking your key…</p>
      )}

      {state.status === "error" && (
        <div role="alert" className="mt-6 rounded-xl border border-danger/40 bg-danger/5 p-5">
          <p className="text-sm font-medium text-danger">Something went wrong</p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
          <div className="mt-4">
            <Button variant="secondary" onClick={reload}>
              Try again
            </Button>
          </div>
        </div>
      )}

      {state.status === "ready" && (
        <div className="fade-in mt-6 space-y-4">
          {deleted && (
            <p role="status" className="rounded-xl border border-border bg-surface-raised p-4 text-sm">
              {deleted} You now have a new, empty key.
            </p>
          )}

          <div className="rounded-xl border border-border bg-surface p-5">
            <p className="text-sm">{summarise(state.identity)}</p>
            <p className="mt-1 text-xs text-muted">
              Key issued {formatIssued(state.identity.created_at)}.
              {state.identity.anonymous && " No password is attached to it."}
            </p>

            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setRevealed((value) => !value)}>
                {revealed ? "Hide key" : "Show key"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setRestoring((value) => !value);
                  setRestoreError(null);
                }}
              >
                {restoring ? "Cancel" : "Use a different key"}
              </Button>
            </div>

            {revealed && (
              <div className="mt-4">
                <label
                  htmlFor="recovery-key"
                  className="block text-xs font-medium text-muted"
                >
                  Recovery key — save this somewhere safe
                </label>
                <input
                  id="recovery-key"
                  readOnly
                  value={storedKey ?? ""}
                  onFocus={(event) => event.currentTarget.select()}
                  className="mt-2 w-full rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm"
                />
                <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                  Anyone who has this key has your recordings. Treat it like a
                  password: do not share it or paste it into a screenshot.
                </p>
              </div>
            )}

            {restoring && (
              <div className="mt-4">
                <label htmlFor="restore-key" className="block text-xs font-medium text-muted">
                  Paste a key you saved earlier
                </label>
                <input
                  id="restore-key"
                  value={keyInput}
                  onChange={(event) => {
                    setKeyInput(event.target.value);
                    setRestoreError(null);
                  }}
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-2 w-full rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm"
                />
                {restoreError && (
                  <p role="alert" className="mt-2 max-w-prose text-xs text-danger">
                    {restoreError}
                  </p>
                )}
                <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                  This replaces the key in this browser. The history it currently
                  holds stays on the server and can be reached again by pasting
                  the old key back — so save it first if you have not.
                </p>
                <div className="mt-3">
                  <Button onClick={restore}>Use this key</Button>
                </div>
              </div>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-5">
            <h3 className="text-sm font-medium">Keys to this history</h3>
            <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
              Every key here reaches the same recordings. Adding one does not
              make a second account — it is another way in, for another device or
              for a copy kept somewhere safe. Removing one takes away a way in
              and nothing else.
            </p>

            <ul className="mt-4 space-y-2">
              {(keys ?? []).map((credential) => (
                <li
                  key={credential.credential_id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-raised px-3 py-2"
                >
                  <span className="text-sm">
                    {describeKey(credential)}
                    {credential.current && (
                      <span className="ml-2 rounded-full border border-border px-2 py-0.5 text-xs text-muted">
                        In use here
                      </span>
                    )}
                  </span>
                  <Button
                    variant="secondary"
                    disabled={busy || revocationProblem(keys ?? []) !== null}
                    onClick={() =>
                      setKeyAction({ kind: "confirming", credential })
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>

            {revocationProblem(keys ?? []) !== null && (
              <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                {revocationProblem(keys ?? [])}
              </p>
            )}

            {keyAction.kind === "confirming" && (
              <div className="mt-4 rounded-md border border-border p-4">
                <p className="text-sm font-medium">
                  Remove “{keyAction.credential.label}”?
                </p>
                <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
                  {revocationWarning(keyAction.credential)}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    disabled={busy}
                    onClick={() => revoke(keyAction.credential)}
                  >
                    Remove this key
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => setKeyAction({ kind: "idle" })}
                  >
                    Keep it
                  </Button>
                </div>
              </div>
            )}

            {keyAction.kind === "created" && (
              <div className="mt-4 rounded-md border border-border p-4">
                <label
                  htmlFor="new-key"
                  className="block text-xs font-medium text-muted"
                >
                  New key — copy it now, it is not shown again
                </label>
                <input
                  id="new-key"
                  readOnly
                  value={keyAction.credential.key}
                  onFocus={(event) => event.currentTarget.select()}
                  className="mt-2 w-full rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm"
                />
                <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                  VocalLens keeps only a scrambled copy, so this is the one and
                  only time it can be shown. Anyone who has it has your
                  recordings.
                </p>
                <div className="mt-3">
                  <Button
                    onClick={() => {
                      setKeys((current) =>
                        current === null
                          ? current
                          : [
                              ...current,
                              {
                                credential_id:
                                  keyAction.credential.credential_id,
                                label: keyAction.credential.label,
                                created_at: keyAction.credential.created_at,
                                current: false,
                              },
                            ],
                      );
                      setKeyAction({ kind: "idle" });
                    }}
                  >
                    I have saved it
                  </Button>
                </div>
              </div>
            )}

            {(keyAction.kind === "adding" || keyAction.kind === "failed") && (
              // The form stays open on failure, with what was typed still in
              // it. Rate limiting is temporary by definition, so collapsing the
              // form and making somebody retype the label to retry is friction
              // for no reason.
              <div className="mt-4">
                <label
                  htmlFor="key-label"
                  className="block text-xs font-medium text-muted"
                >
                  What is this key for?
                </label>
                <input
                  id="key-label"
                  value={label}
                  maxLength={MAX_KEY_LABEL}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="Phone"
                  autoComplete="off"
                  className="mt-2 w-full max-w-xs rounded-md border border-border bg-surface-raised px-3 py-2 text-sm"
                />
                <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">
                  A name for your own benefit — it is never part of getting in.
                </p>
                {keyAction.kind === "failed" && (
                  <p role="alert" className="mt-2 max-w-prose text-xs text-danger">
                    {keyAction.message}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button disabled={busy} onClick={addKey}>
                    {busy ? "Adding…" : "Add key"}
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      setKeyAction({ kind: "idle" });
                      setLabel("");
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {keyAction.kind === "idle" && (
              <div className="mt-4">
                <Button
                  variant="secondary"
                  onClick={() => setKeyAction({ kind: "adding" })}
                >
                  Add another key
                </Button>
              </div>
            )}
          </div>

          <div className="rounded-xl border border-danger/40 p-5">
            <p className="text-sm font-medium">Delete everything</p>
            <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
              {deletionWarning(state.identity)}
            </p>

            {!confirming ? (
              <div className="mt-4">
                <Button
                  variant="secondary"
                  // Disabled only when the key holds *nothing*. A key holding
                  // songs and no recordings holds something, and a button that
                  // refused to delete it would be refusing the one thing this
                  // panel exists to offer.
                  disabled={holdsNothing(state.identity)}
                  onClick={() => setConfirming(true)}
                >
                  Delete everything
                </Button>
              </div>
            ) : (
              <div className="mt-4">
                <label htmlFor="delete-confirm" className="block text-xs font-medium">
                  Type <span className="font-mono">{DELETE_CONFIRMATION}</span> to
                  confirm
                </label>
                <input
                  id="delete-confirm"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  autoComplete="off"
                  className="mt-2 w-full max-w-xs rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm"
                />
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    disabled={!isDeletionConfirmed(confirmation)}
                    onClick={remove}
                  >
                    Delete permanently
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setConfirming(false);
                      setConfirmation("");
                    }}
                  >
                    Keep my recordings
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function announcement(state: State, deleted: string | null): string {
  if (deleted) return deleted;
  switch (state.status) {
    case "loading":
      return "Checking your key.";
    case "error":
      return state.message;
    case "ready":
      return summarise(state.identity);
  }
}
