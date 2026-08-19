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
type.** There are four forms, and each is a type rather than a convention:

* :class:`AudioAnalysisSummary` — no timeline, plus ``pitch_point_count`` (10.11)
* :class:`DecimatedTimeline` — a summary, plus every ``n``-th point (10.14)
* :class:`TimelineFields` — a summary, plus two fields of every frame (10.15)
* :class:`AudioAnalysis` — a summary, plus whole frames

Only the last can be written back, and only writes ask for it. The summary form
selects ``document - 'pitch_points'``, so PostgreSQL drops the points before the
row crosses the socket. Measured on a completed analysis of the longest accepted
recording — 12 931 points, a 1 596 kB document — through the real driver:

============================================  ========  ===============
read                                          time      peak allocation
============================================  ========  ===============
the whole document (``latest_for_recording``)  83.8 ms      19 151 kB
without the timeline (``latest_summary_…``)     1.8 ms          15 kB
============================================  ========  ===============

47×, on the read the browser polls while a measurement runs. End to end over
HTTP against a real server, the same recording: ``GET /audio-analysis`` went
from 116.7 ms to 8.4 ms and ``GET /audio-analysis/feedback`` from 120.2 ms to
8.6 ms, while ``/pitch`` and ``/key`` were unchanged at ~120 ms and ~114 ms
because they genuinely returned or folded the points. Both were fixed later, by
reading something narrower rather than by loading less of the record: ``/pitch``
a sample of the timeline (10.14), ``/key`` and ``/notes`` the two fields they
fold (10.15).

The split is not an optimisation a caller may forget to apply: a summary cannot
be handed to ``update``, because that would re-serialise a document with no
points in it.

**How many points there are is a column, not an expression.** 10.11 answered
that question with ``jsonb_array_length(document->'pitch_points')`` selected
beside the stripped document, believing it read a document PostgreSQL had
already decompressed for the first expression. Step 10.12 measured it: a
toasted document is detoasted once *per expression*, so the count cost a second
full decompression — 5.70 ms and 109 buffers for the pair, against 1.80 ms and
37 for the strip alone. ``pitch_point_count`` (migration 0005) is written from
the same object in the same statement as ``status`` and ``feedback_status``, so
it cannot drift from the timeline it counts, and it answers for documents
written before 10.11 — which carry no count of their own and were the reason
the expression existed.
"""

from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioFeedbackStatus,
    DecimatedTimeline,
    PitchFields,
    PitchPoint,
    TimelineFields,
)

#: Everything the record says about itself, minus the timeline, plus its length.
#:
#: ``document - 'pitch_points'`` is evaluated in PostgreSQL, so the points are
#: never sent. It is the **only** expression here that touches the document,
#: which is the point: each one that did would decompress the whole thing again.
#: The count is the stored column, written beside the document rather than read
#: back out of it.
_SUMMARY_COLUMNS = """
    document - 'pitch_points' AS document,
    pitch_point_count
