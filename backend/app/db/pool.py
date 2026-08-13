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
from typing import Any, Final

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Pool bounds. Small on purpose: this is one API process serving a handful of
#: concurrent requests, and an oversized pool mostly buys idle connections that
#: the server has to keep alive.
_MIN_SIZE: Final = 1
_MAX_SIZE: Final = 10

#: How long a caller waits for a connection before giving up. Long enough to
#: ride out a brief spike, short enough that a wedged pool fails visibly.
_POOL_TIMEOUT_SECONDS: Final = 10.0


class Database:
    """An open pool, plus the transaction helper every repository uses."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("a database DSN is required")
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=_MIN_SIZE,
            max_size=_MAX_SIZE,
            timeout=_POOL_TIMEOUT_SECONDS,
            # Opened explicitly by ``open`` so startup failures surface at
            # startup rather than on the first request.
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=_POOL_TIMEOUT_SECONDS)
        logger.info("database_pool_opened", extra={"max_size": _MAX_SIZE})

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
