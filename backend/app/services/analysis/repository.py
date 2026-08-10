"""Persistence for analysis records.

Mirrors ``services/recordings/repository.py`` deliberately: the orchestration
layer depends on the :class:`AnalysisRepository` protocol, never on an
implementation, so a database-backed store can replace this without the service
changing. Nothing here knows about HTTP or about providers.

Layout under the configured root::

    <root>/
        analyses/     one JSON document per analysis, named <analysis_id>.json

Writes go through the same discipline as recordings: temporary file → write →
flush → fsync → atomic rename, so a reader never sees a half-written record and
a crashed write leaves nothing behind.

Two things separate this from the recordings repository, both driven by what
orchestration needs:

* ``create`` refuses to overwrite. An analysis is written several times as it
  progresses, so "create" and "update" have to mean different things — a
  ``create`` that silently clobbered an in-flight analysis would destroy a
  transcript that cost a paid provider call.
* ``list_for_recording`` exists, because the service has to be able to ask
  whether a recording already has an analysis running. It is a directory scan
  and it is *not* an index — see its docstring.

Like its sibling, this is the smallest thing that makes an analysis id
meaningful after the response is sent. It is not a database.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.analysis.models import Analysis

logger = get_logger(__name__)

_ANALYSES_DIRNAME: Final = "analyses"
_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{32}\Z")
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700


class AnalysisRepositoryError(Exception):
    """An analysis record could not be read or written."""


class InvalidAnalysisIdError(AnalysisRepositoryError):
    """The supplied id is not a well-formed identifier."""


class AnalysisNotFoundError(AnalysisRepositoryError):
    """No record exists to update."""


class AnalysisAlreadyExistsError(AnalysisRepositoryError):
    """A record already exists under that id."""


@runtime_checkable
class AnalysisRepository(Protocol):
    """Storage-agnostic interface for analysis records."""

    def create(self, analysis: Analysis) -> Analysis:
        """Persist a new record and return it.

        Raises:
            AnalysisAlreadyExistsError: a record already exists under that id.
        """

    def get(self, analysis_id: str) -> Analysis | None:
        """Return the record, or ``None`` if it is unknown."""

    def update(self, analysis: Analysis) -> Analysis:
        """Overwrite an existing record and return it.

        Raises:
            AnalysisNotFoundError: no record exists under that id.
        """

    def delete(self, analysis_id: str) -> bool:
        """Remove a record. ``True`` if one was removed."""

    def list_for_recording(self, recording_id: str) -> list[Analysis]:
        """Every analysis of one recording, newest first."""


class JsonFileAnalysisRepository:
    """Filesystem-backed repository storing one JSON document per analysis."""

    def __init__(self, root: Path) -> None:
        self._directory = Path(root) / _ANALYSES_DIRNAME

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def _validate_id(analysis_id: str) -> str:
        if not _ID_PATTERN.match(analysis_id):
            raise InvalidAnalysisIdError("analysis id is not a well-formed identifier")
        return analysis_id

    def _path_for(self, analysis_id: str) -> Path:
        """Build the document path from a pattern-checked id.

        The id is server-generated, but it is validated here anyway rather than
        trusted — the same reasoning as in ``RecordingStorage``.
        """
        return self._directory / f"{self._validate_id(analysis_id)}.json"

    def _write(self, analysis: Analysis, *, overwrite: bool) -> Analysis:
        path = self._path_for(analysis.analysis_id)
        payload = analysis.model_dump_json()

        try:
            self._directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(dir=self._directory, prefix="analysis-")
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
            raise AnalysisAlreadyExistsError(
                f"analysis {analysis.analysis_id} already exists"
            ) from exc
        except OSError as exc:
            logger.warning("analysis_record_write_failed", extra={"reason": type(exc).__name__})
            raise AnalysisRepositoryError("analysis record could not be written") from exc

        return analysis

    def create(self, analysis: Analysis) -> Analysis:
        return self._write(analysis, overwrite=False)

    def update(self, analysis: Analysis) -> Analysis:
        if not self._path_for(analysis.analysis_id).exists():
            raise AnalysisNotFoundError(f"analysis {analysis.analysis_id} does not exist")
        return self._write(analysis, overwrite=True)

    def get(self, analysis_id: str) -> Analysis | None:
        try:
            path = self._path_for(analysis_id)
        except InvalidAnalysisIdError:
            return None
        return self._read(path, analysis_id=analysis_id)

    def _read(self, path: Path, *, analysis_id: str) -> Analysis | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AnalysisRepositoryError("analysis record could not be read") from exc

        try:
            return Analysis.model_validate_json(raw)
        except ValueError as exc:
            # A corrupt document is a fault on our side, not a missing record.
            logger.warning("analysis_record_unreadable", extra={"analysis_id": analysis_id})
            raise AnalysisRepositoryError("analysis record is not readable") from exc

    def delete(self, analysis_id: str) -> bool:
        try:
            path = self._path_for(analysis_id)
        except InvalidAnalysisIdError:
            return False

        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AnalysisRepositoryError("analysis record could not be deleted") from exc
        return True

    def list_for_recording(self, recording_id: str) -> list[Analysis]:
        """Every analysis of one recording, newest first.

        This reads and parses every stored analysis. It is a scan, not an
        index, and it is the clearest sign that this store is a stopgap: the
        cost grows with every analysis ever run. It is acceptable because the
        alternative — an index file to keep consistent with the documents
        beside it — is a database with extra steps, and a real one lands later.

        In-progress temporary files are skipped by name, and an unreadable
        document is skipped with a warning rather than failing the whole query:
        one corrupt record must not make a recording permanently unanalysable.
        """
        try:
            self._validate_id(recording_id)
        except InvalidAnalysisIdError:
            return []
        if not self._directory.is_dir():
            return []

        found: list[Analysis] = []
        for path in sorted(self._directory.glob("*.json")):
            if not _ID_PATTERN.match(path.stem):
                continue
            try:
                analysis = self._read(path, analysis_id=path.stem)
            except AnalysisRepositoryError:
                continue
            if analysis is not None and analysis.recording_id == recording_id:
                found.append(analysis)

        return sorted(found, key=lambda item: item.created_at, reverse=True)
