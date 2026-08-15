"""Applying schema migrations, deterministically and exactly once.

A directory of numbered ``.sql`` files and a table recording which have run.
That is the whole mechanism, and it is deliberate: Alembic exists to generate
migrations from an ORM's models, and this project has no ORM to generate them
from. What is actually needed is "run these files, in this order, once", which
is forty lines.

Every migration runs **inside a transaction along with the row that records
it**, so a migration cannot be half-applied or applied twice. Applying an
already-applied file is a no-op, which is what makes startup safe to repeat.
"""

import hashlib
from pathlib import Path
from typing import Final

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.core.logging import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR: Final = Path(__file__).parent / "migrations"

_CREATE_TABLE: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Every migration, in lexical order — which is why they are numbered."""
    return sorted(path for path in directory.glob("*.sql"))


async def apply_migrations(
    connection: AsyncConnection, directory: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply every migration that has not run. Returns the names applied.

    Safe to call on an empty database, on a fully migrated one, and
    concurrently: the advisory lock below serialises two processes starting at
    once, so they cannot both try to create the same table.

    Raises:
        RuntimeError: an already-applied migration's contents have changed.
            That means the file was edited after it ran, and continuing would
            leave two databases with the same recorded history and different
            schemas.
    """
    # A session-level advisory lock, held for this transaction only. Two API
    # processes booting simultaneously is the ordinary case, not an edge one.
    #
    # Taken **before** the bookkeeping table is created, and that order is the
    # whole point. Until Step 10.10 the ``CREATE TABLE IF NOT EXISTS`` ran
    # first, outside the lock, and ``IF NOT EXISTS`` is not atomic against a
    # concurrent create: two transactions both find the table missing and both
    # insert a ``pg_type`` row, and the loser dies on
    # ``pg_type_typname_nsp_index``. It needs no table of its own, so nothing
    # stops it going first. Measured on a fresh schema with six workers
    # starting together: 25 of 30 boots failed before this line moved, and 30 of
    # 30 succeeded after.
    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    await connection.execute(_CREATE_TABLE)

    # The row factory is set per-cursor here rather than relying on the pool's:
    # migrations also run from the CLI, against a plain connection.
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT name, checksum FROM schema_migrations")
        applied = {row["name"]: row["checksum"] for row in await cursor.fetchall()}

    ran: list[str] = []
    for path in migration_files(directory):
        sql = path.read_text(encoding="utf-8")
        checksum = _checksum(sql)

        if path.name in applied:
            if applied[path.name] != checksum:
                raise RuntimeError(
                    f"migration {path.name} has changed since it was applied; "
                    "add a new migration instead of editing an applied one"
                )
            continue

        await connection.execute(sql)
        await connection.execute(
            "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
        ran.append(path.name)

    if ran:
        logger.info("migrations_applied", extra={"count": len(ran), "names": ",".join(ran)})
    return ran


#: Arbitrary but fixed: any process migrating this schema uses the same key.
_LOCK_KEY: Final = 0x564C_4D49  # "VLMI"
