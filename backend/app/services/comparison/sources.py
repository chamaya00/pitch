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

Two rows, by primary key, with one lateral join each. It does not read the
owner's history and filter it, and it does not grow with how many recordings
somebody has.

The latest audio analysis is loaded **whatever its status**, rather than only a
completed one. A comparison that refuses has to be able to say *why* — nobody
measured this yet, it is still running, it failed, there was no reliable pitch —
and only the record itself carries that.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.services.audio_analysis.models import AudioAnalysis
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
COMPARISON_SOURCES_SQL = """
SELECT r.id            AS recording_id,
       r.document      AS recording,
       a.document      AS audio_analysis
  FROM recordings r
  LEFT JOIN LATERAL (
      SELECT document FROM audio_analyses
       WHERE recording_id = r.id
       ORDER BY created_at DESC, id DESC
       LIMIT 1
  ) a ON TRUE
 WHERE r.owner_id = %s AND r.id = ANY(%s::char(32)[])
"""


@dataclass(frozen=True, slots=True)
class ComparisonSource:
    """One recording and its most recent audio analysis, if it has one."""

    recording: Recording
    audio_analysis: AudioAnalysis | None


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
    """Build a source from one result row."""
    document = row["audio_analysis"]
    return ComparisonSource(
        recording=Recording.model_validate(row["recording"]),
        audio_analysis=None if document is None else AudioAnalysis.model_validate(document),
    )


__all__ = [
    "COMPARISON_SOURCES_SQL",
    "ComparisonSource",
    "ComparisonSourceReader",
    "source_from_row",
]
