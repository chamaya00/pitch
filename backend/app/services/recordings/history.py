"""Recording history: what an owner has recorded, and how far each one got.

A history row is a recording plus the *state* of its two analyses — not their
results. That is the whole design decision here. A list screen needs to know
whether a recording has been measured; it does not need the pitch timeline, and
sending one per row would move megabytes to render a table.

The query is a single statement with two lateral joins, so a history of fifty
recordings is one round trip rather than a hundred and one. It is covered by
``recordings_owner_created_idx`` for the ordering and by the per-recording
indexes for the joins.

**Status is reported, never inferred.** A recording with no analysis row has
``speech_status = None``, which is not the same as "pending" and is not rendered
as a failure — it means nobody has asked for that analysis yet.

**A page says whether it is the last one.** Step 10.13 measured what the old
answer left out: an owner with 137 recordings was sent 50 and given no field
that could say the other 87 existed, and the screen said "Everything you have
uploaded from this browser". The query now asks for one row more than the caller
wanted and :func:`page_of` hands that row back as the answer to "is there
more?" — exact, in the same round trip, and without a second ``count(*)`` whose
answer could already be stale by the time it is rendered.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.analysis.models import AnalysisStatus
from app.services.audio_analysis.models import AudioAnalysisStatus, AudioFeedbackStatus
from app.services.recordings.cursor import HistoryCursor, encode_cursor
from app.services.recordings.models import Recording


class RecordingHistoryEntry(BaseModel):
    """One row of an owner's history."""

    model_config = ConfigDict(frozen=True)

    recording: Recording

    #: ``None`` means no analysis of that kind exists — never "pending", and
    #: never zero.
    speech_status: AnalysisStatus | None = None
    audio_status: AudioAnalysisStatus | None = None
    feedback_status: AudioFeedbackStatus | None = None

    #: When the most recent analysis of either kind was created, for a "last
    #: activity" column. ``None`` when neither has ever run.
    last_analysed_at: datetime | None = None


#: The first page: this owner's newest recordings.
#:
#: ``LIMIT %s`` is always sent ``limit + 1``. The extra row is never returned to
#: anyone — :func:`page_of` drops it — and exists only so the page can say
#: whether another one follows.
HISTORY_SQL = """
SELECT r.id                      AS recording_id,
       r.created_at              AS created_at,
       r.document                AS document,
       s.status                  AS speech_status,
       a.status                  AS audio_status,
       a.feedback_status         AS feedback_status,
       GREATEST(s.created_at, a.created_at) AS last_analysed_at
  FROM recordings r
  LEFT JOIN LATERAL (
      SELECT status, created_at FROM speech_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) s ON TRUE
  LEFT JOIN LATERAL (
      SELECT status, feedback_status, created_at FROM audio_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) a ON TRUE
 WHERE r.owner_id = %s
 ORDER BY r.created_at DESC, r.id DESC
 LIMIT %s
"""

#: A later page: the same query, resumed after the row a cursor names.
#:
#: ``(r.created_at, r.id) < (%s, %s)`` is a row-wise comparison, which is the
#: whole tie-break in one predicate: strictly older, or the same instant and a
#: lower id. Writing it as ``created_at < %s OR (created_at = %s AND id < %s)``
#: would mean the same thing and give the planner a disjunction to work around.
#:
#: The ordering is identical to the first page's, because a cursor is only
#: meaningful against the ordering that produced it.
HISTORY_AFTER_SQL = """
SELECT r.id                      AS recording_id,
       r.created_at              AS created_at,
       r.document                AS document,
       s.status                  AS speech_status,
       a.status                  AS audio_status,
       a.feedback_status         AS feedback_status,
       GREATEST(s.created_at, a.created_at) AS last_analysed_at
  FROM recordings r
  LEFT JOIN LATERAL (
      SELECT status, created_at FROM speech_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) s ON TRUE
  LEFT JOIN LATERAL (
      SELECT status, feedback_status, created_at FROM audio_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) a ON TRUE
 WHERE r.owner_id = %s
   AND (r.created_at, r.id) < (%s, %s)
 ORDER BY r.created_at DESC, r.id DESC
 LIMIT %s
"""


@dataclass(frozen=True, slots=True)
class RecordingHistoryPage:
    """One page of history, and where the next one starts.

    ``next_cursor`` is ``None`` **only** when this page is genuinely the last —
    it is decided by whether a row beyond the page exists, not by comparing the
    number returned against the limit. "I asked for 50 and got 50" is not
    evidence either way: an owner with exactly 50 recordings and an owner with
    500 look the same from there.
    """

    entries: list[RecordingHistoryEntry]
    next_cursor: str | None


def page_of(rows: list[dict[str, Any]], *, limit: int) -> RecordingHistoryPage:
    """Turn ``limit + 1`` rows into a page of at most ``limit``.

    The extra row is the probe. If it arrived, a next page exists and this
    page's cursor is built from its *last returned* row — not from the probe,
    which the caller has not been shown and must not skip past.
    """
    has_more = len(rows) > limit
    visible = rows[:limit]
    entries = [entry_from_row(row) for row in visible]
    cursor = (
        encode_cursor(visible[-1]["created_at"], str(visible[-1]["recording_id"]).strip())
        if has_more and visible
        else None
    )
    return RecordingHistoryPage(entries=entries, next_cursor=cursor)


def cursor_parameters(owner_id: Any, cursor: HistoryCursor | None, limit: int) -> tuple[Any, ...]:
    """The bound parameters for whichever of the two statements applies."""
    if cursor is None:
        return (owner_id, limit + 1)
    return (owner_id, cursor.created_at, cursor.recording_id, limit + 1)


def entry_from_row(row: dict[str, Any]) -> RecordingHistoryEntry:
    """Build a history entry from one result row.

    Statuses arrive as the raw column values; pydantic validates them against
    the enums, so a value the application does not know about fails loudly here
    rather than reaching a response as a string nobody handles.
    """
    return RecordingHistoryEntry.model_validate(
        {
            "recording": Recording.model_validate(row["document"]),
            "speech_status": row["speech_status"],
            "audio_status": row["audio_status"],
            "feedback_status": row["feedback_status"],
            "last_analysed_at": row["last_analysed_at"],
        }
    )


__all__ = [
    "HISTORY_AFTER_SQL",
    "HISTORY_SQL",
    "RecordingHistoryEntry",
    "RecordingHistoryPage",
    "cursor_parameters",
    "entry_from_row",
    "page_of",
]
