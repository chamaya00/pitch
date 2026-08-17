"""Loading an owner's measurements over time.

Two decisions define this query.

**Ownership is in the ``WHERE`` clause.** ``r.owner_id = %s`` sits beside the
join, so another owner's recording is never selected. There is no code path that
loads history and then filters it, because that path is where authorisation bugs
live. An owner with nothing looks exactly like an owner who does not exist:
an empty result.

**The analysis document is never selected.** The metrics live inside a JSONB
document that also holds the pitch timeline, so a document grows with the length
of the recording rather than with the number of measurements in it. Each scalar
is therefore extracted by path in SQL.

Measured against 200 owners × 50 two-minute recordings (5 000 pitch points each,
a 293 MB table), for one 30-recording window through the real driver:

===========================================  =========  ================
approach                                     time       decoded in Python
===========================================  =========  ================
scalars extracted in SQL (this query)         125 ms     14 KB
selecting the analysis documents instead      676 ms     18 MB
===========================================  =========  ================

5× faster and three orders of magnitude less data, and the gap widens with
recording length.

**The metrics object is reached once per row, not once per scalar.** That is the
second decision, and it was missing until Step 10.12 measured what "most of the
remaining 125 ms is PostgreSQL decompressing the documents" actually meant. A
document this size lives in the TOAST table, and PostgreSQL detoasts it *per
expression* — so eleven scalars read by path from ``a.document`` fetched and
decompressed the same 253 kB eleven times. Projecting ``document->'metrics'``
inside the lateral pays for that once and leaves a ~600-byte object for the
scalars to be read from. Measured on 50 five-minute recordings (11 083 points,
1 446 kB raw and 253 kB stored per document), one 30-recording window:

==========================================  =========  =========
approach                                    time       TOAST reads
==========================================  =========  =========
scalars read from ``a.document`` (before)     593 ms      11 834
scalars read from ``a.metrics`` (this query)   30 ms       1 172
==========================================  =========  =========

20×, and the buffer count is the proof of the mechanism rather than a side
effect: it falls by a factor of ten because ten of the eleven detoasts are gone.
What remains is one unavoidable decompression per row, which is the cost of
keeping the metrics in the same document as the timeline. Denormalising them
into columns would remove that too, and is still a schema change worth making
only when a measurement demands it — this one did not.

The extraction is *selection*, not interpretation: what a null means, which
states are eligible and how a series is assembled all live in ``series.py``.
SQL selects, filters and orders; the domain defines progress.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

#: The most recent ``limit`` recordings, returned oldest first.
#:
#: The inner ordering is ``created_at DESC, id DESC`` so the window is the
#: *latest* recordings; the outer ordering reverses it so a series reads left to
#: right in time. Both use the recording's server-assigned ``created_at`` — the
#: client clock is never consulted — and both tie-break on ``id``, so two
#: recordings created in the same clock tick have one deterministic order.
#:
#: ``recordings_owner_created_idx (owner_id, created_at DESC)`` covers the inner
#: query exactly, which is why no new index was added.
#:
#: ``a.metrics`` is ``document->'metrics'``, projected inside the lateral so the
#: document is detoasted once for the row rather than once for each scalar read
#: out of it. It is ``NULL`` for a recording with no completed analysis, exactly
#: as ``a.document`` was, so every scalar below stays ``NULL`` in that case and
#: nothing downstream changes.
PROGRESS_SQL = """
SELECT * FROM (
    SELECT r.id                AS recording_id,
           r.created_at        AS recorded_at,
           r.original_filename AS original_filename,
           r.duration_seconds  AS recording_duration_seconds,
           r.audio_format      AS audio_format,
           a.status            AS analysis_status,
           (a.metrics->>'duration_seconds')::float8            AS duration_seconds,
           (a.metrics->'stability'->>'in_tune_ratio')::float8  AS in_tune_ratio,
           (a.metrics->'stability'->>'mean_abs_cents_deviation')::float8
               AS mean_abs_cents_deviation,
           (a.metrics->'stability'->>'cents_std')::float8      AS cents_std,
           (a.metrics->'stability'->>'voiced_ratio')::float8   AS voiced_ratio,
           (a.metrics->'stability'->>'voiced_frames')::int     AS voiced_frames,
           (a.metrics->'settings'->>'hop_length_samples')::int AS hop_length_samples,
           (a.metrics->'settings'->>'sample_rate_hz')::int     AS sample_rate_hz,
           (a.metrics->'pitch'->>'semitone_span')::int         AS semitone_span,
           a.metrics->'pitch'->>'lowest_note'                  AS lowest_note,
           a.metrics->'pitch'->>'highest_note'                 AS highest_note
      FROM recordings r
      LEFT JOIN LATERAL (
          SELECT status, document->'metrics' AS metrics FROM audio_analyses
           WHERE recording_id = r.id AND status = 'completed'
           ORDER BY created_at DESC, id DESC
           LIMIT 1
      ) a ON TRUE
     WHERE r.owner_id = %s
     ORDER BY r.created_at DESC, r.id DESC
     LIMIT %s
) window_rows
ORDER BY recorded_at ASC, recording_id ASC
"""


@dataclass(frozen=True, slots=True)
class ProgressRow:
    """One recording and the scalars read out of its completed analysis.

    Every metric field is optional, and each ``None`` means something specific
    that the domain layer decides how to present: no completed analysis at all
    (``analysed`` is ``False``), or an analysis that completed without that
    particular measurement.
    """

    recording_id: str
    recorded_at: datetime
    original_filename: str
    recording_duration_seconds: float
    audio_format: str
    #: Whether a **completed** audio analysis was found. The query asks only for
    #: completed ones, so a pending, running or failed analysis reads the same
    #: as none — the recording contributes no point either way.
    analysed: bool

    duration_seconds: float | None = None
    in_tune_ratio: float | None = None
    mean_abs_cents_deviation: float | None = None
    cents_std: float | None = None
    voiced_ratio: float | None = None
    voiced_frames: int | None = None
    hop_length_samples: int | None = None
    sample_rate_hz: int | None = None
    semitone_span: int | None = None
    lowest_note: str | None = None
    highest_note: str | None = None


@runtime_checkable
class ProgressSourceReader(Protocol):
    """Loads one owner's measurement history."""

    async def progress_rows(self, owner_id: uuid.UUID, limit: int) -> list[ProgressRow]:
        """The owner's most recent ``limit`` recordings, oldest first.

        Includes recordings with no completed analysis: a recording that
        contributed no measurement still belongs in its own history, and
        omitting it would make the gaps invisible.
        """


def row_from_record(row: dict[str, Any]) -> ProgressRow:
    """Build a source row from one result row."""
    return ProgressRow(
        # ``recordings.id`` is CHAR(32); strip so a padded value can never
        # differ from the id the client sent.
        recording_id=str(row["recording_id"]).strip(),
        recorded_at=row["recorded_at"],
        original_filename=row["original_filename"],
        recording_duration_seconds=row["recording_duration_seconds"],
        audio_format=row["audio_format"],
        analysed=row["analysis_status"] is not None,
        duration_seconds=row["duration_seconds"],
        in_tune_ratio=row["in_tune_ratio"],
        mean_abs_cents_deviation=row["mean_abs_cents_deviation"],
        cents_std=row["cents_std"],
        voiced_ratio=row["voiced_ratio"],
        voiced_frames=row["voiced_frames"],
        hop_length_samples=row["hop_length_samples"],
        sample_rate_hz=row["sample_rate_hz"],
        semitone_span=row["semitone_span"],
        lowest_note=row["lowest_note"],
        highest_note=row["highest_note"],
    )


__all__ = ["PROGRESS_SQL", "ProgressRow", "ProgressSourceReader", "row_from_record"]