"""

#: The timeline read, sampled in PostgreSQL rather than in Python.
#:
#: ``GET …/audio-analysis/pitch`` returns at most ``max_points`` points and has
#: defaulted to a thousand since Step 7I, because a graph a few hundred pixels
#: wide cannot draw more. It nevertheless used to parse the whole stored
#: timeline and throw 92% of it away: 1 685 kB across the socket, 20 ms of
#: ``json.loads`` and 30 ms of pydantic validation to build 12 931 points, of
#: which 995 were returned. Every millisecond of that ran on the event loop, so
#: it was not one slow request — it was every other request in the process
#: waiting. Step 10.14 measured what that costs under concurrency; the numbers
#: and the experiment are in ``docs/architecture.md``.
#:
#: **PostgreSQL's half of the work does not get cheaper, and that is the point.**
#: Detoasting and parsing the stored jsonb costs it ~20 ms either way — 19.1 ms
#: for this statement against 21.4 ms to hand over the whole document. What
#: changes is what crosses the socket and what the API process then does with
#: it: 133 kB parsed in ~4 ms, rather than 1 685 kB turned into 12 931 validated
#: points in ~50 ms. PostgreSQL does its 20 ms in a backend process per
#: connection; the event loop does its 50 ms in front of every other request in
#: the process. End to end the read went from 172.9 ms to 40.8 ms, and from 7.1
#: to 20.9 requests a second.
#:
#: Three details are deliberate:
#:
#: * **``WITH ORDINALITY``, not ``generate_series`` over subscripts.** They cost
#:   the same (17.7 ms against 17.7 ms), and this one walks the array that is
#:   actually stored. Indexing up to ``pitch_point_count - 1`` would silently
#:   drop the end of a recording if the count and the array ever disagreed.
#: * **The stride is computed from the stored count in the same statement**, so
#:   the sample and the total it is a sample of are read from one row. Deciding
#:   it in Python would take a second read, and two reads can straddle a
#:   re-analysis — the window Phase 8 slice 5 and Step 10.11 both closed.
#: * **The CTE picks the row, not the document.** It selects the id and the
#:   count, and the document is reached once the row is chosen. Carrying the
#:   document through the CTE measured the same (19.1 ms against 19.6 ms), and
#:   this way the statement has exactly two references to it — the strip and the
#:   expansion, both of which have to decompress — so counting them is a
#:   meaningful check rather than one confused by a pass-through.
_DECIMATED_SQL = """
    WITH latest AS (
        SELECT id,
               pitch_point_count,
               GREATEST(1, CEIL(pitch_point_count::numeric / %s))::int AS decimation
        FROM audio_analyses
        WHERE recording_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    )
    SELECT analysis.document - 'pitch_points' AS document,
           latest.pitch_point_count,
           latest.decimation,
           COALESCE(
               (
                   SELECT jsonb_agg(point ORDER BY ordinality)
                   FROM jsonb_array_elements(analysis.document -> 'pitch_points')
                        WITH ORDINALITY AS sampled(point, ordinality)
                   WHERE (ordinality - 1) %% latest.decimation = 0
               ),
               '[]'::jsonb
           ) AS pitch_points
    FROM latest
    JOIN audio_analyses AS analysis ON analysis.id = latest.id
