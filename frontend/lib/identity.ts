import type { Identity } from "@/types/api";

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

/** What this identity holds, as a sentence. */
export function summarise(identity: Identity): string {
  if (identity.recordings === 0) {
    return "This key holds no recordings yet.";
  }
  const parts = [
    `${identity.recordings} recording${identity.recordings === 1 ? "" : "s"}`,
  ];
  if (identity.analysed_recordings > 0) {
    parts.push(`${identity.analysed_recordings} measured`);
  }
  if (identity.ai_feedback > 0) {
    parts.push(
      `${identity.ai_feedback} with generated feedback`,
    );
  }
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
  if (identity.recordings === 0) {
    return "There is nothing stored under this key yet, so there is nothing to delete.";
  }
  const feedback =
    identity.ai_feedback > 0
      ? ` ${identity.ai_feedback} of them carr${identity.ai_feedback === 1 ? "ies" : "y"} generated feedback, which cannot be recovered.`
      : "";
  return (
    `This permanently deletes ${identity.recordings} recording` +
    `${identity.recordings === 1 ? "" : "s"}, every measurement taken from ` +
    `${identity.recordings === 1 ? "it" : "them"}, and the stored audio itself.` +
    `${feedback} It cannot be undone.`
  );
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
