"""Public configuration exposed to clients."""

from pydantic import BaseModel, Field


class PublicConfigResponse(BaseModel):
    """Non-sensitive limits the frontend needs in order to describe itself.

    Exists so the UI can state the real limits and pre-check obvious violations
    instead of hardcoding numbers that would silently drift from the server's.
    Nothing here is a secret, and none of it is authoritative — the upload
    endpoint enforces every one of these values regardless of what a client did.
    """

    max_audio_size_mb: int = Field(description="Largest accepted upload, in megabytes.")
    max_audio_size_bytes: int = Field(description="The same limit in bytes, for exact checks.")
    max_audio_duration_seconds: int = Field(description="Longest accepted recording, in seconds.")
    supported_extensions: list[str] = Field(
        description="Accepted file extensions, lower-cased and dot-prefixed.",
        examples=[[".mp3", ".wav"]],
    )
