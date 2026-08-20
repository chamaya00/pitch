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
from datetime import datetime, timedelta
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
        """What this owner has accumulated. Counts only — never the content.

        All three numbers are counts of **recordings**, so neither of the last
        two can exceed the first — see the implementation for what that cost
        the sentence the deletion confirmation renders before Step 10.19.
        """

    async def recording_ids(self, owner_id: uuid.UUID) -> list[str]:
        """Every recording id this owner has, so the stored audio can be removed."""

    async def delete_owner(self, owner_id: uuid.UUID) -> bool:
        """Remove the owner row; analyses and recordings cascade with it."""

    async def touch(self, owner_id: uuid.UUID, stale_after: timedelta) -> None:
        """Record that this identity was just used, at most once per ``stale_after``.

        The throttle is the whole design. Writing on every request would add a
        write to every read — the objection Step 10.3 raised against a
        database-backed rate limiter — so a busy identity costs one UPDATE per
        interval and an idle one costs nothing.
        """


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

        **All three numbers count recordings**, and the lateral is what makes
        that a property of the statement rather than of a ``DISTINCT`` cleaning
        up afterwards. A recording may be analysed more than once, so joining
        recordings to analyses multiplies rows; folding each recording's
        analyses down to two booleans *first* leaves the outer query one row per
        recording to count. An aggregate with no ``GROUP BY`` always returns a
        row, so a recording with no analyses is a row of two nulls rather than
        no row at all.

        **The third number changed meaning in Step 10.19, because it was
        wrong.** It counted *analyses* whose feedback had completed, and both
        places it is rendered are sentences about recordings — "this key holds 5
        recordings, 3 measured, 2 with generated feedback", and "2 of them carry
        generated feedback, which cannot be recovered". A recording analysed
        twice with feedback both times made those sentences say that one
        recording included two of them. The two stores disagreed about it as
        well, in different ways, and nothing had asked either of them.

        It is also the fastest of the four forms measured — but only when it is
        measured the way production runs it. Through a pooled connection, which
        psycopg prepares after five uses, against an owner holding 5 000
        recordings in a database of 201 owners:

        ======================================  ================  =============
        form                                    5 000 recordings  25 recordings
        ======================================  ================  =============
        join, three ``count(DISTINCT …)``            16.1 ms         0.32 ms
        this lateral                               **11.7 ms**       0.31 ms
        three counts with ``EXISTS``                 20.1 ms         0.71 ms
        ======================================  ================  =============

        ``psql`` ranks them differently, and it is ``psql`` that is misleading:
        with the owner written in as a literal it plans afresh every time, which
        is not what a prepared statement gets. See ``docs/architecture.md``.
        """
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT count(*)                                   AS recordings,
                       count(*) FILTER (WHERE a.analysed)         AS analysed,
                       count(*) FILTER (WHERE a.carries_feedback) AS feedback
                  FROM recordings r
                  LEFT JOIN LATERAL (
                      SELECT bool_or(status = 'completed')          AS analysed,
                             bool_or(feedback_status = 'completed') AS carries_feedback
                        FROM audio_analyses a
                       WHERE a.recording_id = r.id
                  ) a ON TRUE
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

    # --- Activity and retention --------------------------------------------

    async def touch(self, owner_id: uuid.UUID, stale_after: timedelta) -> None:
        """Bump ``last_seen_at``, but only if it is already stale.

        The ``WHERE`` clause is the throttle: an identity used fifty times an
        hour writes once. It also means the common case touches no rows at all,
        so the statement is an index lookup and nothing more.

        Failure here is deliberately not fatal to the request — see the caller.
        """
        async with self._db.transaction() as connection:
            await execute(
                connection,
                """
                UPDATE owners
                   SET last_seen_at = now()
                 WHERE id = %s
                   AND last_seen_at < now() - %s::interval
                """,
                (owner_id, stale_after),
            )

    async def expired_owner_ids(self, cutoff: datetime, limit: int) -> list[uuid.UUID]:
        """Identities that look reclaimable: no recordings, not seen since ``cutoff``.

        ``NOT EXISTS`` rather than a join or a count: it stops at the first
        recording, so an owner with a thousand of them costs the same as an
        owner with one. Ordered by ``last_seen_at`` so the index supplies the
        rows already sorted and the oldest are always dealt with first.

        Advisory only. Everything it returns is re-checked under a lock.
        """
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT o.id
                  FROM owners o
                 WHERE o.last_seen_at < %s
                   AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.owner_id = o.id)
                 ORDER BY o.last_seen_at
                 LIMIT %s
                """,
                (cutoff, limit),
            )
        return [uuid.UUID(str(row["id"])) for row in rows]

    async def claim_expired_owner(self, owner_id: uuid.UUID, cutoff: datetime) -> bool:
        """Re-assert eligibility under a row lock, and hold it for the deletion.

        ``FOR UPDATE`` is what makes the races safe. ``touch`` updates the same
        row, so a returning user either lands before this lock — and the
        re-check below sees the new ``last_seen_at`` and refuses — or waits
        until after the owner is gone. Two cleanup runs serialise the same way,
        and ``SKIP LOCKED`` means the second moves on rather than blocking.

        The transaction is **not** closed here: the caller deletes inside it, so
        nothing can slip between the check and the delete.
        """
        async with self._db.transaction() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT o.id
                  FROM owners o
                 WHERE o.id = %s
                   AND o.last_seen_at < %s
                   AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.owner_id = o.id)
                   FOR UPDATE OF o SKIP LOCKED
                """,
                (owner_id, cutoff),
            )
            if row is None:
                return False
            # Deleted here, inside the lock, rather than by the caller: holding
            # a psycopg transaction open across an await in another object
            # would be a far easier thing to get wrong.
            await execute(connection, "DELETE FROM owners WHERE id = %s", (owner_id,))
        return True

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
