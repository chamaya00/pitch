"""API representation of an owner's recording history.

A list of recordings and *how far each one got*, never their results. Two
reasons, and both are design rather than convenience: a completed audio analysis
carries a pitch timeline of thousands of points, so embedding results would make
a fifty-row history a multi-megabyte response; and a list screen that showed
measurements would invite comparing two recordings whose conditions nobody
controlled.

``null`` in a status field means **no analysis of that kind exists**. It is not
"pending", not a failure, and must never be rendered as either.

``next_cursor`` is the same principle applied to the list itself: a page that is
not the whole history says so, rather than leaving a client to guess from
whether ``count`` happens to equal ``limit``.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.recording import RecordingResponse
from app.services.recordings.history import RecordingHistoryEntry, RecordingHistoryPage


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
    """One page of an owner's recordings, newest first."""

    items: list[RecordingHistoryItem] = Field(
        description="The recordings, newest first. Empty when this owner has none."
    )
    count: int = Field(
        description=(
            "How many items were returned **on this page**. It is not how many "
            "recordings the caller has; when `next_cursor` is set there are more."
        )
    )
    limit: int = Field(description="The largest number of items this request could return.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass as `cursor` to fetch the page after this one. `null` means "
            "this page is the last — established by looking for a further "
            "recording, not by comparing `count` against `limit`. Treat the "
            "value as opaque: it is this server's bookmark, and its contents "
            "are not part of the API."
        ),
        examples=["MjAyNi0wOC0xN1QxMDoxMTowMCswMDowMHwwYzA3OTkxYmE4NTg0NDllOTc2Y2I5M2Y5MzNmNWRkZQ"],
    )

    @classmethod
    def from_entries(
        cls, entries: list[RecordingHistoryEntry], *, limit: int, next_cursor: str | None = None
    ) -> "RecordingHistoryResponse":
        items = [RecordingHistoryItem.from_entry(entry) for entry in entries]
        return cls(items=items, count=len(items), limit=limit, next_cursor=next_cursor)

    @classmethod
    def from_page(cls, page: RecordingHistoryPage, *, limit: int) -> "RecordingHistoryResponse":
        return cls.from_entries(page.entries, limit=limit, next_cursor=page.next_cursor)
