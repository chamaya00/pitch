"""The connection pool, and the one place a DSN is read.

**No ORM.** The repositories in ``services/`` already map domain objects to
storage by hand and have done since Step 7A; an ORM would be a second mapping
layer to keep in step with the pydantic models, for a schema of five tables.
What is actually needed is transactions, constraints, indexes, parameterised
queries and pooling, and ``psycopg`` provides all five.

``psycopg`` is used in **async** mode because the application is async: a
synchronous driver would block the event loop for the duration of every query,
which is exactly the failure the background-task split exists to avoid.

The DSN comes from ``DATABASE_URL`` and nowhere else. It is never logged, never
returned by an endpoint, and never embedded in a test fixture — see
``docs/architecture.md``.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final, NamedTuple

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Default pool bounds, per process.
#:
#: Ten was written when this was "one API process serving a handful of
#: concurrent requests". Step 10.10 made several workers a supported shape and
#: the constant did not move, so the ceiling silently became ``workers x 10``
#: against a server that grants about a hundred connections in total. Step
#: 10.17 measured both halves of that: what a larger pool buys one process
#: (nothing, past four) and what the multiplier costs a server (everything, at
#: twelve workers). See :func:`connection_budget` and ``docs/architecture.md``.
_DEFAULT_MIN_SIZE: Final = 1
_DEFAULT_MAX_SIZE: Final = 4

#: How long a caller waits for a connection before giving up. Long enough to
#: ride out a brief spike, short enough that a wedged pool fails visibly.
_POOL_TIMEOUT_SECONDS: Final = 10.0

#: What this application calls itself in ``pg_stat_activity``.
APPLICATION_NAME: Final = "vocallens-api"

#: What a one-off maintenance job calls itself, and how much it may take.
#:
#: One connection, because these jobs are sequential and because the moment
#: they matter most is the moment the API's workers are holding everything
#: else. A pool that asked for four could fail to open against a server the
#: job was written to tidy up.
MAINTENANCE_APPLICATION_NAME: Final = "vocallens-maintenance"
MAINTENANCE_POOL_SIZE: Final = 1


class ConnectionBudget(NamedTuple):
    """What one server has, and how many pools of this size fit inside it.

    ``processes`` is the arithmetic an operator scaling workers needs and
    cannot do from the API's own configuration, because a process cannot see
    how many siblings uvicorn started. It is reported at startup rather than
    enforced, for the same reason: this process knows its own pool and the
    server's limit, and only the operator knows the worker count.
    """

    max_connections: int
    reserved: int
    pool_max_size: int

    @property
    def available(self) -> int:
        """Connections a non-superuser may actually take."""
        return max(self.max_connections - self.reserved, 0)

    @property
    def processes(self) -> int:
        """How many processes may hold a full pool before the server refuses."""
        return self.available // self.pool_max_size

    @property
    def fits(self) -> bool:
        """Whether even one pool of this size fits on this server."""
        return self.processes >= 1


class Database:
    """An open pool, plus the transaction helper every repository uses."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = _DEFAULT_MIN_SIZE,
        max_size: int = _DEFAULT_MAX_SIZE,
        application_name: str = APPLICATION_NAME,
    ) -> None:
        if not dsn:
            raise ValueError("a database DSN is required")
        if min_size > max_size:
            raise ValueError("a pool cannot keep more connections open than it may hold")
        self._max_size = max_size
        self._application_name = application_name
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=_POOL_TIMEOUT_SECONDS,
            # Opened explicitly by ``open`` so startup failures surface at
            # startup rather than on the first request.
            open=False,
            # ``application_name`` is what makes the holder of a connection
            # visible in ``pg_stat_activity``. Without it, an operator looking
            # at a server that has run out has a list of anonymous rows; with
            # it, they can see which pools are holding what. It is a label, not
            # a credential, and it carries no user data.
            kwargs={"row_factory": dict_row, "application_name": application_name},
        )

    @property
    def max_size(self) -> int:
        """The most connections this process will hold at once."""
        return self._max_size

    @property
    def application_name(self) -> str:
        """How connections from this pool identify themselves to the server."""
        return self._application_name

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=_POOL_TIMEOUT_SECONDS)
        budget = await self.budget()
        if not budget.fits:
            # Refused rather than warned about: a pool larger than the server
            # will ever grant cannot fill, so the setting is not merely
            # ambitious, it is impossible. The same rule Step 10.10 applied to
            # RATE_LIMIT_BACKEND=database without a DATABASE_URL.
            await self.close()
            raise RuntimeError(
                f"a pool of {budget.pool_max_size} cannot fit on a server granting "
                f"{budget.available} connections ({budget.max_connections} max_connections, "
                f"{budget.reserved} reserved); lower DB_POOL_MAX_SIZE"
            )
        logger.info(
            "database_pool_opened",
            extra={
                "max_size": budget.pool_max_size,
                "server_max_connections": budget.max_connections,
                # What an operator adding workers needs in front of them: this
                # server has room for this many processes of this size.
                "processes_that_fit": budget.processes,
            },
        )

    async def budget(self) -> ConnectionBudget:
        """This pool's size against what the server it is pointed at grants.

        Read from the server rather than assumed: ``max_connections`` is not a
        property of this application, and a deployment that raised it should
        not be told a number from a comment.
        """
        async with self.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT current_setting('max_connections')::int             AS max_connections,
                       current_setting('superuser_reserved_connections')::int AS reserved
                """,
            )
        if row is None:  # pragma: no cover - a settings query always returns a row
            raise RuntimeError("PostgreSQL did not report its connection limit")
        return ConnectionBudget(
            max_connections=row["max_connections"],
            reserved=row["reserved"],
            pool_max_size=self._max_size,
        )

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A connection inside a transaction, committed on success.

        Every repository method runs inside one of these. An exception rolls the
        whole thing back — which is what makes "analysis completion is atomic"
        a property of the code rather than a hope.
        """
        async with self._pool.connection() as connection, connection.transaction():
            yield connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """A connection without an explicit transaction, for reads."""
        async with self._pool.connection() as connection:
            yield connection


# --- Query helpers ---------------------------------------------------------
#
# Rows come back as dictionaries. The row factory is set per cursor rather than
# relied on from the pool, so the mapping type is visible to the type checker
# instead of being an ``Any`` that every repository has to assert about.


async def fetch_one(
    connection: AsyncConnection, sql: str, params: Sequence[Any] = ()
) -> dict[str, Any] | None:
    """Run a query and return its first row, or ``None``."""
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchone()


async def fetch_all(
    connection: AsyncConnection, sql: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    """Run a query and return every row."""
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchall()


async def execute(connection: AsyncConnection, sql: str, params: Sequence[Any] = ()) -> int:
    """Run a statement and return how many rows it affected.

    The row count is the point: a conditional ``UPDATE`` that matched nothing is
    how this codebase detects that another worker got there first.
    """
    async with connection.cursor() as cursor:
        await cursor.execute(sql, params)
        return cursor.rowcount
