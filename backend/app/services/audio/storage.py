"""Filesystem storage for uploaded recordings.

This layer stores and retrieves bytes. It does not decide whether a file is
valid audio (``validation.py`` and ``metadata.py`` do that), it does not record
metadata, and it knows nothing about HTTP. Higher layers coordinate the
workflow; this one guarantees that what lands on disk is complete, contained
and named by the server.

Layout under the configured root::

    <root>/
        recordings/   completed recordings only, named <uuid4-hex>.<ext>
        tmp/          partial writes in progress, never visible as recordings

Keeping partial writes in a sibling directory is what makes the invariant easy
to state and to test: a file inside ``recordings/`` is always a finished
recording.

Failures raise :class:`StorageError` subclasses — internal exceptions for the
API layer to translate. Their messages carry the recording id and a reason but
never a filesystem path, so a careless caller cannot leak the server's layout
to a client.
"""

import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

from app.core.logging import get_logger

logger = get_logger(__name__)

_RECORDINGS_DIRNAME: Final = "recordings"
_TEMP_DIRNAME: Final = "tmp"

#: A recording id is exactly ``uuid4().hex``. Anything else is refused before it
#: reaches the filesystem, so no caller-supplied string can shape a path.
_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{32}\Z")

#: Shape check only — whether ``.wav`` is *audio* is the validation layer's
#: business; this guarantees the extension cannot contain a separator, a dot
#: segment, or anything else that would alter where the file lands.
_EXTENSION_PATTERN: Final = re.compile(r"\A\.[a-z0-9]{1,8}\Z")

#: Owner read/write only. POSIX semantics; see the note in ``_apply_file_mode``.
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700

_COPY_CHUNK_BYTES: Final = 1024 * 1024


class StorageError(Exception):
    """Base class for storage failures."""


class InvalidRecordingIdError(StorageError):
    """The supplied recording id is not a well-formed server-generated id."""


class RecordingNotFoundError(StorageError):
    """No stored recording matches the supplied id."""


class SourceUnavailableError(StorageError):
    """The file handed to :meth:`RecordingStorage.save` could not be read."""


class StorageWriteError(StorageError):
    """The recording could not be written to its destination."""


@dataclass(frozen=True, slots=True)
class StoredRecording:
    """Identity of a recording that is fully written and durable.

    Deliberately carries no path: callers that genuinely need one ask
    :meth:`RecordingStorage.path_for`, so a server path cannot end up in an API
    response by accident.
    """

    recording_id: str
    extension: str
    size_bytes: int


