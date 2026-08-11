"""The PostgreSQL foundation, against a real database.

**No mocks.** A fake connection cannot tell you whether a partial unique index
does what its comment claims, and that index is the entire reason this layer
exists — the process-local ``asyncio.Lock`` it replaces was never able to
serialise anything outside one interpreter.

These tests are skipped when ``TEST_DATABASE_URL`` is unset, so a checkout
without a database still runs the rest of the suite. They are not skipped
quietly in CI: a run with no database says so in the skip reason.
"""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anyio
import pytest

from app.core.errors import ErrorCode
from app.db.migrate import apply_migrations, migration_files
from app.db.pool import Database, fetch_all, fetch_one
from app.services.analysis.models import Analysis, AnalysisStatus, new_analysis_id
from app.services.analysis.postgres_repository import (
    ActiveAnalysisExistsError,
    AnalysisConflictError,
    PostgresAnalysisRepository,
)
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
    new_audio_analysis_id,
)
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
    PostgresAudioAnalysisRepository,
)
from app.services.owners.models import Owner, hash_token, new_owner, new_owner_token
from app.services.owners.repository import PostgresOwnerRepository
from app.services.recordings.models import Recording
from app.services.recordings.postgres_repository import (
    PostgresRecordingRepository,
    RecordingAlreadyExistsError,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is unset; PostgreSQL tests need a real database",
)


def run(factory: Any) -> Any:
    return anyio.run(factory)


async def _fresh_database() -> Database:
    """A migrated, empty database. Truncating owners cascades to everything."""
    database = Database(DATABASE_URL)
    await database.open()
    async with database.transaction() as connection:
        await apply_migrations(connection)
    async with database.transaction() as connection:
        await connection.execute("TRUNCATE owners CASCADE")
    return database


async def _with_database(work: Any) -> Any:
    database = await _fresh_database()
    try:
        return await work(database)
    finally:
        await database.close()


def with_db(work: Any) -> Any:
    """Run ``work(database)`` against a fresh schema."""
    return run(lambda: _with_database(work))


async def _make_owner(database: Database) -> tuple[Owner, str]:
    owner, token = new_owner()
    await PostgresOwnerRepository(database).create(owner, token)
    return owner, token


def recording(recording_id: str | None = None, **overrides: Any) -> Recording:
    base: dict[str, Any] = {
        "recording_id": recording_id or uuid.uuid4().hex,
        "original_filename": "take.wav",
        "audio_format": "wav",
        "duration_seconds": 2.0,
        "sample_rate": 22050,
        "channels": 1,
        "size_bytes": 1024,
    }
    base.update(overrides)
    return Recording(**base)


# --- Schema and migrations -------------------------------------------------


def test_migrations_create_the_schema() -> None:
    async def work(database: Database) -> None:
        async with database.connection() as connection:
            rows = await fetch_all(
                connection,
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
            )
        tables = {row["table_name"] for row in rows}
        assert {"owners", "recordings", "speech_analyses", "audio_analyses"} <= tables
        assert "schema_migrations" in tables

    with_db(work)


def test_migrations_are_idempotent() -> None:
    """Applying twice must be a no-op, because startup applies them every boot."""

    async def work(database: Database) -> None:
        async with database.transaction() as connection:
            assert await apply_migrations(connection) == []

    with_db(work)


def test_an_edited_migration_is_refused_rather_than_reapplied(tmp_path: Any) -> None:
    """Two databases must never share a recorded history and differ in schema."""

    async def work(database: Database) -> None:
        directory = tmp_path / "migrations"
        directory.mkdir()
        first = directory / "0001_probe.sql"
        first.write_text("CREATE TABLE migration_probe (id INT)", encoding="utf-8")

        try:
            async with database.transaction() as connection:
                assert await apply_migrations(connection, directory) == ["0001_probe.sql"]

            first.write_text("CREATE TABLE migration_probe (id BIGINT)", encoding="utf-8")
            with pytest.raises(RuntimeError, match="has changed"):
                async with database.transaction() as connection:
                    await apply_migrations(connection, directory)
        finally:
            # This test writes into the *shared* migration ledger, which
            # ``_fresh_database`` deliberately does not truncate — the real
            # migrations have to survive between tests. Undoing it here is what
            # keeps the suite re-runnable against one database.
            async with database.transaction() as connection:
                await connection.execute("DROP TABLE IF EXISTS migration_probe")
                await connection.execute(
                    "DELETE FROM schema_migrations WHERE name = %s", ("0001_probe.sql",)
                )

    with_db(work)


def test_every_migration_is_numbered_so_the_order_is_total() -> None:
    for path in migration_files():
        assert path.name[:4].isdigit(), f"{path.name} has no ordering prefix"


# --- Owners ----------------------------------------------------------------


def test_an_owner_round_trips_by_token() -> None:
    async def work(database: Database) -> None:
        owners = PostgresOwnerRepository(database)
        owner, token = new_owner()
        await owners.create(owner, token)

        resolved = await owners.get_by_token(token)
        assert resolved is not None
        assert resolved.owner_id == owner.owner_id

    with_db(work)


