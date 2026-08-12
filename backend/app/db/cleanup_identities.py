"""Reclaim identities nobody is using.

Run it with::

    python -m app.db.cleanup_identities            # do it
    python -m app.db.cleanup_identities --dry-run  # say what it would do

**An operational command, not an endpoint.** There is deliberately no HTTP route
that triggers this: a route would be either unauthenticated — letting a stranger
drive deletion — or authenticated, in which case the caller would have to name
an owner, and no API in this project lets a caller name somebody else's owner id.
Whoever can run this can already reach the database. That boundary is the point.

**There is no scheduler in this repository**, and this step deliberately does not
add one. Adding a job runner to delete some rows would be more infrastructure
than the problem justifies. The command is written to be safe to run from cron,
from a deploy hook, or by hand, as often as you like: it is idempotent, it
bounds its own work with ``--limit``, and a second run finds nothing left.

It exits non-zero if anything failed, so a scheduler that checks exit codes will
notice — a cleanup that quietly half-worked is worse than one that did not run.
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.pool import Database
from app.services.audio.storage import RecordingStorage
from app.services.owners.deletion import OwnerDeletionService
from app.services.owners.repository import PostgresOwnerRepository
from app.services.owners.retention import (
    DEFAULT_BATCH,
    CleanupReport,
    IdentityRetentionService,
    RetentionPolicy,
)

logger = get_logger(__name__)


async def cleanup(*, limit: int, dry_run: bool) -> CleanupReport:
    """Run one pass. Opens its own pool; nothing here shares the API's."""
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set; there is nothing to clean up.")

    policy = RetentionPolicy(retention=settings.identity_retention)
    database = Database(settings.database_url)
    await database.open()
    try:
        repository = PostgresOwnerRepository(database)
        if dry_run:
            # Deliberately only the candidate query: a dry run must not lock a
            # row, must not delete anything, and must not be able to.
            candidates = await repository.expired_owner_ids(policy.cutoff(), limit)
            return CleanupReport(
                inspected=len(candidates), deleted=0, skipped=len(candidates), failed=0
            )

        service = IdentityRetentionService(
            repository=repository,
            deletion=OwnerDeletionService(
                owners=repository, storage=RecordingStorage(settings.storage_root)
            ),
            policy=policy,
        )
        return await service.run(limit=limit)
    finally:
        await database.close()


def main() -> None:  # pragma: no cover - argument plumbing
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH,
        help=f"How many candidates to consider in one pass (default {DEFAULT_BATCH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be reclaimed without touching anything.",
    )
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    report = asyncio.run(cleanup(limit=args.limit, dry_run=args.dry_run))

    verb = "would reclaim" if args.dry_run else "reclaimed"
    count = report.inspected if args.dry_run else report.deleted
    print(
        f"inspected {report.inspected}, {verb} {count}, "
        f"skipped {report.skipped}, failed {report.failed}"
    )
    if not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    main()
