"""Persistence for recording metadata.

The endpoint depends on the :class:`RecordingRepository` protocol, never on an
implementation, so Phase 7 can introduce a PostgreSQL-backed repository without
touching the route. Nothing here knows about HTTP.

The implementation below writes one JSON document per recording. That is
deliberately the smallest thing that makes a recording id meaningful after the
response is sent — it is not a database, and it is not meant to grow into one:
no querying, no indexes, no relations, no concurrent-update story beyond
last-writer-wins on a single document.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Final, Protocol

from app.core.logging import get_logger
from app.services.recordings.models import Recording

logger = get_logger(__name__)

_METADATA_DIRNAME: Final = "metadata"
_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{32}\Z")
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700


class RepositoryError(Exception):
    """A recording record could not be read or written."""


class InvalidRecordingIdError(RepositoryError):
    """The supplied id is not a well-formed identifier."""


class RecordingRepository(Protocol):
    """Storage-agnostic interface for recording metadata."""

    def create(self, recording: Recording) -> Recording:
        """Persist a new recording record and return it."""

    def get(self, recording_id: str) -> Recording | None:
        """Return the recording, or ``None`` if it is unknown."""

    def delete(self, recording_id: str) -> bool:
        """Remove a record. ``True`` if one was removed."""


class JsonFileRecordingRepository:
    """Filesystem-backed repository storing one JSON document per recording."""

    def __init__(self, root: Path) -> None:
        self._directory = Path(root) / _METADATA_DIRNAME

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def _validate_id(recording_id: str) -> str:
        if not _ID_PATTERN.match(recording_id):
            raise InvalidRecordingIdError("recording id is not a well-formed identifier")
        return recording_id

    def _path_for(self, recording_id: str) -> Path:
        """Build the document path from a pattern-checked id.

        The id is server-generated, but it is validated here anyway rather than
        trusted — the same reasoning as in ``RecordingStorage``.
        """
        return self._directory / f"{self._validate_id(recording_id)}.json"

    def create(self, recording: Recording) -> Recording:
        path = self._path_for(recording.recording_id)
        payload = recording.model_dump_json()

        try:
            self._directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(dir=self._directory, prefix="record-")
            temp_path = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_path, _FILE_MODE)
                # Same discipline as recording bytes: a reader never sees a
                # half-written record.
                os.replace(temp_path, path)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            logger.warning("recording_record_write_failed", extra={"reason": type(exc).__name__})
            raise RepositoryError("recording record could not be written") from exc

        return recording

    def get(self, recording_id: str) -> Recording | None:
        try:
            path = self._path_for(recording_id)
        except InvalidRecordingIdError:
            return None

        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RepositoryError("recording record could not be read") from exc

        try:
            return Recording.model_validate_json(raw)
        except ValueError as exc:
            # A corrupt document is a fault on our side, not a missing record.
            logger.warning("recording_record_unreadable", extra={"recording_id": recording_id})
            raise RepositoryError("recording record is not readable") from exc

    def delete(self, recording_id: str) -> bool:
        try:
            path = self._path_for(recording_id)
        except InvalidRecordingIdError:
            return False

        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RepositoryError("recording record could not be deleted") from exc
        return True
