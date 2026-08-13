"""Loading a progress history and handing it to the pure transformation.

Deliberately thin, and deliberately **not** a trend engine. It bounds the
window, asks the repository for rows, and calls ``build_history``. Everything
that decides what progress *means* — which states are eligible, what a null
becomes, what may be said about change — lives in ``series.py``, where it can be
tested without a database.

It takes no provider, and that absence is the guarantee rather than a promise:
there is no object here through which a language model could produce or judge a
trend. Progress is arithmetic over stored measurements, and a model producing one
of these numbers would be producing a measurement.
"""

import uuid
from typing import Final

from app.services.progress.models import ProgressHistory
from app.services.progress.series import build_history
from app.services.progress.sources import ProgressSourceReader

#: How many recordings a progress view reads by default. A practice history is
#: read as a recent trend, not as an archive; the API lets a client ask for more.
DEFAULT_WINDOW: Final = 30

#: Hard ceiling. An unbounded window is how a cheap indexed query becomes an
#: expensive one, and no screen benefits from four hundred points.
MAX_WINDOW: Final = 200


class ProgressService:
    """One owner's measurements over time."""

    def __init__(self, *, recordings: ProgressSourceReader) -> None:
        self._recordings = recordings

    async def history(self, owner_id: uuid.UUID, limit: int = DEFAULT_WINDOW) -> ProgressHistory:
        """The owner's most recent ``limit`` recordings as progress series.

        Never raises for an empty history: an owner with no recordings gets an
        empty history with ``depth: "empty"``, which is an answer rather than an
        error, and is exactly what an owner whose recordings all belong to
        somebody else would get.
        """
        window = max(1, min(limit, MAX_WINDOW))
        rows = await self._recordings.progress_rows(owner_id, window)
        return build_history(rows, limit=window)
