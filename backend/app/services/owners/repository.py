"""Persistence for owners.

Protocol first, PostgreSQL behind it — the same shape every repository in this
project has had since Step 7A. A service asks for an owner; it never sees SQL.

Step 7P adds the two owner-level operations the product was missing: a summary
of what an identity has accumulated, and the removal of all of it. Both are
scoped by ``owner_id`` in SQL like everything else, so neither can reach another
owner's data.

Step 10.2 moves the key out. An owner no longer *is* a credential: credentials
live in their own table and reference an owner, so one identity can have several
ways in. The credential operations are at the bottom of this module and are
exposed through their own protocol (``services/owners/credential_repository.py``),
because the resolver needs them and has no business reaching an owner's
recordings.
"""

import uuid
from typing import Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.owners.credentials import (
    Credential,
    CredentialExistsError,
    LastCredentialError,
    credential_hash,
    new_credential,
)
from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import Owner


@runtime_checkable
class OwnerRepository(Protocol):
    """Storage-agnostic interface for owner identities."""

    async def create(self, owner: Owner, token: str) -> Owner:
        """Persist a new owner and its first credential.

        The owner and the way in to it are created together: an owner with no
        credential would be an identity nobody could ever reach.
        """

    async def get_by_token(self, token: str) -> Owner | None:
        """Resolve a credential key to its owner, or ``None``.

        Any of the owner's credentials resolves to the same owner — which is the
        point of Step 10.2, and the reason adding a key to a second device does
        not create a second identity.
        """

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
        """The owner and its first credential, in one transaction.

        Both or neither: an owner without a credential is an identity nobody
        could reach, and a credential without an owner would fail the foreign
        key anyway.
        """
        credential, _ = new_credential(owner.owner_id, "Original key")
        async with self._db.transaction() as connection:
            await execute(
                connection,
                "INSERT INTO owners (id, created_at) VALUES (%s, %s)",
                (owner.owner_id, owner.created_at),
            )
            await execute(
                connection,
                """
                INSERT INTO credentials (id, owner_id, credential_hash, label, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    credential.credential_id,
                    owner.owner_id,
                    credential_hash(token),
                    credential.label,
                    owner.created_at,
                ),
            )
        return owner

    async def get_by_token(self, token: str) -> Owner | None:
        # Looked up by hash, so the raw key never appears in a query, a query
        # log, or a slow-query report — and the comparison happens in an index,
        # not in a Python branch whose timing could leak.
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT o.id, o.created_at
                  FROM credentials c
                  JOIN owners o ON o.id = c.owner_id
                 WHERE c.credential_hash = %s
                """,
                (credential_hash(token),),
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

    # --- Credentials -------------------------------------------------------

    async def create_credential(self, credential: Credential, key: str) -> Credential:
        async with self._db.transaction() as connection:
            try:
                await execute(
                    connection,
                    """
                    INSERT INTO credentials (id, owner_id, credential_hash, label, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        credential.credential_id,
                        credential.owner_id,
                        credential_hash(key),
                        credential.label,
                        credential.created_at,
                    ),
                )
            except errors.UniqueViolation as exc:
                # 128-bit keys, so this is a duplicate insert rather than a
                # collision.
                raise CredentialExistsError("that credential is already registered") from exc
        return credential

    async def owner_for_key(self, key: str) -> uuid.UUID | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT owner_id FROM credentials WHERE credential_hash = %s",
                (credential_hash(key),),
            )
        return None if row is None else uuid.UUID(str(row["owner_id"]))

    async def credential_for_key(self, key: str) -> uuid.UUID | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT id FROM credentials WHERE credential_hash = %s",
                (credential_hash(key),),
            )
        return None if row is None else uuid.UUID(str(row["id"]))

    async def list_credentials(self, owner_id: uuid.UUID) -> list[Credential]:
        """Oldest first, covered by ``credentials_owner_idx``. Never the hashes."""
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT id, owner_id, label, created_at
                  FROM credentials
                 WHERE owner_id = %s
                 ORDER BY created_at, id
                """,
                (owner_id,),
            )
        return [
            Credential(
                credential_id=row["id"],
                owner_id=row["owner_id"],
                label=row["label"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def revoke_credential(self, credential_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        """Remove one credential, never the last one.

        The owner row is locked for the transaction first. Without it two
        concurrent revocations could each count two credentials, each delete
        one, and strand the identity between them — the exact race a rule like
        "never the last" invites.
        """
        async with self._db.transaction() as connection:
            await execute(connection, "SELECT id FROM owners WHERE id = %s FOR UPDATE", (owner_id,))
            row = await fetch_one(
                connection,
                "SELECT count(*) AS total FROM credentials WHERE owner_id = %s",
                (owner_id,),
            )
            total = 0 if row is None else int(row["total"])

            # Only refuse when the credential named is genuinely the last one.
            # A request naming somebody else's credential must fall through to
            # the delete and report "not found", not reveal a count.
            if total <= 1:
                exists = await fetch_one(
                    connection,
                    "SELECT id FROM credentials WHERE id = %s AND owner_id = %s",
                    (credential_id, owner_id),
                )
                if exists is not None:
                    raise LastCredentialError("an owner must keep at least one credential")

            affected = await execute(
                connection,
                "DELETE FROM credentials WHERE id = %s AND owner_id = %s",
                (credential_id, owner_id),
            )
        return affected > 0
