"""API representation of an owner's recording history.

A list of recordings and *how far each one got*, never their results. Two
reasons, and both are design rather than convenience: a completed audio analysis
carries a pitch timeline of thousands of points, so embedding results would make
a fifty-row history a multi-megabyte response; and a list screen that showed
measurements would invite comparing two recordings whose conditions nobody
controlled.

``null`` in a status field means **no analysis of that kind exists**. It is not
"pending", not a failure, and must never be rendered as either.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.recording import RecordingResponse
from app.services.recordings.history import RecordingHistoryEntry


class RecordingHistoryItem(BaseModel):
    """One recording, with the state of its analyses."""

    recording: RecordingResponse
    speech_status: str | None = Field(
        default=None,
        description=(
            "Status of the most recent speech analysis, or `null` if this "
            "recording has never been analysed for speech. `null` is not "
            "`pending`."
        ),
    )
    audio_status: str | None = Field(
        default=None,
        description=(
            "Status of the most recent audio analysis, or `null` if it has never been measured."
        ),
    )
    feedback_status: str | None = Field(
        default=None,
        description=(
            "State of the AI interpretation of the audio measurements, or "
            "`null` if there is no audio analysis to interpret."
        ),
    )
    last_analysed_at: datetime | None = Field(
        default=None,
        description="When the most recent analysis of either kind was created (UTC).",
    )

    @classmethod
    def from_entry(cls, entry: RecordingHistoryEntry) -> "RecordingHistoryItem":
        return cls(
            recording=RecordingResponse.from_recording(entry.recording),
            speech_status=entry.speech_status.value if entry.speech_status else None,
            audio_status=entry.audio_status.value if entry.audio_status else None,
            feedback_status=entry.feedback_status.value if entry.feedback_status else None,
            last_analysed_at=entry.last_analysed_at,
        )


class RecordingHistoryResponse(BaseModel):
    """An owner's recordings, newest first."""

    items: list[RecordingHistoryItem] = Field(
        description="The recordings, newest first. Empty when this owner has none."
    )
    count: int = Field(description="How many items were returned.")
    limit: int = Field(description="The largest number of items this request could return.")

    @classmethod
    def from_entries(
        cls, entries: list[RecordingHistoryEntry], *, limit: int
    ) -> "RecordingHistoryResponse":
        items = [RecordingHistoryItem.from_entry(entry) for entry in entries]
        return cls(items=items, count=len(items), limit=limit)
