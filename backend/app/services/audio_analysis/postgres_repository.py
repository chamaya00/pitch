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

**Not every read wants it, and the ones that do not say so in their return
type.** Each method below comes in two forms: one that returns an
:class:`AudioAnalysis` with the timeline, and one that returns an
:class:`AudioAnalysisSummary` without it. The summary form selects
``document - 'pitch_points'`` and the array's length beside it, so PostgreSQL
drops the points before the row crosses the socket and the count survives.
Measured on a completed analysis of the longest accepted recording — 12 931
points, a 1 596 kB document — through the real driver:

============================================  ========  ===============
read                                          time      peak allocation
============================================  ========  ===============
the whole document (``latest_for_recording``)  83.8 ms      19 151 kB
without the timeline (``latest_summary_…``)     1.8 ms          15 kB
============================================  ========  ===============

47×, on the read the browser polls while a measurement runs. End to end over
HTTP against a real server, the same recording: ``GET /audio-analysis`` went
from 116.7 ms to 8.4 ms and ``GET /audio-analysis/feedback`` from 120.2 ms to
8.6 ms, while ``/pitch`` and ``/key`` are unchanged at ~120 ms and ~114 ms
because they genuinely return or fold the points.

The split is not an optimisation a caller may forget to apply: a summary cannot
be handed to ``update``, because that would re-serialise a document with no
points in it.
"""

from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioFeedbackStatus,
)

#: Everything the record says about itself, minus the timeline, plus its length.
#:
#: ``document - 'pitch_points'`` is evaluated in PostgreSQL, so the points are
#: never sent; ``jsonb_array_length`` reads the same document the server has
#: already decompressed to build the first expression. ``coalesce`` covers a
#: pending record, whose document has no ``pitch_points`` key at all.
_SUMMARY_COLUMNS = """
    document - 'pitch_points' AS document,
    coalesce(jsonb_array_length(document->'pitch_points'), 0) AS pitch_point_count
"""


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
        """Return the record **with its timeline**, or ``None``.

        The expensive read. Use :meth:`summary` unless the points are needed —
        or unless the record is about to be written back, which needs them.
        """

    async def summary(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        """Return the record without its timeline, or ``None``."""

    async def update(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        """Overwrite a record whose stored status is still ``expect_status``.

        Takes the full record rather than a summary on purpose: this replaces
        the stored document, so a caller holding a timeline-free record would
        erase the timeline.

        Raises:
            AudioAnalysisConflictError: the stored status has moved on.
        """

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        """Move feedback generation to ``generating``, if nobody else has.

        Returns the claimed record, or ``None`` when another worker claimed it
        first or it is already written. The claim is one conditional statement
        against stored columns, so it neither reads nor rewrites the timeline.
        """

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        """The most recent audio analysis of a recording, with its timeline."""

    async def latest_summary_for_recording(self, recording_id: str) -> AudioAnalysisSummary | None:
        """The most recent audio analysis of a recording, without its timeline."""

    async def list_summaries_for_recording(self, recording_id: str) -> list[AudioAnalysisSummary]:
        """Every audio analysis of one recording, newest first, without timelines.

        There is deliberately no timeline-carrying counterpart. Nothing needs
        every timeline a recording has ever produced, and a re-analysed
        recording would make one read cost a document per attempt.
        """


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

    async def summary(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                f"SELECT {_SUMMARY_COLUMNS} FROM audio_analyses WHERE id = %s",  # noqa: S608
                (audio_analysis_id,),
            )
        return None if row is None else _to_summary(row)

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

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        async with self._db.transaction() as connection:
            row = await fetch_one(
                connection,
                f"""
                UPDATE audio_analyses
                   SET feedback_status = %s,
                       document = jsonb_set(
                           jsonb_set(
                               jsonb_set(document, '{{feedback_status}}', %s::jsonb),
                               '{{feedback}}', 'null'::jsonb
                           ),
                           '{{feedback_error_code}}', 'null'::jsonb
                       )
                 WHERE id = %s
                   AND status = %s
                   AND feedback_status IN (%s, %s)
                RETURNING {_SUMMARY_COLUMNS}
                """,  # noqa: S608
                (
                    AudioFeedbackStatus.GENERATING.value,
                    f'"{AudioFeedbackStatus.GENERATING.value}"',
                    audio_analysis_id,
                    AudioAnalysisStatus.COMPLETED.value,
                    AudioFeedbackStatus.NOT_REQUESTED.value,
                    AudioFeedbackStatus.FAILED.value,
                ),
            )
        return None if row is None else _to_summary(row)

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

    async def latest_summary_for_recording(self, recording_id: str) -> AudioAnalysisSummary | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                f"""
                SELECT {_SUMMARY_COLUMNS} FROM audio_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC LIMIT 1
                """,  # noqa: S608
                (recording_id,),
            )
        return None if row is None else _to_summary(row)

    async def list_summaries_for_recording(self, recording_id: str) -> list[AudioAnalysisSummary]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                f"""
                SELECT {_SUMMARY_COLUMNS} FROM audio_analyses
                WHERE recording_id = %s ORDER BY created_at DESC, id DESC
                """,  # noqa: S608
                (recording_id,),
            )
        return [_to_summary(row) for row in rows]


def _to_analysis(row: dict[str, Any]) -> AudioAnalysis:
    return AudioAnalysis.model_validate(row["document"])


def _to_summary(row: dict[str, Any]) -> AudioAnalysisSummary:
    """Build a summary from a stripped document and the count selected beside it.

    The count comes from the column rather than from the document, because the
    document handed here no longer has the array it counts — and a document
    written before ``pitch_point_count`` existed never carried one.
    """
    return AudioAnalysisSummary.model_validate(
        {**row["document"], "pitch_point_count": row["pitch_point_count"]}
    )
