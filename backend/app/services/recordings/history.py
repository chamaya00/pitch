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
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.analysis.models import AnalysisStatus
from app.services.audio_analysis.models import AudioAnalysisStatus, AudioFeedbackStatus
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


HISTORY_SQL = """
SELECT r.document                AS document,
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


__all__ = ["HISTORY_SQL", "RecordingHistoryEntry", "entry_from_row"]
