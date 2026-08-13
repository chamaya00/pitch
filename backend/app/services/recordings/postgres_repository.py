"""Recordings in PostgreSQL.

Replaces the JSON document store as the source of truth. Two things change
beyond the storage engine:

**Recordings have an owner.** Every read is scoped by owner id, in SQL, so a
recording belonging to someone else is not filtered out of a result — it is
never selected. Authorisation that happens in a ``WHERE`` clause cannot be
bypassed by a client, which is the point.

**History is an indexed query.** ``recordings_owner_created_idx`` covers
"this owner's recordings, newest first" exactly, replacing the directory scan
that read and parsed every document ever written.

The audio itself stays on disk under ``RecordingStorage``. A database is a poor
place for megabytes of WAV, and nothing here needs to query inside them.
"""

import uuid
from typing import Any, Protocol, runtime_checkable

from psycopg import errors

from app.db.pool import Database, execute, fetch_all, fetch_one
from app.services.comparison.sources import (
    COMPARISON_SOURCES_SQL,
    ComparisonSource,
    source_from_row,
)
from app.services.progress.sources import (
    PROGRESS_SQL,
    ProgressRow,
    row_from_record,
)
from app.services.recordings.history import (
    HISTORY_SQL,
    RecordingHistoryEntry,
    entry_from_row,
)
from app.services.recordings.models import Recording


class RecordingAlreadyExistsError(Exception):
    """A recording already exists under that id."""


@runtime_checkable
class OwnedRecordingRepository(Protocol):
    """Storage-agnostic interface for owned recordings.

    Distinct from the original ``RecordingRepository`` protocol, which has no
    concept of an owner. Every method here takes one, because a method that
    could read a recording without knowing whose it is would be a method that
    could leak one.
    """

    async def create(self, recording: Recording, owner_id: uuid.UUID) -> Recording:
        """Persist a new recording owned by ``owner_id``."""

    async def get(self, recording_id: str, owner_id: uuid.UUID) -> Recording | None:
        """The recording, or ``None`` if it does not exist **or is not theirs**."""

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[Recording]:
        """An owner's recordings, newest first."""

    async def list_history(self, owner_id: uuid.UUID, limit: int) -> list[RecordingHistoryEntry]:
        """An owner's recordings with each one's analysis state, newest first."""

    async def comparison_sources(
        self, owner_id: uuid.UUID, recording_ids: list[str]
    ) -> dict[str, ComparisonSource]:
        """The named recordings and their latest audio analyses, if they are this owner's.

        An id that is unknown or belongs to somebody else is absent from the
        result rather than reported — see ``services/comparison/sources.py``.
        """

    async def progress_rows(self, owner_id: uuid.UUID, limit: int) -> list[ProgressRow]:
        """The owner's most recent ``limit`` recordings as progress rows, oldest first.

        Reads scalars out of the analysis documents rather than the documents
        themselves — see ``services/progress/sources.py`` for why that is the
        difference between a few hundred bytes per recording and most of a
        megabyte.
        """

    async def owner_of(self, recording_id: str) -> uuid.UUID | None:
        """Who owns a recording, without reading it. ``None`` if unknown."""


class PostgresRecordingRepository:
    """Recordings in PostgreSQL, scoped by owner on every read."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(self, recording: Recording, owner_id: uuid.UUID) -> Recording:
        async with self._db.transaction() as connection:
            try:
                await execute(
                    connection,
                    """
                    INSERT INTO recordings (
                        id, owner_id, original_filename, audio_format, duration_seconds,
                        sample_rate, channels, size_bytes, bits_per_sample, created_at, document
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        recording.recording_id,
                        owner_id,
                        recording.original_filename,
                        recording.audio_format,
                        recording.duration_seconds,
                        recording.sample_rate,
                        recording.channels,
                        recording.size_bytes,
                        recording.bits_per_sample,
                        recording.created_at,
                        recording.model_dump_json(),
                    ),
                )
            except errors.UniqueViolation as exc:
                # Ids are server-generated uuid4 hex, so this means a genuine
                # duplicate insert rather than a collision.
                raise RecordingAlreadyExistsError(
                    f"recording {recording.recording_id} already exists"
                ) from exc
        return recording

    async def get(self, recording_id: str, owner_id: uuid.UUID) -> Recording | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT document FROM recordings WHERE id = %s AND owner_id = %s",
                (recording_id, owner_id),
            )
        return None if row is None else _to_recording(row)

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[Recording]:
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection,
                """
                SELECT document FROM recordings
                WHERE owner_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (owner_id, limit),
            )
        return [_to_recording(row) for row in rows]

    async def list_history(self, owner_id: uuid.UUID, limit: int) -> list[RecordingHistoryEntry]:
        async with self._db.connection() as connection:
            rows = await fetch_all(connection, HISTORY_SQL, (owner_id, limit))
        return [entry_from_row(row) for row in rows]

    async def progress_rows(self, owner_id: uuid.UUID, limit: int) -> list[ProgressRow]:
        async with self._db.connection() as connection:
            rows = await fetch_all(connection, PROGRESS_SQL, (owner_id, limit))
        return [row_from_record(row) for row in rows]

    async def comparison_sources(
        self, owner_id: uuid.UUID, recording_ids: list[str]
    ) -> dict[str, ComparisonSource]:
        if not recording_ids:
            return {}
        async with self._db.connection() as connection:
            rows = await fetch_all(
                connection, COMPARISON_SOURCES_SQL, (owner_id, list(recording_ids))
            )
        return {str(row["recording_id"]).strip(): source_from_row(row) for row in rows}

    async def owner_of(self, recording_id: str) -> uuid.UUID | None:
        async with self._db.connection() as connection:
            row = await fetch_one(
                connection, "SELECT owner_id FROM recordings WHERE id = %s", (recording_id,)
            )
        return None if row is None else uuid.UUID(str(row["owner_id"]))


def _to_recording(row: dict[str, Any]) -> Recording:
    """The stored document is authoritative for the domain object."""
    return Recording.model_validate(row["document"])
