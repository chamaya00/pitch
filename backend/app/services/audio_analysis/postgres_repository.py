"""Audio analyses in PostgreSQL.

Mirrors ``services/analysis/postgres_repository.py``, with one addition the
speech side does not need: **feedback generation is claimed atomically.**

A partial unique index cannot express "only one feedback run per analysis",
because the row already exists and its analysis status does not change while
feedback is written. :meth:`PostgresAudioAnalysisRepository.claim_feedback` is
therefore a single conditional ``UPDATE`` that moves ``feedback_status`` from
``not_requested`` or ``failed`` to ``generating`` and returns the row only if it
was the one that moved it. Two concurrent requests produce one claim and one
``None``, in one statement, with no application lock involved — which matters
here more than anywhere else, because a duplicate is a paid provider call.

The pitch timeline lives inside the stored document. It is written once, read
whole and never queried by predicate, so a table of one row per frame would be
~13 000 rows per recording supporting a query nobody issues. The note breakdown
is still derived from those points in Python by code that already exists and is
tested — the same architecture as before, now reading from a different store.
"""

from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
)


class AudioAnalysisConflictError(Exception):
    """Another worker owns this analysis, or already finished it."""


class ActiveAudioAnalysisExistsError(Exception):
    """An audio analysis for this recording is already in flight."""


@runtime_checkable
class AsyncAudioAnalysisRepository(Protocol):
    """Storage-agnostic interface for audio-analysis records."""

    async def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        """Persist a new record.

        Raises:
            ActiveAudioAnalysisExistsError: one is already in flight.
        """

    async def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        """Return the record, or ``None``."""

    async def update(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        """Overwrite a record whose stored status is still ``expect_status``.

        Raises:
            AudioAnalysisConflictError: the stored status has moved on.
        """

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysis | None:
        """Move feedback generation to ``generating``, if nobody else has.

        Returns the claimed record, or ``None`` when another worker claimed it
        first or it is already written.
        """

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        """The most recent audio analysis of a recording."""

    async def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        """Every audio analysis of one recording, newest first."""


class PostgresAudioAnalysisRepository:
    """Audio analyses in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        async with self._db.transaction() as connection:
            try:
                await execute(
                    connection,
                    """
                    INSERT INTO audio_analyses
                        (id, recording_id, status, feedback_status, created_at,
                         error_code, document)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        analysis.audio_analysis_id,
                        analysis.recording_id,
                        analysis.status.value,
                        analysis.feedback_status.value,
                        analysis.created_at,
                        analysis.error_code.value if analysis.error_code else None,
                        analysis.model_dump_json(),
                    ),
                )
            except errors.UniqueViolation as exc:
                raise ActiveAudioAnalysisExistsError(
                    f"an audio analysis of {analysis.recording_id} is already in flight"
                ) from exc
        return analysis

    async def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT document FROM audio_analyses WHERE id = %s",
                (audio_analysis_id,),
            )
        return None if row is None else _to_analysis(row)

    async def update(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        async with self._db.transaction() as connection:
            affected = await execute(
                connection,
                """
                UPDATE audio_analyses
                   SET status = %s, feedback_status = %s, error_code = %s, document = %s
                 WHERE id = %s AND status = %s
                """,
                (
                    analysis.status.value,
                    analysis.feedback_status.value,
                    analysis.error_code.value if analysis.error_code else None,
                    analysis.model_dump_json(),
                    analysis.audio_analysis_id,
                    expect_status.value,
                ),
            )
        if affected == 0:
            raise AudioAnalysisConflictError(
                f"audio analysis {analysis.audio_analysis_id} is no longer {expect_status.value}"
            )
        return analysis

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysis | None:
        async with self._db.transaction() as connection:
            row = await fetch_one(
                connection,
                """
                UPDATE audio_analyses
                   SET feedback_status = %s,
                       document = jsonb_set(
                           jsonb_set(
                               jsonb_set(document, '{feedback_status}', %s::jsonb),
                               '{feedback}', 'null'::jsonb
                           ),
                           '{feedback_error_code}', 'null'::jsonb
                       )
                 WHERE id = %s
                   AND status = %s
                   AND feedback_status IN (%s, %s)
                RETURNING document
                """,
                (
                    AudioFeedbackStatus.GENERATING.value,
                    f'"{AudioFeedbackStatus.GENERATING.value}"',
                    audio_analysis_id,
                    AudioAnalysisStatus.COMPLETED.value,
                    AudioFeedbackStatus.NOT_REQUESTED.value,
                    AudioFeedbackStatus.FAILED.value,
                ),
            )
        return None if row is None else _to_analysis(row)

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT document FROM audio_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (recording_id,),
            )
        return None if row is None else _to_analysis(row)

    async def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT document FROM audio_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC
                """,
                (recording_id,),
            )
        return [_to_analysis(row) for row in rows]


def _to_analysis(row: dict[str, Any]) -> AudioAnalysis:
    return AudioAnalysis.model_validate(row["document"])
