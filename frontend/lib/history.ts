import type { RecordingHistoryItem } from "@/types/api";

/**
 * Turning history statuses into words.
 *
 * Kept out of the components and free of React so the one rule that matters
 * here can be tested directly: **`null` is not `pending` and not a failure.**
 * A recording nobody has analysed reads "Not analysed", which is a statement
 * about what was asked for, not about how it went.
 */

/** What a status chip says and how it reads. */
export interface StatusLabel {
  readonly text: string;
  /** Drives colour only. `absent` is deliberately not `bad`. */
  readonly tone: "absent" | "running" | "good" | "bad";
}

const SPEECH_LABELS: Record<string, StatusLabel> = {
  pending: { text: "Queued", tone: "running" },
  transcribing: { text: "Transcribing", tone: "running" },
  analyzing: { text: "Analysing", tone: "running" },
  completed: { text: "Analysed", tone: "good" },
  failed: { text: "Failed", tone: "bad" },
};

const AUDIO_LABELS: Record<string, StatusLabel> = {
  pending: { text: "Queued", tone: "running" },
  analyzing: { text: "Measuring", tone: "running" },
  completed: { text: "Measured", tone: "good" },
  failed: { text: "Not measured", tone: "bad" },
};

/** The label for "no analysis of this kind exists". */
export const NOT_RUN: StatusLabel = { text: "Not run", tone: "absent" };

export function speechLabel(status: string | null): StatusLabel {
  if (status === null) return NOT_RUN;
  return SPEECH_LABELS[status] ?? { text: status, tone: "absent" };
}

export function audioLabel(status: string | null): StatusLabel {
  if (status === null) return NOT_RUN;
  return AUDIO_LABELS[status] ?? { text: status, tone: "absent" };
}

/**
 * Whether a history row has anything worth opening.
 *
 * A recording nobody has analysed still opens — that is how somebody analyses
 * it — so this is about the *summary line*, not about access.
 */
export function hasBeenAnalysed(item: RecordingHistoryItem): boolean {
  return item.speech_status !== null || item.audio_status !== null;
}

/**
 * A short, absolute date for a history row.
 *
 * Absolute rather than "3 days ago" on purpose: relative time is friendlier to
 * read and worse to act on, and the one thing this list is for is finding a
 * particular take. Rendered in the reader's locale and time zone.
 */
export function formatWhen(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Unknown date";
  return when.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
