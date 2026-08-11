"""Persistence for owners.

Protocol first, PostgreSQL behind it — the same shape every repository in this
project has had since Step 7A. A service asks for an owner; it never sees SQL.

Step 7P adds the two owner-level operations the product was missing: a summary
of what an identity has accumulated, and the removal of all of it. Both are
scoped by ``owner_id`` in SQL like everything else, so neither can reach another
owner's data.
"""

import uuid
from typing import Protocol, runtime_checkable

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import Owner, hash_token


@runtime_checkable
class OwnerRepository(Protocol):
    """Storage-agnostic interface for owner identities."""

    async def create(self, owner: Owner, token: str) -> Owner:
        """Persist a new owner, storing only a hash of ``token``."""

    async def get_by_token(self, token: str) -> Owner | None:
        """Resolve a bearer token to its owner, or ``None``."""

    async def get(self, owner_id: uuid.UUID) -> Owner | None:
        """Return the owner, or ``None`` if it is unknown."""

    async def data_summary(self, owner_id: uuid.UUID) -> OwnerDataSummary:
        """What this owner has accumulated. Counts only — never the content."""

    async def recording_ids(self, owner_id: uuid.UUID) -> list[str]:
        """Every recording id this owner has, so the stored audio can be removed."""

    async def delete_owner(self, owner_id: uuid.UUID) -> bool:
        """Remove the owner row; analyses and recordings cascade with it."""


class PostgresOwnerRepository:
    """Owner identities in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(self, owner: Owner, token: str) -> Owner:
        async with self._db.transaction() as connection:
            await execute(
                connection,
                "INSERT INTO owners (id, token_hash, created_at) VALUES (%s, %s, %s)",
                (owner.owner_id, hash_token(token), owner.created_at),
            )
        return owner

    async def get_by_token(self, token: str) -> Owner | None:
        # Looked up by hash, so the clear token never appears in a query, a
        # query log, or a slow-query report.
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT id, created_at FROM owners WHERE token_hash = %s",
                (hash_token(token),),
            )
        return None if row is None else Owner(owner_id=row["id"], created_at=row["created_at"])

    async def get(self, owner_id: uuid.UUID) -> Owner | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection, "SELECT id, created_at FROM owners WHERE id = %s", (owner_id,)
            )
        return None if row is None else Owner(owner_id=row["id"], created_at=row["created_at"])

    async def data_summary(self, owner_id: uuid.UUID) -> OwnerDataSummary:
        """Count what this owner has, in one statement.

        Counts, never content: this answers "what would I lose?" and nothing
        that could stand in for reading the recordings themselves. Scoped by
        ``owner_id`` in the ``WHERE`` clause like every other read.
        """
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT count(DISTINCT r.id) AS recordings,
                       count(DISTINCT a.recording_id)
                           FILTER (WHERE a.status = 'completed')          AS analysed,
                       count(DISTINCT a.id)
                           FILTER (WHERE a.feedback_status = 'completed') AS feedback
                  FROM recordings r
                  LEFT JOIN audio_analyses a ON a.recording_id = r.id
                 WHERE r.owner_id = %s
                """,
                (owner_id,),
            )
        if row is None:  # pragma: no cover - an aggregate always returns a row
            return OwnerDataSummary(recordings=0, analysed_recordings=0, ai_feedback=0)
        return OwnerDataSummary(
            recordings=row["recordings"],
            analysed_recordings=row["analysed"],
            ai_feedback=row["feedback"],
        )

    async def recording_ids(self, owner_id: uuid.UUID) -> list[str]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                "SELECT id FROM recordings WHERE owner_id = %s ORDER BY id",
                (owner_id,),
            )
        # ``recordings.id`` is CHAR(32); strip so a padded value can never miss
        # the file it names.
        return [str(row["id"]).strip() for row in rows]

    async def delete_owner(self, owner_id: uuid.UUID) -> bool:
        """One statement. Recordings and both kinds of analysis cascade."""
        async with self._db.transaction() as connection:
            affected = await execute(connection, "DELETE FROM owners WHERE id = %s", (owner_id,))
        return affected > 0
