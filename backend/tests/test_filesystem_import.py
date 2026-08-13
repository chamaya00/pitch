"""Migrating pre-7M filesystem state into PostgreSQL.

Needs a real database: the properties under test are foreign keys, ``ON CONFLICT
DO NOTHING`` and what a second run does, none of which an in-memory double can
stand in for honestly.

The rule that matters most is the one asserted first and last: **the JSON
documents are still there afterwards.** An import that deletes its source has no
safe failure mode.
"""

import os
import uuid
from pathlib import Path
from typing import Any

import anyio
import pytest

from app.db.import_filesystem import import_filesystem
from app.db.migrate import apply_migrations
from app.db.pool import Database, fetch_all, fetch_one
from app.services.analysis.models import Analysis, AnalysisStatus, new_analysis_id
from app.services.analysis.repository import JsonFileAnalysisRepository
from app.services.audio_analysis.models import AudioAnalysis, new_audio_analysis_id
from app.services.audio_analysis.repository import JsonFileAudioAnalysisRepository
from app.services.owners.models import new_owner
from app.services.recordings.models import Recording
from app.services.recordings.repository import JsonFileRecordingRepository

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is unset; the import writes to a real database",
)


async def _fresh(work: Any) -> Any:
    database = Database(DATABASE_URL)
    await database.open()
    try:
        async with database.transaction() as connection:
            await apply_migrations(connection)
        async with database.transaction() as connection:
            await connection.execute("TRUNCATE owners CASCADE")
        return await work(database)
    finally:
        await database.close()


def with_db(work: Any) -> Any:
    return anyio.run(lambda: _fresh(work))


def seed_filesystem(root: Path, *, recordings: int = 2, orphan: bool = False) -> dict[str, Any]:
    """Write the JSON documents a pre-7M installation would have."""
    recording_store = JsonFileRecordingRepository(root)
    analysis_store = JsonFileAnalysisRepository(root)
    audio_store = JsonFileAudioAnalysisRepository(root)

    ids = []
    for index in range(recordings):
        stored = recording_store.create(
            Recording(
                recording_id=uuid.uuid4().hex,
                original_filename=f"take-{index}.wav",
                audio_format="wav",
                duration_seconds=2.0,
                sample_rate=22050,
                channels=1,
                size_bytes=4096,
            )
        )
        ids.append(stored.recording_id)

    analysis_store.create(Analysis(analysis_id=new_analysis_id(), recording_id=ids[0]))
    audio_store.create(
        AudioAnalysis(audio_analysis_id=new_audio_analysis_id(), recording_id=ids[0])
    )
    if orphan:
        # An analysis whose recording document was deleted at some point.
        analysis_store.create(
            Analysis(analysis_id=new_analysis_id(), recording_id=uuid.uuid4().hex)
        )
    return {"ids": ids, "root": root}


def test_the_import_moves_recordings_and_analyses(tmp_path: Path) -> None:
    seeded = seed_filesystem(tmp_path)

    async def work(database: Database) -> None:
        owner, token = new_owner()
        report = await import_filesystem(database, tmp_path, owner=owner, token=token)

        assert report.recordings == 2
        assert report.speech_analyses == 1
        assert report.audio_analyses == 1
        assert report.skipped == []

        async with database.connection() as connection:
            rows = await fetch_all(
                connection, "SELECT id FROM recordings WHERE owner_id = %s", (owner.owner_id,)
            )
        assert {row["id"] for row in rows} == set(seeded["ids"])

    with_db(work)


def test_the_import_never_deletes_the_source_documents(tmp_path: Path) -> None:
    """A migration with no safe failure mode is not a migration."""
    seed_filesystem(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.json"))

    async def work(database: Database) -> None:
        owner, token = new_owner()
        await import_filesystem(database, tmp_path, owner=owner, token=token)

    with_db(work)

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.json"))
    assert after == before
    assert before, "the fixture must actually have written documents"


def test_running_the_import_twice_imports_each_record_once(tmp_path: Path) -> None:
    """The honest response to a partial failure is to fix it and re-run."""
    seed_filesystem(tmp_path)

    async def work(database: Database) -> None:
        owner, token = new_owner()
        first = await import_filesystem(database, tmp_path, owner=owner, token=token)
        second = await import_filesystem(database, tmp_path, owner=owner, token=token)

        assert first.recordings == 2
        assert second.recordings == 0, "a second run must not duplicate anything"
        assert second.speech_analyses == 0

        async with database.connection() as connection:
            row = await fetch_one(connection, "SELECT count(*) AS total FROM recordings")
        assert row is not None
        assert row["total"] == 2

    with_db(work)


def test_an_analysis_without_its_recording_is_skipped_and_named(tmp_path: Path) -> None:
    """A row pointing at a recording that does not exist would fail the foreign key."""
    seed_filesystem(tmp_path, orphan=True)

    async def work(database: Database) -> None:
        owner, token = new_owner()
        report = await import_filesystem(database, tmp_path, owner=owner, token=token)

        assert report.speech_analyses == 1
        assert len(report.skipped) == 1
        assert "is not in the filesystem store" in report.skipped[0]

    with_db(work)


def test_an_unreadable_document_is_skipped_rather_than_stopping_the_import(
    tmp_path: Path,
) -> None:
    seed_filesystem(tmp_path)
    (JsonFileRecordingRepository(tmp_path).directory / "broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    async def work(database: Database) -> None:
        owner, token = new_owner()
        report = await import_filesystem(database, tmp_path, owner=owner, token=token)

        assert report.recordings == 2
        assert any("broken.json" in reason for reason in report.skipped)

    with_db(work)


def test_the_imported_owner_can_read_its_history(tmp_path: Path) -> None:
    """The point of the import: the recordings are reachable afterwards."""
    from app.services.recordings.postgres_repository import PostgresRecordingRepository

    seed_filesystem(tmp_path)

    async def work(database: Database) -> None:
        owner, token = new_owner()
        await import_filesystem(database, tmp_path, owner=owner, token=token)

        repository = PostgresRecordingRepository(database)
        history = await repository.list_history(owner.owner_id, 10)

        assert len(history) == 2
        analysed = [entry for entry in history if entry.speech_status is not None]
        assert len(analysed) == 1
        assert analysed[0].speech_status is AnalysisStatus.PENDING

    with_db(work)


def test_an_empty_storage_root_imports_nothing_and_does_not_fail(tmp_path: Path) -> None:
    async def work(database: Database) -> None:
        owner, token = new_owner()
        report = await import_filesystem(database, tmp_path, owner=owner, token=token)

        assert report.recordings == 0
        assert report.skipped == []

    with_db(work)
