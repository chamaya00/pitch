"""API representation of a recording.

Built explicitly from the domain model so that internal fields cannot leak into
a response by being added upstream. Filesystem paths are never part of this
contract.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.recordings.models import Recording


class RecordingResponse(BaseModel):
    """A recording that was accepted, validated and stored."""

    recording_id: str = Field(
        description="Server-generated identifier for this recording.",
        examples=["8f14e45fceea167a5a36dedd4bea2543"],
    )
    original_filename: str = Field(
        description="Sanitized version of the uploaded file name, for display only.",
        examples=["practice-take-1.wav"],
    )
    format: str = Field(description="Audio format detected from the file contents.")
    duration_seconds: float = Field(
        description=(
            "Duration read from the container headers. Accurate to well under a "
            "percent; not a decoded measurement."
        )
    )
    sample_rate: int = Field(description="Samples per second.")
    channels: int = Field(description="Number of audio channels.")
    size_bytes: int = Field(description="Size of the stored file in bytes.")
    bits_per_sample: int | None = Field(
        default=None,
        description="Bit depth, available for PCM WAV only.",
    )
    created_at: datetime = Field(description="When the recording was accepted (UTC).")

    @classmethod
    def from_recording(cls, recording: Recording) -> "RecordingResponse":
        return cls(
            recording_id=recording.recording_id,
            original_filename=recording.original_filename,
            format=recording.audio_format.value,
            duration_seconds=recording.duration_seconds,
            sample_rate=recording.sample_rate,
            channels=recording.channels,
            size_bytes=recording.size_bytes,
            bits_per_sample=recording.bits_per_sample,
            created_at=recording.created_at,
        )
