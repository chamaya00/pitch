import type { RecordingHistory, RecordingHistoryItem } from "@/types/api";

/**
 * Turning history statuses into words, and stitching pages of them together.
 *
 * Kept out of the components and free of React so the two rules that matter
 * here can be tested directly:
 *
 * **`null` is not `pending` and not a failure.** A recording nobody has
 * analysed reads "Not analysed", which is a statement about what was asked for,
 * not about how it went.
 *
 * **A page is not the history.** Until Step 10.13 the list rendered one page and
 * called it "everything you have uploaded from this browser" — with 137
 * recordings it showed 50 and said nothing about the other 87. What is loaded
 * and whether more exists are now two separate facts, and the screen states
 * both.
 */

/**
 * Every page loaded so far, and where the next one starts.
 *
 * `nextCursor === null` means the list on screen is the whole history. That is
 * the server's answer, carried through unchanged — it is never inferred from
 * how many items came back.
 */
export interface LoadedHistory {
  readonly items: readonly RecordingHistoryItem[];
  readonly nextCursor: string | null;
}

export function firstPage(page: RecordingHistory): LoadedHistory {
  return { items: page.items, nextCursor: page.next_cursor };
}

/**
 * Add a page to what is already on screen.
 *
 * Recordings already loaded are dropped rather than repeated. Keyset paging
 * cannot serve the same row twice, so this is not a workaround for the server;
 * it is protection against *this* code asking for the same page twice — two
 * clicks on "Show older recordings" before the first answer arrives — which
 * would otherwise render duplicate React keys and a list that lies about how
 * much there is.
 */
export function appendPage(
  loaded: LoadedHistory,
  page: RecordingHistory,
): LoadedHistory {
  const known = new Set(loaded.items.map((item) => item.recording.recording_id));
  const fresh = page.items.filter(
    (item) => !known.has(item.recording.recording_id),
  );
  return {
    items: [...loaded.items, ...fresh],
    nextCursor: page.next_cursor,
  };
}

/**
 * What to tell a screen reader about the list.
 *
 * Deliberately two clauses when the list is partial. "50 recordings" is what
 * this used to announce with 137 in the account, and a count that is really a
 * page size is the same untruth as a measurement that was never taken.
 */
export function historyAnnouncement(loaded: LoadedHistory): string {
  const count = loaded.items.length;
  if (count === 0) return "You have no recordings yet.";
  const noun = count === 1 ? "recording" : "recordings";
  return loaded.nextCursor === null
    ? `${count} ${noun}.`
    : `${count} ${noun} shown. More can be loaded.`;
}

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
