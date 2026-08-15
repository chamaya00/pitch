"""A fixed window that every API worker shares.

The same algorithm as :class:`~app.services.limits.window.FixedWindowLimiter`,
with the count in a table instead of a dictionary, so N workers enforce one
limit between them rather than N limits each.

**One statement, and that is the whole correctness argument.** Reading the
count, deciding whether the window has expired, resetting or incrementing it and
returning the result are a single ``INSERT ... ON CONFLICT DO UPDATE``. Two
workers counting the same caller collide on the primary key; PostgreSQL makes
the second wait for the first to commit and then applies its ``DO UPDATE`` to
the row the first one wrote. A read-then-write would have a gap between the two
where both workers see the same count and both allow, which is the interleaving
that loses a limit — and it is not a rare one, since the traffic this exists to
stop is concurrent by nature.

**The database's clock, not this process's.** ``FixedWindowLimiter`` uses
``time.monotonic`` so an NTP correction cannot extend somebody's window. That is
not available here: a monotonic clock is meaningless between machines, and the
only clock all the workers share is the server's. Windows are therefore anchored
to ``now()``, and a backwards wall-clock correction on the database server can
extend a window by the size of the correction. It is a real weakness of a shared
counter and it is the price of being shared; on the scale that matters — a
window measured in hours against an abuse rate measured per second — it changes
nothing. The prune interval below still uses ``time.monotonic``, because that
one *is* per process.

**Failure is not silent.** If the database cannot be reached, the error
propagates rather than being swallowed into "allowed". A limiter that fails open
removes the limit exactly when the system is least healthy. Nothing is lost by
being strict: every request that reaches this limiter was about to write to the
same database anyway, so a database that cannot count is a database that could
not have served the request either.
"""

import time
from collections.abc import Callable
from typing import Final

from app.core.logging import get_logger
from app.db.pool import Database, execute, fetch_one
from app.services.limits.limiter import Decision

logger = get_logger(__name__)

#: Roll the window and count the attempt in one statement.
#:
#: The ``CASE`` arms are duplicated across the two columns rather than computed
#: once because a statement cannot bind an intermediate value, and splitting it
#: into a CTE that both read would reintroduce the read-then-write gap this
#: exists to close. They are the same predicate, so the count and the window
#: start can never disagree about whether the window expired.
#:
#: ``EXCLUDED`` is the row that failed to insert; the aliased ``w`` is the row
#: already there. The unqualified column in ``RETURNING`` is the row as it
#: stands *after* the update, which is what the decision is made from.
_COUNT_SQL: Final = """
INSERT INTO rate_limit_windows AS w (bucket, limit_key, window_started_at, count)
VALUES (%s, %s, now(), 1)
ON CONFLICT (bucket, limit_key) DO UPDATE
   SET window_started_at = CASE
           WHEN w.window_started_at + %s * INTERVAL '1 second' <= now() THEN now()
           ELSE w.window_started_at
       END,
       count = CASE
           WHEN w.window_started_at + %s * INTERVAL '1 second' <= now() THEN 1
           ELSE w.count + 1
       END
RETURNING count,
          EXTRACT(
              EPOCH FROM (window_started_at + %s * INTERVAL '1 second' - now())
          ) AS remaining_seconds
"""

#: Drop windows that have ended. They enforce nothing and are pure growth.
_PRUNE_SQL: Final = """
DELETE FROM rate_limit_windows
 WHERE bucket = %s
   AND window_started_at + %s * INTERVAL '1 second' <= now()
"""


class PostgresRateLimiter:
    """Allow ``limit`` attempts per ``window_seconds`` for each key, across workers.

    The database is reached through a callable rather than held directly,
    because the limiters are built in ``create_app`` and the pool is opened
    later, in the lifespan. Resolving it per check also means a test can build
    the application, point it at a database, and have the limiter find it —
    rather than the limiter having captured ``None`` at construction and
    silently never counting.
    """

    def __init__(
        self,
        *,
        bucket: str,
        limit: int,
        window_seconds: int,
        database: Callable[[], Database | None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._bucket = bucket
        self._limit = limit
        self._window = window_seconds
        self._database = database
        self._clock = clock
        # Negative infinity rather than "now", so the first check of a process's
        # life prunes. A worker that restarts often would otherwise never sweep.
        self._pruned_at = float("-inf")

    @property
    def limit(self) -> int:
        return self._limit

    def _require_database(self) -> Database:
        database = self._database()
        if database is None:
            # Unreachable through the application: ``Settings`` refuses to build
            # with the database backend selected and no DSN. Explicit anyway,
            # because the alternative to raising here is counting nothing.
            raise RuntimeError(
                "the database rate-limit backend is selected but no database is configured"
            )
        return database

    async def check(self, key: str) -> Decision:
        """Record an attempt by ``key`` and say whether it may proceed.

        Counts the attempt whether or not it is allowed, so a refused caller
        does not get a free retry — the same contract the in-process limiter
        keeps, enforced here by the ``count + 1`` arm running regardless of what
        the count already was.
        """
        database = self._require_database()
        await self._prune_if_due(database)

        async with database.transaction() as connection:
            row = await fetch_one(
                connection,
                _COUNT_SQL,
                (self._bucket, key, self._window, self._window, self._window),
            )
        # ``RETURNING`` on an upsert that either inserted or updated always has
        # a row; there is no third outcome for this statement.
        assert row is not None

        count = int(row["count"])
        if count <= self._limit:
            return Decision(allowed=True)

        remaining = float(row["remaining_seconds"])
        # Round up, and never below 1: a client told to retry after 0 seconds
        # retries immediately, which is a retry storm rather than a back-off.
        return Decision(allowed=False, retry_after_seconds=max(1, int(remaining) + 1))

    async def _prune_if_due(self, database: Database) -> None:
        """Delete ended windows, at most once per window per process.

        The in-process limiter bounds its memory by capping the key table and
        evicting; a table cannot be capped that way, and an unbounded one would
        make the shared counter a storage-exhaustion vector — the failure the
        whole limiter exists to prevent, moved from RAM to disk.

        Once per window is deliberate. Ended rows enforce nothing, so deleting
        them late costs only space, and the alternatives are worse: a delete on
        every check doubles the statement count to reclaim rows that are already
        harmless, and a scheduler is infrastructure this project does not have
        and has twice declined to add.
        """
        now = self._clock()
        if now - self._pruned_at < self._window:
            return
        self._pruned_at = now
        async with database.transaction() as connection:
            deleted = await execute(connection, _PRUNE_SQL, (self._bucket, self._window))
        if deleted:
            logger.info(
                "rate_limit_windows_pruned",
                extra={"bucket": self._bucket, "count": deleted},
            )


__all__ = ["PostgresRateLimiter"]
