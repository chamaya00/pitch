"""Deleting everything one owner has.

The database half of this is a single ``DELETE`` — ``recordings``,
``speech_analyses`` and ``audio_analyses`` all cascade from ``owners`` — so the
only part that needs thought is the part that is *not* in the database.

**The audio bytes live on disk.** A deletion that removed the rows and left the
WAV files behind would report success while leaving every recording on the
server, and would make "delete my data" a false statement. So the ids are read
first, the files are removed, and only then does the owner row go.

That order is deliberate and the failure modes are worth stating:

* A file that cannot be removed is **counted and reported**, not swallowed. The
  deletion still proceeds — leaving the rows behind would make the data
  reachable again, which is worse — but the caller learns the count.
* If the process dies between the files and the row, the rows survive and a
  retry finishes the job. The reverse order would leave orphaned audio nobody
  can name.

There is no soft delete and no "deleted" flag. Somebody asking to remove their
recordings is asking for them to be gone, and a row that still exists is not
gone.
"""

import uuid
from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.audio.storage import RecordingStorage, StorageError
from app.services.owners.identity import OwnerDataRepository, OwnerDataSummary

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DeletionReport:
    """What was actually removed. Returned so a caller need not assume."""

    recordings: int
    #: Stored audio files removed from disk.
    audio_files_deleted: int
    #: Files that could not be removed. Reported rather than hidden; the rows
    #: were still deleted, so these are unreachable but present on disk.
    audio_files_failed: int
    #: Whether the owner row existed and was removed.
    owner_deleted: bool


class OwnerDeletionService:
    """Removes an owner and everything belonging to them."""

    def __init__(self, *, owners: OwnerDataRepository, storage: RecordingStorage) -> None:
        self._owners = owners
        self._storage = storage

    async def summary(self, owner_id: uuid.UUID) -> OwnerDataSummary:
        """What this owner would lose. Read-only."""
        return await self._owners.data_summary(owner_id)

    async def delete_everything(self, owner_id: uuid.UUID) -> DeletionReport:
        """Remove the stored audio, then the owner and everything cascading from it."""
        recording_ids = await self._owners.recording_ids(owner_id)

        deleted = 0
        failed = 0
        for recording_id in recording_ids:
            try:
                if self._storage.delete(recording_id):
                    deleted += 1
            except StorageError:
                # Named in the report rather than raised: stopping here would
                # leave the rows in place and the data reachable again.
                failed += 1

        owner_deleted = await self._owners.delete_owner(owner_id)

        logger.info(
            "owner_deleted",
            extra={
                # The id is safe to log; the token is not, and is not available
                # here in any form.
                "owner_id": str(owner_id),
                "recordings": len(recording_ids),
                "audio_files_deleted": deleted,
                "audio_files_failed": failed,
            },
        )
        return DeletionReport(
            recordings=len(recording_ids),
            audio_files_deleted=deleted,
            audio_files_failed=failed,
            owner_deleted=owner_deleted,
        )
