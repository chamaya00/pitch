import type { Credential, Identity } from "@/types/api";

/**
 * Wording and rules for the recovery key.
 *
 * The key is the only thing standing between somebody and their entire history,
 * and the server cannot help: it stores a SHA-256 hash, so a lost key is lost.
 * That makes two things the responsibility of this browser, and therefore of
 * this module:
 *
 * **Say what is at stake, in counts rather than adjectives.** "5 recordings, 4
 * measured, 1 with generated feedback" is actionable; "your data" is not.
 * Generated feedback is called out separately because measurements can be
 * recomputed from the audio and prose cannot — it cost a provider call.
 *
 * **Refuse a key that cannot be one.** Restoring a mistyped value would replace
 * a working identity with a broken one and lose the history in the act of trying
 * to save it, so the shape is checked before anything is written.
 */

/** The server's key shape: 22 characters of URL-safe base64. */
const KEY_PATTERN = /^[A-Za-z0-9_-]{22}$/;

export function isRecoveryKey(value: string): boolean {
  return KEY_PATTERN.test(value.trim());
}

/** Why a pasted value was refused, or `null` if it is usable. */
export function recoveryKeyProblem(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "Paste the key you saved.";
  if (!KEY_PATTERN.test(trimmed)) {
    return "That doesn't look like a VocalLens key. It is 22 characters of letters, numbers, hyphens and underscores.";
  }
  return null;
}

/**
 * What this identity holds, as a sentence.
 *
 * **"Holds nothing" means nothing at all.** Until song references existed, a
 * recording was the only thing a key could hold, so an early return on
 * `recordings === 0` said the truth. It does not any more: a key can hold songs
 * somebody described and no recordings, and telling them it holds nothing would
 * be false about data this key is the only way back to.
 */
export function summarise(identity: Identity): string {
  const parts: string[] = [];
  if (identity.recordings > 0) {
    parts.push(`${identity.recordings} recording${identity.recordings === 1 ? "" : "s"}`);
    if (identity.analysed_recordings > 0) {
      parts.push(`${identity.analysed_recordings} measured`);
    }
    if (identity.ai_feedback > 0) {
      parts.push(`${identity.ai_feedback} with generated feedback`);
    }
  }
  if (identity.song_references > 0) {
    parts.push(
      `${identity.song_references} song${identity.song_references === 1 ? "" : "s"} you described`,
    );
  }
  if (parts.length === 0) return "This key holds nothing yet.";
  return `This key holds ${parts.join(", ")}.`;
}

/**
 * What deleting would remove, in the second person, for the confirmation.
 *
 * Deliberately concrete. "Delete everything?" is a question people answer
 * without reading; "this deletes 5 recordings, including 1 with generated
 * feedback" is one they consider.
 */
export function deletionWarning(identity: Identity): string {
  const songs =
    identity.song_references > 0
      ? `${identity.song_references} song${identity.song_references === 1 ? "" : "s"} you described`
      : "";

  if (identity.recordings === 0) {
    // Not "there is nothing to delete". A key holding only songs holds
    // something, and this is the sentence somebody reads before losing it.
    return songs === ""
      ? "There is nothing stored under this key yet, so there is nothing to delete."
      : `This permanently deletes ${songs}. It cannot be undone.`;
  }

  const feedback =
    identity.ai_feedback > 0
      ? ` ${identity.ai_feedback} of them carr${identity.ai_feedback === 1 ? "ies" : "y"} generated feedback, which cannot be recovered.`
      : "";
  const also = songs === "" ? "" : ` It also deletes ${songs}.`;
  return (
    `This permanently deletes ${identity.recordings} recording` +
    `${identity.recordings === 1 ? "" : "s"}, every measurement taken from ` +
    `${identity.recordings === 1 ? "it" : "them"}, and the stored audio itself.` +
    `${feedback}${also} It cannot be undone.`
  );
}

/**
 * Whether this key holds nothing at all.
 *
 * One predicate, so the sentence a reader sees and the button they press cannot
 * disagree about what "nothing" means — and so a third kind of held thing is a
 * change in one place rather than in every `=== 0` comparison in the panel.
 */
export function holdsNothing(identity: Identity): boolean {
  return identity.recordings === 0 && identity.song_references === 0;
}

/** The phrase somebody has to type to confirm deletion. */
export const DELETE_CONFIRMATION = "delete";

export function isDeletionConfirmed(typed: string): boolean {
  return typed.trim().toLowerCase() === DELETE_CONFIRMATION;
}

/** When the identity was issued, in the reader's locale. */
export function formatIssued(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Unknown date";
  return when.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

/* --- Keys ------------------------------------------------------------------
 *
 * An identity can have several keys. All of them reach the same recordings, so
 * the wording below never implies otherwise: adding one is "another way in",
 * not "another account", and revoking one removes a key, not any data.
 */

/** Longest label the server will keep. Anything longer is trimmed there too. */
export const MAX_KEY_LABEL = 60;

/** The label used when somebody names a key nothing. Matches the server's. */
export const DEFAULT_KEY_LABEL = "New key";

/**
 * How a key is described in a list.
 *
 * Never its value, and never its hash — neither is available to this browser
 * for any key but the one it holds, and printing that one in a list is how a
 * bearer credential ends up in a screenshot.
 */
export function describeKey(credential: Credential): string {
  const issued = formatIssued(credential.created_at);
  return credential.current
    ? `${credential.label} — this browser, added ${issued}`
    : `${credential.label} — added ${issued}`;
}

/**
 * Why a key cannot be revoked, or `null` if it can.
 *
 * The last one is refused by the server, and the reason is worth stating rather
 * than showing a failure: removing it would leave the recordings owned and
 * unreachable, which is a different thing from deleting them.
 */
export function revocationProblem(all: readonly Credential[]): string | null {
  if (all.length <= 1) {
    return "This is the only way in to your recordings. Add another key first, or delete everything if that is what you want.";
  }
  // Revoking the key this browser is holding is allowed once another exists.
  // It is a deliberate thing to do — from a device you are handing on — and
  // the warning says what happens next rather than the button refusing.
  return null;
}

/** What revoking this key does, and what it does not do. */
export function revocationWarning(credential: Credential): string {
  const base =
    "This removes a way in. Your recordings, measurements and feedback all stay exactly as they are.";
  return credential.current
    ? `${base} It is the key this browser is using, so you will need one of your other keys here afterwards.`
    : base;
}
