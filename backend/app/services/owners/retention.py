"""Reclaiming identities nobody is using.

Step 10.3 measured the problem: an unauthenticated request mints an owner, so a
crawler, a probe or one curious visit leaves an owner row and a credential row
behind. Rate limiting bounded the *rate*; nothing bounded the total, and nothing
ever reclaimed one. At the time of writing this module, four of the five owners
in the development database had never uploaded anything.

**The policy is deliberately the narrowest one that solves that.** An identity
is eligible only when *both* hold:

1. it owns **no recordings**, and
2. it has not been seen for the retention period.

The first condition is what makes this safe rather than a judgement call.
Deleting an empty identity is **invisible to whoever held it**: their key stops
resolving, the resolver mints them a fresh one on the next request, and the
thing they lost was nothing. Deleting an identity that owns recordings is a
different act entirely — it destroys somebody's singing history — and **no
retention requirement for that exists anywhere in this repository**. Inventing
one here would be inventing a product decision, so this module does not.

That gap is recorded in ``docs/limitations.md`` rather than quietly closed.

**Why not age?** Because `owners.created_at` says when somebody arrived, not
whether they are still here. Reading your history writes nothing, so an identity
created a year ago can be in daily use. `owners.last_seen_at` (migration 0003)
is the signal, written when a credential resolves.

Nothing here talks to a database or a filesystem: this module decides *what* is
eligible and the service below asks the repository to act. The eligibility rule
is repeated in SQL for the candidate query — that duplication is deliberate and
:func:`is_eligible` exists so the rule can be tested directly and so the final,
authoritative check happens in Python under a row lock.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.owners.deletion import OwnerDeletionService
from app.services.recordings.models import utc_now

logger = get_logger(__name__)

#: How many candidates one run will consider unless told otherwise. A bound
#: rather than "everything" so a first run against a long-neglected database is
#: a series of short transactions instead of one enormous one.
DEFAULT_BATCH: Final = 500


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """When an unused identity may be reclaimed.

    ``retention`` is configuration, not a constant: the repository specifies no
    retention period anywhere, so a default is a choice rather than a
    requirement and is written down as one. See ``docs/architecture.md``.
    """

    retention: timedelta

    def cutoff(self, now: datetime | None = None) -> datetime:
        """Identities last seen strictly before this are old enough."""
        return (now or utc_now()) - self.retention

    def is_eligible(
        self, *, last_seen_at: datetime, recordings: int, now: datetime | None = None
    ) -> bool:
        """Whether this identity may be reclaimed.

        Both conditions, always. An identity with recordings is never eligible
        however old it is, and an identity seen recently is never eligible
        however empty it is.
        """
        if recordings > 0:
            return False
        return last_seen_at < self.cutoff(now)


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """What a run actually did. Every number is counted, never assumed."""

    #: Candidates the query returned.
    inspected: int
    #: Candidates still eligible when re-checked under a row lock.
    deleted: int
    #: Candidates that stopped being eligible between the query and the lock —
    #: someone came back, or uploaded. Not an error; the point of re-checking.
    skipped: int
    #: Deletions that raised. Counted so a run cannot report success while
    #: having failed.
    failed: int

    @property
    def ok(self) -> bool:
        return self.failed == 0


@runtime_checkable
class RetentionRepository(Protocol):
    """The two database operations retention needs."""

    async def expired_owner_ids(self, cutoff: datetime, limit: int) -> list[uuid.UUID]:
        """Identities that *look* eligible: no recordings, last seen before ``cutoff``.

        Advisory only. Between this query and the deletion an owner may come
        back, so the answer is re-checked under a lock before anything is
        removed.
        """

    async def claim_expired_owner(self, owner_id: uuid.UUID, cutoff: datetime) -> bool:
        """Re-assert eligibility under a row lock **and delete**, atomically.

        Check and delete are one transaction because separating them is the
        race. The lock is what makes the rest safe:

        * two cleanup runs cannot both claim the same owner;
        * a request that resolves this identity updates ``last_seen_at``, taking
          the same row lock, so it either lands before the claim (and the
          re-check refuses) or waits until after the owner is gone.

        The "no recordings" predicate sits in the *same statement* as the
        delete, which is what keeps the audio-before-rows invariant intact: this
        path cannot remove an owner who owns audio, so it never has audio to
        remove. Returns whether a row was actually deleted.
        """


class IdentityRetentionService:
    """Deletes unused identities.

    **It does not reimplement deletion, and it does not need to.** An identity
    is eligible only if it owns no recordings, and the claim re-checks that in
    the same statement that deletes the row — so this path can never remove an
    owner whose audio is still on disk. The invariant
    ``OwnerDeletionService`` exists to protect (files before rows, so a crash
    leaves rows a retry can finish rather than audio nobody can name) is
    therefore preserved by never being engaged.

    That is a claim worth checking rather than asserting, so before claiming
    anything this service asks ``OwnerDeletionService`` what the owner holds and
    refuses any owner it reports as non-empty. Two independent reads have to
    agree before a row is touched.

    ``DELETE /api/v1/identity`` — the path that *does* delete owners with audio —
    is untouched and still goes through ``OwnerDeletionService`` exactly as
    before.
    """

    def __init__(
        self,
        *,
        repository: RetentionRepository,
        deletion: OwnerDeletionService,
        policy: RetentionPolicy,
    ) -> None:
        self._repository = repository
        self._deletion = deletion
        self._policy = policy

    async def run(
        self, *, limit: int = DEFAULT_BATCH, now: datetime | None = None
    ) -> CleanupReport:
        """Reclaim up to ``limit`` unused identities.

        Idempotent: a second run finds nothing left to do, because the first run
        deleted the rows it acted on and the query only ever returns rows that
        still exist.
        """
        cutoff = self._policy.cutoff(now)
        candidates = await self._repository.expired_owner_ids(cutoff, limit)

        deleted = skipped = failed = 0

        for owner_id in candidates:
            try:
                # The second opinion. The candidate query already said this
                # owner has no recordings; asking the deletion service's own
                # read path means two independent statements must agree before
                # anything is removed, and it is what would catch a candidate
                # query that had quietly stopped filtering.
                held = await self._deletion.summary(owner_id)
                if held.recordings > 0:
                    logger.warning("identity_cleanup_refused_non_empty")
                    skipped += 1
                    continue

                claimed = await self._repository.claim_expired_owner(owner_id, cutoff)
            except Exception:
                # Counted, never swallowed: a run that lost a deletion must not
                # be able to report success.
                logger.exception("identity_cleanup_failed")
                failed += 1
                continue

            if not claimed:
                # Came back, or uploaded, between the query and the lock.
                skipped += 1
                continue

            deleted += 1

        logger.info(
            "identity_cleanup_finished",
            extra={
                "inspected": len(candidates),
                "deleted": deleted,
                "skipped": skipped,
                "failed": failed,
                "retention_days": self._policy.retention.days,
            },
        )
        return CleanupReport(
            inspected=len(candidates),
            deleted=deleted,
            skipped=skipped,
            failed=failed,
        )


__all__ = [
    "DEFAULT_BATCH",
    "CleanupReport",
    "IdentityRetentionService",
    "RetentionPolicy",
    "RetentionRepository",
]