class RecordingStorage:
    """Stores recordings under a configured root directory.

    Construction has no side effects — directories are created on first write —
    so a misconfigured root surfaces as a write failure rather than an import
    time crash, and tests can point at a path that does not exist yet.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # -- Layout ------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def recordings_dir(self) -> Path:
        return self._root / _RECORDINGS_DIRNAME

    @property
    def temp_dir(self) -> Path:
        return self._root / _TEMP_DIRNAME

    def _ensure_directories(self) -> None:
        for directory in (self.recordings_dir, self.temp_dir):
            directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            _apply_mode(directory, _DIR_MODE)

    # -- Identity and containment -----------------------------------------

    @staticmethod
    def _validate_id(recording_id: str) -> str:
        if not isinstance(recording_id, str) or not _ID_PATTERN.match(recording_id):
            raise InvalidRecordingIdError("recording id is not a well-formed identifier")
        return recording_id

    @staticmethod
    def _validate_extension(extension: str) -> str:
        normalised = extension.lower()
        if not _EXTENSION_PATTERN.match(normalised):
            raise StorageWriteError("refusing to store a file with an unusable extension")
        return normalised

    def _contained(self, candidate: Path) -> Path:
        """Return ``candidate`` only if it really sits inside the recordings dir.

        The ids are server-generated and already pattern-checked, so this should
        never fire — which is exactly why it is worth asserting. It also closes
        the symlink case: ``resolve()`` follows links, so an entry pointing
        outside the root fails containment instead of being read.
        """
        root = self.recordings_dir.resolve()
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            logger.warning("storage_path_escaped_root")
            raise InvalidRecordingIdError("resolved path escaped the storage root")
        return resolved

    def _locate(self, recording_id: str) -> Path:
        """Find the stored file for an id, whatever extension it was saved with."""
        self._validate_id(recording_id)
        match = next(self.recordings_dir.glob(f"{recording_id}.*"), None)
        if match is None:
            raise RecordingNotFoundError(f"no recording stored for id {recording_id}")
        return self._contained(match)

    # -- Public API --------------------------------------------------------

    def save(self, source: Path, *, extension: str) -> StoredRecording:
        """Copy an already-validated file into storage under a new id.

        The caller keeps ownership of ``source``: it is read, never moved or
        removed, so the caller's own cleanup stays unconditional and a failure
        here leaves its temporary file intact for retry or inspection.

        Raises:
            SourceUnavailableError: ``source`` is missing or unreadable.
            StorageWriteError: the destination could not be written.
        """
        normalised_extension = self._validate_extension(extension)
        recording_id = uuid.uuid4().hex
        destination = self._contained(self.recordings_dir / f"{recording_id}{normalised_extension}")

        try:
            self._ensure_directories()
        except OSError as exc:
            raise StorageWriteError("storage directories are unavailable") from exc

        try:
            handle = source.open("rb")
        except OSError as exc:
            raise SourceUnavailableError("source file could not be opened") from exc

        with handle:
            size_bytes = self._write_atomically(handle, destination)

        logger.info(
            "recording_stored",
            extra={
                "recording_id": recording_id,
                "extension": normalised_extension,
                "size_bytes": size_bytes,
            },
        )
        return StoredRecording(
            recording_id=recording_id,
            extension=normalised_extension,
            size_bytes=size_bytes,
        )

    def _write_atomically(self, source: BinaryIO, destination: Path) -> int:
        """Write to a temp file, fsync it, then rename it into place.

        ``os.replace`` is atomic within a filesystem, and the temp file is made
        inside the storage root so that guarantee holds. Nothing appears in
        ``recordings/`` until the bytes are on disk; any failure removes the
        partial file, so a half-written upload can never look like a recording.
        """
        descriptor, temp_name = tempfile.mkstemp(dir=self.temp_dir, prefix="incoming-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                shutil.copyfileobj(source, target, _COPY_CHUNK_BYTES)
                target.flush()
                os.fsync(target.fileno())

            _apply_mode(temp_path, _FILE_MODE)
            size_bytes = temp_path.stat().st_size
            os.replace(temp_path, destination)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            logger.warning("recording_store_failed", extra={"reason": type(exc).__name__})
            raise StorageWriteError("recording could not be written to storage") from exc
        except BaseException:
            # Covers cancellation and anything else non-OSError: still no litter.
            temp_path.unlink(missing_ok=True)
            raise

        # Make the rename itself durable, not just the file contents. Not every
        # platform allows opening a directory, so this is best effort.
        _sync_directory(destination.parent)
        return size_bytes

    def path_for(self, recording_id: str) -> Path:
        """Absolute path of a stored recording.

        Raises:
            InvalidRecordingIdError: the id is malformed.
            RecordingNotFoundError: nothing is stored under that id.
        """
        return self._locate(recording_id)

    def exists(self, recording_id: str) -> bool:
        """Whether a recording is stored. A malformed id is simply absent."""
        try:
            self._locate(recording_id)
        except StorageError:
            return False
        return True

    def open_recording(self, recording_id: str) -> BinaryIO:
        """Open a stored recording for reading. The caller closes the handle."""
        path = self._locate(recording_id)
        try:
            return path.open("rb")
        except OSError as exc:
            raise RecordingNotFoundError(f"recording {recording_id} could not be opened") from exc

    def delete(self, recording_id: str) -> bool:
        """Remove a stored recording.

        Idempotent: returns ``True`` when a file was removed and ``False`` when
        there was nothing to remove, so repeated cleanup is not an error. A
        malformed id raises rather than silently reporting ``False``.
        """
        self._validate_id(recording_id)
        try:
            path = self._locate(recording_id)
        except RecordingNotFoundError:
            return False

        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageWriteError(f"recording {recording_id} could not be deleted") from exc

        logger.info("recording_deleted", extra={"recording_id": recording_id})
        return True


def _apply_mode(path: Path, mode: int) -> None:
    """Apply POSIX permissions, tolerating platforms that do not honour them.

    On Windows only the read-only flag is meaningful and access is governed by
    ACLs instead, so a failure here is not fatal — the restrictive mode is a
    hardening measure on POSIX, not a correctness requirement.
    """
    try:
        os.chmod(path, mode)
    except (NotImplementedError, OSError):  # pragma: no cover - platform dependent
        logger.debug("storage_chmod_unsupported")


def _sync_directory(directory: Path) -> None:
    """fsync a directory so a rename survives a crash, where supported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - Windows cannot open a directory
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - not supported on every filesystem
        pass
    finally:
        os.close(descriptor)
