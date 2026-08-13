"""The upload pipeline.

Coordinates the Phase 1 services in a fixed order and owns the temporary file
for the duration::

    filename → extension → stream to temp file (counting bytes)
    → parse metadata → extension/content agreement → duration limit
    → store bytes → record metadata → Recording

Deliberately free of FastAPI: it consumes an async iterator of chunks, so it can
be driven by an ``UploadFile``, a test, or anything else. Every exit path
removes the temporary file.
"""

import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.audio.metadata import AudioFormat, AudioMetadata, extract_metadata
from app.services.audio.storage import RecordingStorage, StorageError
from app.services.audio.validation import (
    sanitize_filename,
    validate_extension,
)
from app.services.recordings.models import Recording
from app.services.recordings.postgres_repository import OwnedRecordingRepository

logger = get_logger(__name__)

_CHUNK_BYTES: Final = 1024 * 1024

#: Which extension each detected format is allowed to arrive under. The stored
#: extension is taken from the *detected* format, never from the client's name.
_EXTENSION_BY_FORMAT: Final[dict[AudioFormat, str]] = {
    AudioFormat.WAV: ".wav",
    AudioFormat.MP3: ".mp3",
}

_INTERNAL_MESSAGE: Final = "The recording could not be saved. Please try again."


async def _stream_to_temp_file(chunks: AsyncIterator[bytes], *, max_bytes: int) -> tuple[Path, int]:
    """Write the upload to a server-owned temporary file, counting as it goes.

    The count is of bytes actually received: ``Content-Length`` is never
    consulted, and reading stops the moment the limit is passed rather than
    after the fact. ``mkstemp`` supplies a random, server-chosen name with
    owner-only permissions, so nothing the client sent influences the path.
    """
    descriptor, name = tempfile.mkstemp(prefix="vocallens-upload-")
    temp_path = Path(name)
    received = 0

    try:
        with os.fdopen(descriptor, "wb") as handle:
            async for chunk in chunks:
                received += len(chunk)
                if received > max_bytes:
                    raise ApiError(
                        ErrorCode.FILE_TOO_LARGE,
                        f"Files must be {max_bytes / (1024 * 1024):.0f} MB or smaller.",
                    )
                handle.write(chunk)
    except BaseException:
        # Covers the size rejection, client disconnects and cancellation alike.
        temp_path.unlink(missing_ok=True)
        raise

    return temp_path, received


def _require_matching_format(extension: str, metadata: AudioMetadata) -> str:
    """Reject a file whose real format disagrees with its extension.

    Returns the extension to store under, derived from the detected format.
    Renaming a mismatch to match its contents would silently accept a file the
    user did not think they were sending.
    """
    expected = _EXTENSION_BY_FORMAT[metadata.format]
    if extension != expected:
        raise ApiError(
            ErrorCode.FORMAT_MISMATCH,
            f"This file is named '{extension}' but contains "
            f"{metadata.format.value.upper()} audio. "
            "Please upload it with the correct extension.",
        )
    return expected


def _require_duration_within_limit(metadata: AudioMetadata, settings: Settings) -> None:
    """Apply the product limit. The measurement itself came from the parser."""
    if metadata.duration_seconds > settings.max_audio_duration_seconds:
        limit_minutes = settings.max_audio_duration_seconds / 60
        raise ApiError(
            ErrorCode.AUDIO_TOO_LONG,
            f"Recordings must be {limit_minutes:.0f} minutes or shorter.",
        )


async def process_upload(
    *,
    chunks: AsyncIterator[bytes],
    filename: str | None,
    content_type: str | None,
    settings: Settings,
    storage: RecordingStorage,
    repository: OwnedRecordingRepository,
    owner_id: uuid.UUID,
) -> Recording:
    """Run the upload pipeline and return the stored recording.

    ``content_type`` is recorded for logs only. A client that mislabels its
    upload changes nothing: the detected content decides.

    ``owner_id`` is who the recording belongs to. It is resolved from the
    request before this runs and is the only thing that ever makes the recording
    visible again — there is no unowned recording.

    Raises:
        ApiError: for every rejection a client can act on.
    """
    display_name = sanitize_filename(filename)
    declared_extension = validate_extension(display_name)

    temp_path, size_bytes = await _stream_to_temp_file(
        chunks, max_bytes=settings.max_audio_size_bytes
    )

    try:
        metadata = extract_metadata(temp_path)
        stored_extension = _require_matching_format(declared_extension, metadata)
        _require_duration_within_limit(metadata, settings)

        try:
            stored = storage.save(temp_path, extension=stored_extension)
        except StorageError as exc:
            logger.error("upload_storage_failed", extra={"reason": type(exc).__name__})
            raise ApiError(ErrorCode.INTERNAL_ERROR, _INTERNAL_MESSAGE) from exc

        recording = Recording(
            recording_id=stored.recording_id,
            original_filename=display_name,
            audio_format=metadata.format,
            duration_seconds=metadata.duration_seconds,
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
            size_bytes=stored.size_bytes,
            bits_per_sample=metadata.bits_per_sample,
        )

        try:
            await repository.create(recording, owner_id)
        except Exception as exc:  # noqa: BLE001 - any store failure needs the rollback
            # The bytes are already stored. Roll them back so a failed upload
            # never leaves a recording nobody can reach.
            _rollback(storage, stored.recording_id)
            logger.error("upload_record_failed", extra={"reason": type(exc).__name__})
            raise ApiError(ErrorCode.INTERNAL_ERROR, _INTERNAL_MESSAGE) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    logger.info(
        "upload_stored",
        extra={
            "recording_id": recording.recording_id,
            "format": metadata.format.value,
            "size_bytes": size_bytes,
            "duration_seconds": round(metadata.duration_seconds, 3),
            "declared_content_type": content_type or "unknown",
            "owner_id": str(owner_id),
        },
    )
    return recording


def _rollback(storage: RecordingStorage, recording_id: str) -> None:
    """Best-effort removal of stored bytes after a later step failed."""
    try:
        storage.delete(recording_id)
    except StorageError:  # pragma: no cover - nothing useful left to do
        logger.error("upload_rollback_failed", extra={"recording_id": recording_id})