def test_only_a_hash_of_the_token_is_stored() -> None:
    """A leaked database must yield no usable tokens."""

    async def work(database: Database) -> None:
        owner, token = new_owner()
        await PostgresOwnerRepository(database).create(owner, token)

        async with database.connection() as connection:
            row = await fetch_one(connection, "SELECT token_hash FROM owners LIMIT 1")
        assert row is not None
        assert row["token_hash"] != token
        assert row["token_hash"] == hash_token(token)

    with_db(work)


def test_an_unknown_token_resolves_to_nobody() -> None:
    async def work(database: Database) -> None:
        assert await PostgresOwnerRepository(database).get_by_token(new_owner_token()) is None

    with_db(work)


# --- Recordings and ownership ----------------------------------------------


def test_a_recording_round_trips() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)
        stored = recording(original_filename="song.wav", duration_seconds=12.5)
        await repository.create(stored, owner.owner_id)

        loaded = await repository.get(stored.recording_id, owner.owner_id)
        assert loaded is not None
        assert loaded.original_filename == "song.wav"
        assert loaded.duration_seconds == 12.5

    with_db(work)


def test_one_owner_cannot_read_another_owners_recording() -> None:
    """Enforced in the WHERE clause, so it cannot be bypassed by a client."""

    async def work(database: Database) -> None:
        mine, _ = await _make_owner(database)
        theirs, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)
        stored = recording()
        await repository.create(stored, mine.owner_id)

        assert await repository.get(stored.recording_id, theirs.owner_id) is None
        assert await repository.list_for_owner(theirs.owner_id, 10) == []

    with_db(work)


def test_history_lists_an_owners_recordings_newest_first() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)

        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        older = recording(created_at=now - timedelta(minutes=5), original_filename="older.wav")
        newer = recording(created_at=now, original_filename="newer.wav")
        await repository.create(older, owner.owner_id)
        await repository.create(newer, owner.owner_id)

        listed = await repository.list_for_owner(owner.owner_id, 10)
        assert [item.original_filename for item in listed] == ["newer.wav", "older.wav"]

    with_db(work)


def test_history_respects_its_limit() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)
        for _ in range(5):
            await repository.create(recording(), owner.owner_id)

        assert len(await repository.list_for_owner(owner.owner_id, 3)) == 3

    with_db(work)


def test_creating_the_same_recording_twice_is_refused() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)
        stored = recording()
        await repository.create(stored, owner.owner_id)

        with pytest.raises(RecordingAlreadyExistsError):
            await repository.create(stored, owner.owner_id)

    with_db(work)


def test_a_recording_cannot_belong_to_an_owner_that_does_not_exist() -> None:
    """A foreign key, so an orphaned recording is impossible rather than unlikely."""

    async def work(database: Database) -> None:
        from psycopg import errors

        with pytest.raises(errors.ForeignKeyViolation):
            await PostgresRecordingRepository(database).create(recording(), uuid.uuid4())

    with_db(work)


# --- Speech analysis concurrency -------------------------------------------


def test_only_one_speech_analysis_can_be_in_flight_per_recording() -> None:
    """The constraint that replaces the process-local lock."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)

        analyses = PostgresAnalysisRepository(database)
        await analyses.create(
            Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
        )

        with pytest.raises(ActiveAnalysisExistsError):
            await analyses.create(
                Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
            )

    with_db(work)


def test_concurrent_starts_produce_exactly_one_analysis() -> None:
    """Eight coroutines racing. Seven must lose, and lose safely."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAnalysisRepository(database)

        created: list[str] = []
        refused: list[str] = []

        async def attempt() -> None:
            try:
                made = await analyses.create(
                    Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
                )
                created.append(made.analysis_id)
            except ActiveAnalysisExistsError:
                refused.append("refused")

        async with anyio.create_task_group() as group:
            for _ in range(8):
                group.start_soon(attempt)

        assert len(created) == 1, f"{len(created)} analyses were created"
        assert len(refused) == 7
        assert len(await analyses.list_for_recording(stored.recording_id)) == 1

    with_db(work)


