"""Persistence for song references.

Protocol first, PostgreSQL behind it — the same shape every repository in this
project has. Every method takes an owner id, because a method that could read a
reference without knowing whose it is would be a method that could leak one.

**Ownership is a ``WHERE`` clause, never a Python branch.** A reference
belonging to somebody else is not fetched and then rejected; it is never
selected, and comes back from here as simply absent — indistinguishable from an
id that was never real. There is no code path in this module that reads a row
and then decides whether the caller may see it.

Under the chosen input model there is no reference audio, so there is nothing on
disk to keep in step with these rows: deleting one is a single statement, and
``DELETE /identity`` needs no new step because the foreign key cascades from
``owners``.
"""

import uuid
from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.compatibility.models import SongReference


class ReferenceAlreadyExistsError(Exception):
    """A reference already exists under that id."""


@runtime_checkable
class SongReferenceRepository(Protocol):
    """Storage-agnostic interface for owned song references."""

    async def create(self, reference: SongReference, owner_id: uuid.UUID) -> SongReference:
        """Persist a new reference owned by ``owner_id``."""

    async def get(self, reference_id: str, owner_id: uuid.UUID) -> SongReference | None:
        """The reference, or ``None`` if it does not exist **or is not theirs**."""

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[SongReference]:
        """An owner's references, newest first."""

    async def delete(self, reference_id: str, owner_id: uuid.UUID) -> bool:
        """Remove one of the owner's references. ``True`` if one was removed."""

    async def count_for_owner(self, owner_id: uuid.UUID) -> int:
        """How many references this owner holds. A count, never the content."""


class PostgresSongReferenceRepository:
    """Song references in PostgreSQL, scoped by owner on every statement."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(self, reference: SongReference, owner_id: uuid.UUID) -> SongReference:
        async with self._db.transaction() as connection:
            try:
                await execute(
                    connection,
                    """
                    INSERT INTO song_references (id, owner_id, created_at, document)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        reference.reference_id,
                        owner_id,
                        reference.created_at,
                        reference.model_dump_json(),
                    ),
                )
            except errors.UniqueViolation as exc:
                # Ids are server-generated uuid4 hex, so this is a genuine
                # duplicate insert rather than a collision.
                raise ReferenceAlreadyExistsError(
                    f"reference {reference.reference_id} already exists"
                ) from exc
        return reference

    async def get(self, reference_id: str, owner_id: uuid.UUID) -> SongReference | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT document FROM song_references WHERE id = %s AND owner_id = %s",
                (reference_id, owner_id),
            )
        return None if row is None else _to_reference(row)

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[SongReference]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT document FROM song_references
                WHERE owner_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (owner_id, limit),
            )
        return [_to_reference(row) for row in rows]

    async def delete(self, reference_id: str, owner_id: uuid.UUID) -> bool:
        async with self._db.transaction() as connection:
            affected = await execute(
                connection,
                "DELETE FROM song_references WHERE id = %s AND owner_id = %s",
                (reference_id, owner_id),
            )
        return affected > 0

    async def count_for_owner(self, owner_id: uuid.UUID) -> int:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT count(*) AS total FROM song_references WHERE owner_id = %s",
                (owner_id,),
            )
        return 0 if row is None else int(row["total"])


def _to_reference(row: dict[str, Any]) -> SongReference:
    """Rebuild the domain object from its stored document.

    Validated on the way back rather than trusted: the model's own rule — that
    both notes are real MIDI notes in the right order — is the same one that
    guarded the write, so a row that somehow stopped satisfying it fails here
    instead of producing a comparison against nonsense.
    """
    return SongReference.model_validate(row["document"])