"""


#: The two aggregations' read: every frame, but only the fields they fold.
#:
#: ``GET …/audio-analysis/notes`` and ``…/key`` are the only reads left that
#: touch every stored point, and unlike the graph they genuinely need every one:
#: a breakdown of a sample is a breakdown of a different recording. What they do
#: not need is the *frame*. Of the six fields a stored point carries they read
#: two — the semitone and the deviation from it — and Step 10.14 measured what
#: building the other four cost: of ~80 ms per request, 23 ms was SQL and
#: transfer, 20 ms ``json.loads`` and **30 ms pydantic building 12 931
#: ``PitchPoint`` models**, against folds of 3.3 ms and 1.4 ms. All of it on the
#: event loop, so the two reads served ~7 requests a second however many clients
#: asked, and a client with an analysis page open pushed the poll behind it from
#: 14.2 ms to 301.0 ms.
#:
#: PostgreSQL projects the two fields into arrays now. Four details are
#: deliberate:
#:
#: * **One walk of the array, two aggregates.** Both fields come from the same
#:   ``jsonb_array_elements`` expansion inside one lateral, so the statement has
#:   exactly two references to ``document`` — the strip and the expansion — and
#:   the 10.12 rule holds: a third reference would be a third full detoast of a
#:   1 685 kB document — which is what one ``jsonb_path_query_array`` per field
#:   would have cost, three references in place of two.
#: * **``array_agg`` into typed arrays, not ``jsonb_agg``.** The values arrive as
#:   an ``int[]`` and a ``float8[]`` the driver decodes directly, rather than as
#:   a jsonb array the API process has to parse: 26.0 ms against 28.1 ms end to
#:   end, and it is the cast that decides what a bad value does — a stored note
#:   that is not a number fails in PostgreSQL rather than becoming one.
#: * **Both aggregates are ordered by the same ordinality.** Neither fold cares
#:   what order the frames arrive in, but ``cents[i]`` must be the deviation of
#:   ``midi_notes[i]``: two aggregates that disagreed about order would attribute
#:   real deviations to the wrong notes and produce a breakdown that looks
#:   entirely reasonable. Measured at 26.0 ms against 28.5 ms unordered, so the
#:   guarantee is free.
#: * **The lateral holds an aggregate, so it always returns a row.** An analysis
#:   still running has no ``pitch_points`` key at all; an aggregate with no
#:   ``GROUP BY`` answers "no rows" with one row of nulls, which ``COALESCE``
#:   turns into empty arrays. A pending record therefore reads back as itself
#:   with an empty timeline, rather than vanishing from the result.
_FIELDS_SQL = """
    WITH latest AS (
        SELECT id
        FROM audio_analyses
        WHERE recording_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    )
    SELECT analysis.document - 'pitch_points' AS document,
           analysis.pitch_point_count,
           frames.midi_notes,
           frames.cents
    FROM latest
    JOIN audio_analyses AS analysis ON analysis.id = latest.id
    CROSS JOIN LATERAL (
        SELECT COALESCE(
                   array_agg((frame.point ->> 'midi_note')::int ORDER BY frame.ordinality),
                   '{}'::int[]
               ) AS midi_notes,
               COALESCE(
                   array_agg((frame.point ->> 'cents')::float8 ORDER BY frame.ordinality),
                   '{}'::float8[]
               ) AS cents
        FROM jsonb_array_elements(analysis.document -> 'pitch_points')
             WITH ORDINALITY AS frame(point, ordinality)
    ) AS frames
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

    async def latest_decimated_for_recording(
        self, recording_id: str, *, max_points: int
    ) -> DecimatedTimeline | None:
        """The most recent analysis with **at most ``max_points``** of its timeline.

        The read behind ``GET …/audio-analysis/pitch``, which draws a graph a
        few hundred pixels wide and has never returned more than a thousand
        points by default. Every ``n``-th point is selected where the timeline
        is longer than that, and ``n`` is reported rather than implied.

        The stride is decided from the stored count, so the caller does not
        supply one and cannot ask for a sample that misrepresents its own
        density.
        """

    async def latest_fields_for_recording(self, recording_id: str) -> TimelineFields | None:
        """The most recent analysis with the **fields the aggregations fold**.

        The read behind ``GET …/audio-analysis/notes`` and ``…/key``. Both fold
        every frame of the timeline, so unlike the graph neither may be given a
        sample; both read exactly two things about a frame, so neither needs it
        built. What comes back is every stored frame's semitone and deviation,
        as two arrays, and the record they belong to.

        Nothing built from this can be returned as a timeline or written back —
        see :class:`TimelineFields`.
        """

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
                         error_code, pitch_point_count, document)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        analysis.audio_analysis_id,
                        analysis.recording_id,
                        analysis.status.value,
                        analysis.feedback_status.value,
                        analysis.created_at,
                        analysis.error_code.value if analysis.error_code else None,
                        # Counted from the tuple being serialised on the line
                        # below, not read off ``pitch_point_count``. The model
                        # derives that field when a record is *validated*, and
                        # ``model_copy(update=...)`` skips validators — so a
                        # record assembled that way carries a stale count and
                        # a fresh timeline. Counting here cannot be stale.
                        len(analysis.pitch_points),
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
                   SET status = %s, feedback_status = %s, error_code = %s,
                       pitch_point_count = %s, document = %s
                 WHERE id = %s AND status = %s
                """,
                (
                    analysis.status.value,
                    analysis.feedback_status.value,
                    analysis.error_code.value if analysis.error_code else None,
                    # Rewritten with the document, in the same statement: this
                    # is the write that attaches a timeline to a pending record.
                    len(analysis.pitch_points),
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

    async def latest_decimated_for_recording(
        self, recording_id: str, *, max_points: int
    ) -> DecimatedTimeline | None:
        if max_points < 1:
            raise ValueError("max_points must be at least 1")
        async with self._db.connection() as connection:
            row = await fetch_one(connection, _DECIMATED_SQL, (max_points, recording_id))
        return None if row is None else _to_decimated(row)

    async def latest_fields_for_recording(self, recording_id: str) -> TimelineFields | None:
        async with self._db.connection() as connection:
            row = await fetch_one(connection, _FIELDS_SQL, (recording_id,))
        return None if row is None else _to_fields(row)

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


def _to_fields(row: dict[str, Any]) -> TimelineFields:
    """Build the fold's input from the stripped document and the two arrays.

    The driver has already decoded them as an ``int[]`` and a ``float8[]``;
    :class:`PitchFields` bounds every value exactly as :class:`PitchPoint` does,
    so a fold still cannot be handed a number that is not a note. That check
    costs 0.83 ms for 12 931 frames — it was never the models that made it
    possible.
    """
    return TimelineFields(
        analysis=_to_summary(row),
        fields=PitchFields(midi_notes=row["midi_notes"], cents=row["cents"]),
    )


def _to_decimated(row: dict[str, Any]) -> DecimatedTimeline:
    """Build a sampled timeline from the stripped document and the points beside it.

    The points are validated exactly as they would be coming out of a whole
    document — there is one fewer of them, not one less check.
    """
    return DecimatedTimeline(
        analysis=_to_summary(row),
        points=tuple(PitchPoint.model_validate(point) for point in row["pitch_points"]),
        decimation=row["decimation"],
    )
