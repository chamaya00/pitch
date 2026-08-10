"""Persistence for audio-analysis records.

Mirrors ``services/analysis/repository.py`` deliberately, which in turn mirrors
``services/recordings/repository.py``: same atomic-write discipline, same
protocol-first shape, same directory-scan lookup. Orchestration depends on the
:class:`AudioAnalysisRepository` protocol and never on this implementation.

Layout under the configured root::

    <root>/
        audio-analyses/   one JSON document per analysis, named <id>.json

A separate directory from ``analyses/`` on purpose. Speech analysis and audio
analysis are different measurements of the same recording with different
lifecycles — one can be re-run without touching the other — and sharing a
document would couple them for no gain.

**This is the third copy of this write discipline.** It is duplication, and it
is chosen over abstracting the three because the store is a stopgap: a real
database replaces all of it, and a generic document-store abstraction built now
would be a layer to delete later. Noted here so the cost is visible rather than
discovered.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.audio_analysis.models import AudioAnalysis

logger = get_logger(__name__)

_DIRNAME: Final = "audio-analyses"
_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{32}\Z")
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700


class AudioAnalysisRepositoryError(Exception):
    """An audio-analysis record could not be read or written."""


class InvalidAudioAnalysisIdError(AudioAnalysisRepositoryError):
    """The supplied id is not a well-formed identifier."""


class AudioAnalysisNotFoundError(AudioAnalysisRepositoryError):
    """No record exists to update."""


class AudioAnalysisAlreadyExistsError(AudioAnalysisRepositoryError):
    """A record already exists under that id."""


@runtime_checkable
class AudioAnalysisRepository(Protocol):
    """Storage-agnostic interface for audio-analysis records."""

    def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        """Persist a new record and return it.

        Raises:
            AudioAnalysisAlreadyExistsError: a record already exists.
        """

    def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        """Return the record, or ``None`` if it is unknown."""

    def update(self, analysis: AudioAnalysis) -> AudioAnalysis:
        """Overwrite an existing record and return it.

        Raises:
            AudioAnalysisNotFoundError: no record exists under that id.
        """

    def delete(self, audio_analysis_id: str) -> bool:
        """Remove a record. ``True`` if one was removed."""

    def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        """Every audio analysis of one recording, newest first."""


class JsonFileAudioAnalysisRepository:
    """Filesystem-backed repository storing one JSON document per analysis."""

    def __init__(self, root: Path) -> None:
        self._directory = Path(root) / _DIRNAME

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def _validate_id(audio_analysis_id: str) -> str:
        if not _ID_PATTERN.match(audio_analysis_id):
            raise InvalidAudioAnalysisIdError("id is not a well-formed identifier")
        return audio_analysis_id

    def _path_for(self, audio_analysis_id: str) -> Path:
        """Build the document path from a pattern-checked id.

        Server-generated, and validated here anyway rather than trusted — the
        same reasoning as in ``RecordingStorage``.
        """
        return self._directory / f"{self._validate_id(audio_analysis_id)}.json"

    def _write(self, analysis: AudioAnalysis, *, overwrite: bool) -> AudioAnalysis:
        path = self._path_for(analysis.audio_analysis_id)
        payload = analysis.model_dump_json()

        try:
            self._directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(dir=self._directory, prefix="audio-")
            temp_path = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_path, _FILE_MODE)
                if overwrite:
                    # A reader never sees a half-written record.
                    os.replace(temp_path, path)
                else:
                    # ``link`` is atomic and refuses an existing destination,
                    # which is what makes create and update distinguishable.
                    os.link(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)
        except FileExistsError as exc:
            raise AudioAnalysisAlreadyExistsError(
                f"audio analysis {analysis.audio_analysis_id} already exists"
            ) from exc
        except OSError as exc:
            logger.warning("audio_analysis_write_failed", extra={"reason": type(exc).__name__})
            raise AudioAnalysisRepositoryError("record could not be written") from exc

        return analysis

    def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        return self._write(analysis, overwrite=False)

    def update(self, analysis: AudioAnalysis) -> AudioAnalysis:
        if not self._path_for(analysis.audio_analysis_id).exists():
            raise AudioAnalysisNotFoundError(
                f"audio analysis {analysis.audio_analysis_id} does not exist"
            )
        return self._write(analysis, overwrite=True)

    def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        try:
            path = self._path_for(audio_analysis_id)
        except InvalidAudioAnalysisIdError:
            return None
        return self._read(path, audio_analysis_id=audio_analysis_id)

    def _read(self, path: Path, *, audio_analysis_id: str) -> AudioAnalysis | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AudioAnalysisRepositoryError("record could not be read") from exc

        try:
            return AudioAnalysis.model_validate_json(raw)
        except ValueError as exc:
            # A corrupt document is a fault on our side, not a missing record.
            logger.warning(
                "audio_analysis_unreadable", extra={"audio_analysis_id": audio_analysis_id}
            )
            raise AudioAnalysisRepositoryError("record is not readable") from exc

    def delete(self, audio_analysis_id: str) -> bool:
        try:
            path = self._path_for(audio_analysis_id)
        except InvalidAudioAnalysisIdError:
            return False

        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AudioAnalysisRepositoryError("record could not be deleted") from exc
        return True

    def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        """Every audio analysis of one recording, newest first.

        A scan, not an index — see the note in ``services/analysis/repository``.
        An unreadable document is skipped with a warning rather than failing the
        whole query: one corrupt record must not make a recording permanently
        unanalysable.
        """
        try:
            self._validate_id(recording_id)
        except InvalidAudioAnalysisIdError:
            return []
        if not self._directory.is_dir():
            return []

        found: list[AudioAnalysis] = []
        for path in sorted(self._directory.glob("*.json")):
            if not _ID_PATTERN.match(path.stem):
                continue
            try:
                analysis = self._read(path, audio_analysis_id=path.stem)
            except AudioAnalysisRepositoryError:
                continue
            if analysis is not None and analysis.recording_id == recording_id:
                found.append(analysis)

        return sorted(found, key=lambda item: item.created_at, reverse=True)
