"""Loading exactly the two recordings a comparison needs.

The query is the security boundary, so it is worth being explicit about its
shape. ``owner_id`` is in the ``WHERE`` clause **alongside** the id filter, not
applied afterwards in Python:

    WHERE r.owner_id = %s AND r.id = ANY(%s)

A recording belonging to somebody else is therefore never selected, and comes
back from this module as simply absent — indistinguishable from an id that was
never real. There is no code path that fetches a recording and then decides
whether the caller may see it, because that path is where authorisation bugs
live.

Two rows, by primary key. It does not read the owner's history and filter it,
and it does not grow with how many recordings somebody has.

The latest audio analysis is loaded **whatever its status**, rather than only a
completed one. A comparison that refuses has to be able to say *why* — nobody
measured this yet, it is still running, it failed, there was no reliable pitch —
and only the record itself carries that.

What it loads of that analysis is the record without its timeline, plus the two
fields of every frame the note breakdown reads (Step 10.16). Both sides' whole
timelines used to cross the socket so that the API process could rebuild 25 862
pitch frames and fold two fields out of them.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.services.audio_analysis.models import TimelineFields
from app.services.audio_analysis.postgres_repository import (
    PITCH_FIELD_AGGREGATES,
    timeline_fields_of,
)
from app.services.recordings.models import Recording

#: One statement, two ids, both owner-scoped. ``ANY(%s)`` takes a list, so the
#: ids are bound parameters rather than an interpolated ``IN`` list.
#:
#: The array is cast to ``char(32)`` — the column's own type — and that cast is
#: load-bearing rather than cosmetic. Without it PostgreSQL compares
#: ``id::text`` to a ``text[]``, which no index on ``id`` can serve, so the
#: planner falls back to ``recordings_owner_created_idx`` and reads the owner's
#: *entire* history to filter two rows out of it. Measured on 200 owners with 50
#: recordings each: 67 buffers and 48 rows discarded, versus 21 buffers and none
#: discarded once the types match. With the cast the primary key is used, and
#: the cost of a comparison stops growing with how much somebody has recorded.
#:
#: **It reads the fields the comparison folds, not the frames** (10.16). A
#: comparison needs both sides' metrics and both sides' note breakdowns, and a
#: breakdown may not be taken from a sample — of every thirteenth frame is a
#: breakdown of a different recording. What it never needed is the *frame*: of
#: the six fields a stored point carries, the breakdown reads the semitone and
#: the deviation from it. Until this step the statement handed over two whole
#: documents and the API process turned 2 916 kB into 25 862 ``PitchPoint``
#: models on the event loop, which made this the slowest read in the product —
#: 137.5 ms with one client, 2 160.3 ms with sixteen, and ~7 requests a second
#: either way, which is the signature of work that cannot overlap. Reading the
#: fields instead: 36.0 ms, 239.9 ms, and 63.1 requests a second at sixteen. It
#: is the same defect 10.15 removed from ``/notes``, in the one statement that
#: reads two recordings at once, and the same fix.
#:
#: Four details are deliberate, and three of them are 10.15's rules holding in a
#: statement with two sides:
#:
#: * **The strip and the expansion, and nothing else.** ``analysis.document`` is
#:   referenced exactly twice, which is 10.12's rule: a toasted document is
#:   decompressed once per *expression*, so a third reference would be a third
#:   full detoast — per side, here.
#: * **The latest analysis is chosen before its timeline is expanded.** The
#:   inner lateral selects one id; the join brings back that row alone. Folding
#:   the aggregate into that same subquery would have expanded the timeline of
#:   every analysis a re-analysed recording has, only to discard all but one.
#: * **``LEFT`` at every join, so a recording without an analysis survives.** A
#:   comparison has to be able to say *nobody measured this yet*, which it can
#:   only do if the row comes back at all. An unmeasured side arrives as a null
#:   document, exactly as before.
#: * **The aggregates are shared with the ``/notes`` read**, rather than written
#:   twice — see :data:`PITCH_FIELD_AGGREGATES`. Two statements that ordered
#:   these arrays differently would attribute real deviations to the wrong
#:   notes, in one of the two places a note breakdown is built.
COMPARISON_SOURCES_SQL = f"""
SELECT r.id                        AS recording_id,
       r.document                  AS recording,
       analysis.document - 'pitch_points' AS audio_analysis,
       analysis.pitch_point_count  AS pitch_point_count,
       frames.midi_notes           AS midi_notes,
       frames.cents                AS cents
  FROM recordings r
  LEFT JOIN LATERAL (
      SELECT id FROM audio_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) latest ON TRUE
  LEFT JOIN audio_analyses AS analysis ON analysis.id = latest.id
  LEFT JOIN LATERAL (
      SELECT {PITCH_FIELD_AGGREGATES}
      FROM jsonb_array_elements(analysis.document -> 'pitch_points')
           WITH ORDINALITY AS frame(point, ordinality)
  ) AS frames ON TRUE
 WHERE r.owner_id = %s AND r.id = ANY(%s::char(32)[])
"""


@dataclass(frozen=True, slots=True)
class ComparisonSource:
    """One recording and its most recent audio analysis, if it has one.

    The analysis arrives as :class:`TimelineFields` — the record without its
    timeline, plus the two fields of every frame the note breakdown folds. That
    is a type rather than a convention, and it is what stops this read from
    growing back: a :class:`~app.services.audio_analysis.models.PitchFields`
    has no timestamps in it, so nothing here can be drawn or returned as points,
    and the record it carries is an
    :class:`~app.services.audio_analysis.models.AudioAnalysisSummary`, which
    ``update`` refuses. A comparison cannot become a write.
    """

    recording: Recording
    audio_analysis: TimelineFields | None


@runtime_checkable
class ComparisonSourceReader(Protocol):
    """Loads the two sides of a comparison, scoped to one owner."""

    async def comparison_sources(
        self, owner_id: uuid.UUID, recording_ids: list[str]
    ) -> dict[str, ComparisonSource]:
        """Return the requested recordings this owner actually has.

        Keyed by recording id. An id that is unknown **or not theirs** is simply
        absent from the result; the caller cannot tell the two apart, and
        neither can the client.
        """


def source_from_row(row: dict[str, Any]) -> ComparisonSource:
    """Build a source from one result row.

    A null document is a recording nobody has measured yet — the ``LEFT`` joins
    keep its row — and it stays ``None`` here rather than becoming an empty
    analysis. The count comes from the column beside the document, because the
    document handed back no longer holds the array it counts.
    """
    document = row["audio_analysis"]
    return ComparisonSource(
        recording=Recording.model_validate(row["recording"]),
        audio_analysis=(
            None
            if document is None
            else timeline_fields_of(
                document,
                pitch_point_count=row["pitch_point_count"],
                midi_notes=row["midi_notes"],
                cents=row["cents"],
            )
        ),
    )


__all__ = [
    "COMPARISON_SOURCES_SQL",
    "ComparisonSource",
    "ComparisonSourceReader",
    "source_from_row",
]