def test_a_finished_analysis_frees_the_recording_for_another() -> None:
    """The index is partial: terminal records do not block a retry."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAnalysisRepository(database)

        first = Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
        await analyses.create(first)
        await analyses.update(
            first.model_copy(
                update={
                    "status": AnalysisStatus.FAILED,
                    "error_code": ErrorCode.ANALYSIS_PROVIDER_ERROR,
                }
            ),
            expect_status=AnalysisStatus.PENDING,
        )

        second = await analyses.create(
            Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
        )
        assert second.analysis_id != first.analysis_id

    with_db(work)


def test_a_stale_worker_cannot_overwrite_a_finished_analysis() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAnalysisRepository(database)

        pending = Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
        await analyses.create(pending)

        # A sweep marks it failed while a slow worker still holds the old copy.
        await analyses.update(
            pending.model_copy(
                update={"status": AnalysisStatus.FAILED, "error_code": ErrorCode.INTERNAL_ERROR}
            ),
            expect_status=AnalysisStatus.PENDING,
        )

        with pytest.raises(AnalysisConflictError):
            await analyses.update(
                pending.model_copy(update={"status": AnalysisStatus.TRANSCRIBING}),
                expect_status=AnalysisStatus.PENDING,
            )

        current = await analyses.get(pending.analysis_id)
        assert current is not None
        assert current.status is AnalysisStatus.FAILED

    with_db(work)


def test_the_stored_columns_agree_with_the_stored_document() -> None:
    """The columns are projections. A disagreement would make queries lie."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAnalysisRepository(database)

        analysis = Analysis(analysis_id=new_analysis_id(), recording_id=stored.recording_id)
        await analyses.create(analysis)
        await analyses.update(
            analysis.model_copy(update={"status": AnalysisStatus.TRANSCRIBING}),
            expect_status=AnalysisStatus.PENDING,
        )

        async with database.connection() as connection:
            row = await fetch_one(
                connection,
                "SELECT status, document->>'status' AS document_status FROM speech_analyses",
            )
        assert row is not None
        assert row["status"] == row["document_status"] == "transcribing"

    with_db(work)


# --- Audio analysis concurrency --------------------------------------------


def test_only_one_audio_analysis_can_be_in_flight_per_recording() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAudioAnalysisRepository(database)

        await analyses.create(
            AudioAnalysis(
                audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
            )
        )
        with pytest.raises(ActiveAudioAnalysisExistsError):
            await analyses.create(
                AudioAnalysis(
                    audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
                )
            )

    with_db(work)


def test_a_stale_worker_cannot_overwrite_a_finished_audio_analysis() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAudioAnalysisRepository(database)

        pending = AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
        )
        await analyses.create(pending)
        await analyses.update(
            pending.model_copy(
                update={
                    "status": AudioAnalysisStatus.FAILED,
                    "error_code": ErrorCode.INSUFFICIENT_PITCH_SIGNAL,
                }
            ),
            expect_status=AudioAnalysisStatus.PENDING,
        )

        with pytest.raises(AudioAnalysisConflictError):
            await analyses.update(
                pending.model_copy(update={"status": AudioAnalysisStatus.ANALYZING}),
                expect_status=AudioAnalysisStatus.PENDING,
            )

    with_db(work)


def test_concurrent_feedback_claims_produce_exactly_one_winner() -> None:
    """Every duplicate here would be a paid provider call."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAudioAnalysisRepository(database)

        from tests.test_audio_feedback import metrics

        analysis = AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
        )
        await analyses.create(analysis)
        await analyses.update(
            analysis.model_copy(
                update={"status": AudioAnalysisStatus.COMPLETED, "metrics": metrics()}
            ),
            expect_status=AudioAnalysisStatus.PENDING,
        )

        claims: list[AudioAnalysis | None] = []

        async def attempt() -> None:
            claims.append(await analyses.claim_feedback(analysis.audio_analysis_id))

        async with anyio.create_task_group() as group:
            for _ in range(6):
                group.start_soon(attempt)

        won = [claim for claim in claims if claim is not None]
        assert len(won) == 1, f"{len(won)} workers claimed the same feedback run"
        assert won[0].feedback_status is AudioFeedbackStatus.GENERATING

    with_db(work)


def test_a_claim_is_refused_unless_the_analysis_completed() -> None:
    """A model is never asked to interpret an analysis that has not finished."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAudioAnalysisRepository(database)

        pending = AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
        )
        await analyses.create(pending)
        assert await analyses.claim_feedback(pending.audio_analysis_id) is None

    with_db(work)


# --- Transactions ----------------------------------------------------------


def test_a_failed_transaction_leaves_nothing_behind() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        repository = PostgresRecordingRepository(database)

        with pytest.raises(RuntimeError):
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    INSERT INTO recordings (
                        id, owner_id, original_filename, audio_format, duration_seconds,
                        sample_rate, channels, size_bytes, created_at, document
                    ) VALUES (%s, %s, 'x.wav', 'wav', 1, 22050, 1, 10, now(), '{}'::jsonb)
                    """,
                    (uuid.uuid4().hex, owner.owner_id),
                )
                raise RuntimeError("something went wrong after the insert")

        assert await repository.list_for_owner(owner.owner_id, 10) == []

    with_db(work)


def test_deleting_an_owner_takes_their_recordings_with_it() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)

        async with database.transaction() as connection:
            await connection.execute("DELETE FROM owners WHERE id = %s", (owner.owner_id,))

        async with database.connection() as connection:
            rows = await fetch_all(connection, "SELECT id FROM recordings")
        assert rows == []

    with_db(work)


# --- Isolation of the async iterator used above ----------------------------


async def _unused() -> AsyncIterator[None]:  # pragma: no cover - typing anchor
    yield None
