"""Persistence for owners.

Protocol first, PostgreSQL behind it — the same shape every repository in this
project has had since Step 7A. A service asks for an owner; it never sees SQL.
"""

import uuid
from typing import Protocol, runtime_checkable

from app.db.pool import Database, execute, fetch_one
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
