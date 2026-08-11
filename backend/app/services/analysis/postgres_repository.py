"""Speech analyses in PostgreSQL.

The reason this step exists, in one sentence: **the constraint that stops two
analyses running for one recording is now in the database, not in a
process-local lock.**

``speech_analyses_one_active_idx`` is a partial unique index over
``recording_id`` where the status is not terminal. Two workers that both decide
to start an analysis produce one insert and one unique violation — in different
processes, on different machines, however many of them there are. The
``asyncio.Lock`` that used to guard this could only ever serialise coroutines
inside one interpreter, and its own docstring said so.

The second guarantee is that a stale worker cannot overwrite a finished result.
Every update carries the status the caller last read, and the ``WHERE`` clause
requires the stored row to still say that. A worker that resumes after its
record was swept to ``failed`` finds nothing to update and says so, rather than
writing a "completed" over the failure.

The domain object is stored as JSONB and is authoritative; the columns beside it
exist so queries have something indexed to work with, and are written from the
same object in the same statement.
"""

from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.analysis.models import Analysis, AnalysisStatus


class AnalysisConflictError(Exception):
    """Another worker owns this analysis, or already finished it.

    Raised where a conditional update matched no rows. Not an error in the
    ordinary sense — it is how a caller learns it lost a race, and the correct
    response is usually to re-read and return what is actually stored.
    """


class ActiveAnalysisExistsError(Exception):
    """An analysis for this recording is already in flight."""


@runtime_checkable
class AsyncAnalysisRepository(Protocol):
    """Storage-agnostic interface for speech-analysis records."""

    async def create(self, analysis: Analysis) -> Analysis:
        """Persist a new record.

        Raises:
            ActiveAnalysisExistsError: one is already in flight for this
                recording. Enforced by the database, so it holds across
                processes.
        """

    async def get(self, analysis_id: str) -> Analysis | None:
        """Return the record, or ``None``."""

    async def update(self, analysis: Analysis, *, expect_status: AnalysisStatus) -> Analysis:
        """Overwrite a record whose stored status is still ``expect_status``.

        Raises:
            AnalysisConflictError: the stored status has moved on, so another
                worker is handling this record.
        """

    async def latest_for_recording(self, recording_id: str) -> Analysis | None:
        """The most recent analysis of a recording, whatever state it is in."""

    async def list_for_recording(self, recording_id: str) -> list[Analysis]:
        """Every analysis of one recording, newest first. An indexed query."""


class PostgresAnalysisRepository:
    """Speech analyses in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(self, analysis: Analysis) -> Analysis:
        async with self._db.transaction() as connection:
            try:
                await execute(
                    connection,
                    """
                    INSERT INTO speech_analyses
                        (id, recording_id, status, created_at, error_code, document)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        analysis.analysis_id,
                        analysis.recording_id,
                        analysis.status.value,
                        analysis.created_at,
                        analysis.error_code.value if analysis.error_code else None,
                        analysis.model_dump_json(),
                    ),
                )
            except errors.UniqueViolation as exc:
                raise ActiveAnalysisExistsError(
                    f"an analysis of {analysis.recording_id} is already in flight"
                ) from exc
        return analysis

    async def get(self, analysis_id: str) -> Analysis | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection, "SELECT document FROM speech_analyses WHERE id = %s", (analysis_id,)
            )
        return None if row is None else _to_analysis(row)

    async def update(self, analysis: Analysis, *, expect_status: AnalysisStatus) -> Analysis:
        async with self._db.transaction() as connection:
            affected = await execute(
                connection,
                """
                UPDATE speech_analyses
                   SET status = %s, error_code = %s, document = %s
                 WHERE id = %s AND status = %s
                """,
                (
                    analysis.status.value,
                    analysis.error_code.value if analysis.error_code else None,
                    analysis.model_dump_json(),
                    analysis.analysis_id,
                    expect_status.value,
                ),
            )
        if affected == 0:
            raise AnalysisConflictError(
                f"analysis {analysis.analysis_id} is no longer {expect_status.value}"
            )
        return analysis

    async def latest_for_recording(self, recording_id: str) -> Analysis | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT document FROM speech_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (recording_id,),
            )
        return None if row is None else _to_analysis(row)

    async def list_for_recording(self, recording_id: str) -> list[Analysis]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT document FROM speech_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC
                """,
                (recording_id,),
            )
        return [_to_analysis(row) for row in rows]


def _to_analysis(row: dict[str, Any]) -> Analysis:
    document = dict(row["document"])
    # ``provenance`` is a computed field derived from the content; it is
    # serialised into the document and must not be fed back in as an input.
    document.pop("provenance", None)
    return Analysis.model_validate(document)
