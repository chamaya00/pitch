"""The recording domain model.

This is the application's own representation, independent of how it is stored
and of how it is serialised over HTTP. The API schema in ``app/schemas`` is
built from it explicitly, so adding an internal field here cannot silently
widen the public response.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.audio.metadata import AudioFormat


def utc_now() -> datetime:
    return datetime.now(UTC)


class Recording(BaseModel):
    """An uploaded recording that has been validated and stored."""

    model_config = ConfigDict(frozen=True)

    recording_id: str = Field(description="Server-generated identifier.")
    #: Sanitized client filename, kept for display only. Never a storage path.
    original_filename: str
    audio_format: AudioFormat
    duration_seconds: float
    sample_rate: int
    channels: int
    size_bytes: int
    bits_per_sample: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
