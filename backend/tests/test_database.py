"""The PostgreSQL foundation, against a real database.

**No mocks.** A fake connection cannot tell you whether a partial unique index
does what its comment claims, and that index is the entire reason this layer
exists — the process-local ``asyncio.Lock`` it replaces was never able to
serialise anything outside one interpreter.

These tests are skipped when ``TEST_DATABASE_URL`` is unset, so a checkout
without a database still runs the rest of the suite. They are not skipped
quietly in CI: a run with no database says so in the skip reason.
"""

import inspect
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import pytest

from app.core.errors import ErrorCode
from app.db.migrate import MIGRATIONS_DIR, apply_migrations, migration_files
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
    PitchPoint,
    new_audio_analysis_id,
)
from app.services.audio_analysis.postgres_repository import (
    _DECIMATED_SQL,
    _SUMMARY_COLUMNS,
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
    PostgresAudioAnalysisRepository,
)
from app.services.owners.credential_repository import PostgresCredentialRepository
from app.services.owners.credentials import (
    CredentialExistsError,
    LastCredentialError,
    new_credential,
)
from app.services.owners.models import Owner, hash_token, new_owner, new_owner_token
from app.services.owners.repository import PostgresOwnerRepository
from app.services.progress.sources import PROGRESS_SQL
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


def run_at_utc() -> datetime:
    """`now()` as the retention code sees it."""
    return datetime.now(UTC)


async def _warm_pool(database: Database, connections: int) -> None:
    """Open several real connections at once so a race is actually a race."""

    async def hold(started: anyio.Event, release: anyio.Event) -> None:
        async with database.connection() as connection:
            await connection.execute("SELECT 1")
            started.set()
            await release.wait()

    release = anyio.Event()
    async with anyio.create_task_group() as group:
        events = [anyio.Event() for _ in range(connections)]
        for event in events:
            group.start_soon(hold, event, release)
        for event in events:
            await event.wait()
        release.set()


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


def audio_metrics() -> Any:
    """The metrics block a completed audio analysis must carry.

    Imported where it is used, as the feedback-claim test above does, so this
    module keeps its own import list to the things it is testing.
    """
    from tests.test_audio_feedback import metrics

    return metrics()


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


def test_workers_starting_together_against_an_empty_database_all_survive(tmp_path: Any) -> None:
    """First boot with several workers, which Step 10.10 made a supported shape.

    ``apply_migrations`` created ``schema_migrations`` *before* taking the
    advisory lock, and ``CREATE TABLE IF NOT EXISTS`` is not atomic against a
    concurrent create: two transactions both find it missing, both insert a
    ``pg_type`` row, and the loser dies on ``pg_type_typname_nsp_index``. The
    docstring claimed concurrent calls were safe; they were not, and the failure
    was found by running the API under two uvicorn workers rather than by
    reading. Six workers against a fresh schema failed 25 boots out of 30.

    It only ever bit an **empty** database, which is why no existing test saw
    it: every other migration test runs against one that is already migrated,
    where ``IF NOT EXISTS`` short-circuits and the race has nothing to lose.

    Run in a scratch schema so ``public`` — which the rest of this suite needs
    migrated — is never dropped.
    """
    schema = "boot_race_probe"
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_probe.sql").write_text("CREATE TABLE probe (id INT)", encoding="utf-8")

    async def work(database: Database) -> None:
        async with database.transaction() as connection:
            await connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await connection.execute(f"CREATE SCHEMA {schema}")
        try:
            failures: list[str] = []

            async def boot() -> None:
                # Its own connection, as its own process would have. The
                # bookkeeping table is created inside the scratch schema, so
                # every one of these starts from "no schema_migrations".
                try:
                    async with database.transaction() as connection:
                        await connection.execute(f"SET LOCAL search_path TO {schema}")
                        await apply_migrations(connection, directory)
                except Exception as exc:  # noqa: BLE001 - the failure is the assertion
                    failures.append(f"{type(exc).__name__}: {exc}")

            async with anyio.create_task_group() as tasks:
                for _ in range(6):
                    tasks.start_soon(boot)

            assert failures == [], f"a worker died on first boot: {failures[0]}"

            # And they applied it once between them, not once each.
            async with database.connection() as connection:
                rows = await fetch_all(
                    connection,
                    f"SELECT name FROM {schema}.schema_migrations",  # noqa: S608
                )
            assert [row["name"] for row in rows] == ["0001_probe.sql"]
        finally:
            async with database.transaction() as connection:
                await connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    with_db(work)


def test_an_owner_created_before_the_credentials_migration_survives_it(tmp_path: Any) -> None:
    """The one property the credentials migration exists to preserve.

    Seeded through the *old* schema — an owner whose key is a column on
    ``owners`` — and then migrated. Afterwards the same key must resolve to the
    same owner id, and that owner's recording must still be theirs. Anything
    less would mean the migration reassigned somebody's history.

    The staging is done in a schema of its own so the shared test database keeps
    its already-migrated tables; ``search_path`` sends every unqualified name in
    the migration files there instead.
    """

    async def work(database: Database) -> None:
        staged = tmp_path / "staged"
        staged.mkdir()
        files = migration_files()
        initial, rest = files[0], files[1:]
        assert rest, "this test is meaningless with only one migration"
        # Copied byte for byte, so the checksum matches and the second pass
        # applies only what came after it.
        (staged / initial.name).write_text(initial.read_text(encoding="utf-8"), encoding="utf-8")

        owner_id = uuid.uuid4()
        token = new_owner_token()
        recording_id = uuid.uuid4().hex

        try:
            # --- The world as it was before this step ---
            async with database.transaction() as connection:
                await connection.execute("CREATE SCHEMA migration_probe")
                await connection.execute("SET LOCAL search_path TO migration_probe")
                await apply_migrations(connection, staged)

            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO migration_probe")
                await connection.execute(
                    "INSERT INTO owners (id, token_hash, created_at) VALUES (%s, %s, now())",
                    (owner_id, hash_token(token)),
                )
                await connection.execute(
                    """
                    INSERT INTO recordings (id, owner_id, original_filename, audio_format,
                                            duration_seconds, sample_rate, channels, size_bytes,
                                            created_at, document)
                    VALUES (%s, %s, 'take.wav', 'wav', 2.0, 22050, 1, 1024, now(), '{}'::jsonb)
                    """,
                    (recording_id, owner_id),
                )

            # --- Migrate ---
            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO migration_probe")
                applied = await apply_migrations(connection, MIGRATIONS_DIR)
                assert applied == [path.name for path in rest]

            # --- The same key, the same owner, the same recording ---
            async with database.connection() as connection:
                await connection.execute("SET search_path TO migration_probe")
                resolved = await fetch_one(
                    connection,
                    """
                    SELECT o.id, c.label
                      FROM credentials c JOIN owners o ON o.id = c.owner_id
                     WHERE c.credential_hash = %s
                    """,
                    (hash_token(token),),
                )
                assert resolved is not None
                assert resolved["id"] == owner_id
                assert resolved["label"] == "Original key"

                still_theirs = await fetch_one(
                    connection,
                    "SELECT owner_id FROM recordings WHERE id = %s",
                    (recording_id,),
                )
                assert still_theirs is not None
                assert still_theirs["owner_id"] == owner_id

                # And the column that used to be the identity is gone, not left
                # behind carrying a stale UNIQUE index.
                columns = await fetch_all(
                    connection,
                    """
                    SELECT column_name FROM information_schema.columns
                     WHERE table_schema = 'migration_probe' AND table_name = 'owners'
                    """,
                )
                assert "token_hash" not in {row["column_name"] for row in columns}
        finally:
            # The ledger this wrote lives in the probe schema and goes with it;
            # the real one in ``public`` was never touched.
            async with database.transaction() as connection:
                await connection.execute("DROP SCHEMA IF EXISTS migration_probe CASCADE")

    with_db(work)


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
            row = await fetch_one(
                connection,
                "SELECT credential_hash FROM credentials WHERE owner_id = %s",
                (owner.owner_id,),
            )
        assert row is not None
        assert row["credential_hash"] != token
        assert row["credential_hash"] == hash_token(token)

    with_db(work)


def test_an_unknown_token_resolves_to_nobody() -> None:
    async def work(database: Database) -> None:
        assert await PostgresOwnerRepository(database).get_by_token(new_owner_token()) is None

    with_db(work)


# --- Credentials -----------------------------------------------------------
#
# The interface-level rules live in ``test_repository_contract``. What is here
# is what only a database can be asked: a unique index that holds across
# connections, a cascade the schema performs, and a row lock that two concurrent
# transactions actually contend for.


def test_a_credential_hash_is_unique_across_connections() -> None:
    """Not a Python check — the constraint, exercised through two transactions."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        other, _ = await _make_owner(database)
        credentials = PostgresCredentialRepository(database)
        first, key = new_credential(owner.owner_id, "Phone")
        await credentials.create(first, key)

        second, _ = new_credential(other.owner_id, "Stolen")
        with pytest.raises(CredentialExistsError):
            await credentials.create(second, key)

        assert await credentials.owner_for_key(key) == owner.owner_id

    with_db(work)


def test_deleting_an_owner_cascades_to_its_credentials() -> None:
    """The foreign key does this, not application code."""

    async def work(database: Database) -> None:
        owner, token = await _make_owner(database)
        credentials = PostgresCredentialRepository(database)
        extra, key = new_credential(owner.owner_id, "Phone")
        await credentials.create(extra, key)

        await PostgresOwnerRepository(database).delete_owner(owner.owner_id)

        async with database.connection() as connection:
            rows = await fetch_all(connection, "SELECT id FROM credentials")
        assert rows == []
        assert await credentials.owner_for_key(token) is None

    with_db(work)


def test_concurrent_revocations_cannot_strand_an_identity() -> None:
    """Two racing revocations of an owner's two keys. One must lose.

    Without the row lock both transactions count two credentials, both delete
    one, and the owner is left with none — recordings still theirs, and no way
    in. The rule "never the last" is only true if it is enforced against a
    concurrent revocation, which is what this asserts.

    The pool is warmed first, and that is load-bearing rather than tidy-up.
    ``min_size`` is 1, so the second task would otherwise spend the race opening
    a TCP connection and authenticating while the first ran to completion: the
    two transactions never overlap and the test passes with the lock removed.
    Removing ``FOR UPDATE`` must make this fail, so the connections have to
    already exist when the race starts.
    """

    async def work(database: Database) -> None:
        owner, first_key = await _make_owner(database)
        credentials = PostgresCredentialRepository(database)
        first = (await credentials.list_for_owner(owner.owner_id))[0]
        second, second_key = new_credential(owner.owner_id, "Phone")
        await credentials.create(second, second_key)

        async with database.connection() as warm_one, database.connection() as warm_two:
            await warm_one.execute("SELECT 1")
            await warm_two.execute("SELECT 1")

        removed: list[uuid.UUID] = []
        refused: list[str] = []

        async def attempt(credential_id: uuid.UUID) -> None:
            try:
                if await credentials.revoke(credential_id, owner.owner_id):
                    removed.append(credential_id)
            except LastCredentialError:
                refused.append("refused")

        async with anyio.create_task_group() as group:
            group.start_soon(attempt, first.credential_id)
            group.start_soon(attempt, second.credential_id)

        assert len(removed) == 1, f"{len(removed)} credentials were removed"
        assert len(refused) == 1
        remaining = await credentials.list_for_owner(owner.owner_id)
        assert len(remaining) == 1
        # And the surviving key still reaches the owner it always did.
        survivor = first_key if removed == [second.credential_id] else second_key
        assert await credentials.owner_for_key(survivor) == owner.owner_id

    with_db(work)


def test_a_revoked_credential_leaves_the_recordings_alone() -> None:
    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        credentials = PostgresCredentialRepository(database)
        extra, key = new_credential(owner.owner_id, "Phone")
        await credentials.create(extra, key)

        await credentials.revoke(extra.credential_id, owner.owner_id)

        async with database.connection() as connection:
            row = await fetch_one(
                connection, "SELECT owner_id FROM recordings WHERE id = %s", (stored.recording_id,)
            )
        assert row is not None
        assert row["owner_id"] == owner.owner_id

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


# --- What a read is allowed to decompress ----------------------------------
#
# A stored analysis is large enough to live in the TOAST table, and PostgreSQL
# detoasts it once per *expression* that references it — not once per row. That
# is the mechanism behind Step 10.12, and it is invisible in any test that only
# checks the values that come back: a query that decompresses a 253 kB document
# eleven times returns exactly what a query that decompresses it once returns.
# So the shape of the SQL is asserted here, from the modules that carry it.


def _document_reads(sql: str) -> int:
    """How many times a statement *reads* ``document``.

    ``AS document`` names a result column and reads nothing, so it does not
    count; every other occurrence is an expression PostgreSQL will detoast for.
    """
    return len(re.findall(r"(?<!AS )\bdocument\b", sql))


def test_the_summary_read_touches_the_document_once() -> None:
    """The regression 10.12 fixed, pinned where it happened.

    10.11 selected ``document - 'pitch_points'`` *and*
    ``jsonb_array_length(document->'pitch_points')`` together, believing the
    second reused what the first had decompressed. It did not: measured on a
    five-minute recording, the pair cost 5.70 ms and 109 buffers against 1.80 ms
    and 37 for the strip alone. A second reference is therefore a second full
    decompression, and this asserts there is not one.
    """
    reads = _document_reads(_SUMMARY_COLUMNS)
    assert reads == 1, f"the summary read decompresses the document {reads} times"
    assert "pitch_point_count" in _SUMMARY_COLUMNS, "the count must still be selected"
    assert "jsonb_array_length" not in _SUMMARY_COLUMNS


def test_the_decimated_read_touches_the_document_twice_and_no_more() -> None:
    """Two expressions, and both of them earn their decompression.

    The strip produces the record without its timeline; the expansion produces
    the sample. Neither can be derived from the other, so this read pays for two
    detoasts where the summary read pays for one — and a third would be the 10.12
    regression again, in a new statement.

    The stride is asserted with them because it is what makes the read a sample
    rather than a slice: it is computed from the stored count in the same
    statement, so nothing here needs a second read to find out how long the
    timeline is.
    """
    reads = _document_reads(_DECIMATED_SQL)
    assert reads == 2, f"the decimated read decompresses the document {reads} times"
    assert "WITH ORDINALITY" in _DECIMATED_SQL
    assert "pitch_point_count::numeric" in _DECIMATED_SQL, "the stride comes from the column"


def test_the_progress_query_reaches_the_metrics_once_per_row() -> None:
    """Eleven scalars, one decompression.

    Reading each scalar from ``a.document`` detoasted the same document eleven
    times: 593 ms and 11 834 buffers for a 30-recording window, against 30 ms and
    1 172 once the lateral projects ``document->'metrics'`` and the scalars come
    off that. The property is which side of the lateral the document is read on.
    """
    assert "document->'metrics' AS metrics" in PROGRESS_SQL
    # Once, inside the lateral. Every scalar above it reads `a.metrics`.
    reads = _document_reads(PROGRESS_SQL)
    assert reads == 1, f"the progress query decompresses each document {reads} times"
    assert "a.document" not in PROGRESS_SQL


def test_an_audio_analysis_predating_the_count_column_is_counted_by_the_migration(
    tmp_path: Any,
) -> None:
    """Migration 0005, applied to a database seeded through the old schema.

    The rows that made the ``jsonb_array_length`` expression necessary are the
    ones written before 10.11, whose documents carry no ``pitch_point_count`` at
    all. Reading the count out of such a document reports zero — "measured
    nothing" for an analysis that measured plenty — so the migration has to
    supply it. This seeds exactly that row and checks the summary read answers
    from the column.
    """

    async def work(database: Database) -> None:
        staged = tmp_path / "staged"
        staged.mkdir()
        files = migration_files()
        # Pinned by name rather than by position, for the reason recorded on
        # the 0003 backfill test: staging "everything but the last" tests
        # nothing as soon as another migration lands after it.
        counted = next(p for p in files if p.name.startswith("0005_"))
        split = files.index(counted)
        before, rest = files[:split], files[split:]
        assert counted in rest, "the migration under test must be one that is applied"
        for path in before:
            (staged / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        owner_id = uuid.uuid4()
        recording_id = uuid.uuid4().hex
        analysis_id = uuid.uuid4().hex
        # A document in the shape 10.11 predates: a timeline, and no count.
        old_document = {
            "audio_analysis_id": analysis_id,
            "recording_id": recording_id,
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z",
            "feedback_status": "not_requested",
            "metrics": audio_metrics().model_dump(mode="json"),
            "pitch_points": [
                {
                    "timestamp_seconds": round(index * 0.023, 3),
                    "frequency_hz": 440.0,
                    "midi_note": 69,
                    "note_name": "A4",
                    "cents": 0.0,
                    "confidence": 0.95,
                }
                for index in range(6)
            ],
        }
        assert "pitch_point_count" not in old_document

        try:
            async with database.transaction() as connection:
                await connection.execute("CREATE SCHEMA point_count_probe")
                await connection.execute("SET LOCAL search_path TO point_count_probe")
                await apply_migrations(connection, staged)

            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO point_count_probe")
                await connection.execute(
                    "INSERT INTO owners (id, created_at) VALUES (%s, now())", (owner_id,)
                )
                await connection.execute(
                    """
                    INSERT INTO recordings (id, owner_id, original_filename, audio_format,
                                            duration_seconds, sample_rate, channels, size_bytes,
                                            created_at, document)
                    VALUES (%s, %s, 't.wav', 'wav', 2.0, 22050, 1, 1024, now(), '{}'::jsonb)
                    """,
                    (recording_id, owner_id),
                )
                await connection.execute(
                    """
                    INSERT INTO audio_analyses (id, recording_id, status, feedback_status,
                                                created_at, document)
                    VALUES (%s, %s, 'completed', 'not_requested', now(), %s)
                    """,
                    (analysis_id, recording_id, json.dumps(old_document)),
                )

            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO point_count_probe")
                assert await apply_migrations(connection, MIGRATIONS_DIR) == [p.name for p in rest]

            async with database.connection() as connection:
                await connection.execute("SET search_path TO point_count_probe")
                row = await fetch_one(
                    connection,
                    "SELECT pitch_point_count FROM audio_analyses WHERE id = %s",
                    (analysis_id,),
                )
                assert row is not None
                assert row["pitch_point_count"] == 6, "the backfill did not count the timeline"
        finally:
            async with database.transaction() as connection:
                await connection.execute("DROP SCHEMA IF EXISTS point_count_probe CASCADE")

    with_db(work)


def test_the_point_count_column_follows_the_timeline_through_a_write() -> None:
    """The column is a projection of the points, like ``status`` is of the status.

    A pending record has no timeline; the write that completes it attaches one.
    If the column were written once at insert and left alone, every analysis in
    the database would report zero frames measured.
    """

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        stored = recording()
        await PostgresRecordingRepository(database).create(stored, owner.owner_id)
        analyses = PostgresAudioAnalysisRepository(database)

        pending = AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(), recording_id=stored.recording_id
        )
        await analyses.create(pending)

        async def stored_count() -> int:
            async with database.connection() as connection:
                row = await fetch_one(
                    connection,
                    "SELECT pitch_point_count FROM audio_analyses WHERE id = %s",
                    (pending.audio_analysis_id,),
                )
            assert row is not None
            return int(row["pitch_point_count"])

        assert await stored_count() == 0

        points = tuple(
            PitchPoint(
                timestamp_seconds=round(index * 0.023, 3),
                frequency_hz=440.0,
                midi_note=69,
                note_name="A4",
                cents=0.0,
                confidence=0.95,
            )
            for index in range(9)
        )
        await analyses.update(
            pending.model_copy(
                update={
                    "status": AudioAnalysisStatus.COMPLETED,
                    "metrics": audio_metrics(),
                    "pitch_points": points,
                }
            ),
            expect_status=AudioAnalysisStatus.PENDING,
        )

        assert await stored_count() == 9
        summary = await analyses.summary(pending.audio_analysis_id)
        assert summary is not None
        assert summary.pitch_point_count == 9

        whole = await analyses.get(pending.audio_analysis_id)
        assert whole is not None
        # The column and the timeline it counts, from the same record.
        assert len(whole.pitch_points) == summary.pitch_point_count

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


# --- Identity retention -----------------------------------------------------
#
# What only a real database can be asked: that the row lock actually serialises
# two concurrent cleanup runs, that deleting an owner really cascades, and that
# the migration's backfill produced sensible values for rows that predate it.


async def _age_owner(database: Database, owner_id: uuid.UUID, days: int) -> None:
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE owners SET last_seen_at = now() - make_interval(days => %s) WHERE id = %s",
            (days, owner_id),
        )


def test_reclaiming_an_owner_cascades_to_everything_it_had() -> None:
    """Belt and braces: an eligible owner has no recordings, but if the
    predicate were ever wrong the foreign keys must still leave nothing behind.
    """

    async def work(database: Database) -> None:
        owner, token = await _make_owner(database)
        await _age_owner(database, owner.owner_id, 400)
        owners = PostgresOwnerRepository(database)

        cutoff = run_at_utc() - timedelta(days=30)
        assert await owners.claim_expired_owner(owner.owner_id, cutoff) is True

        async with database.connection() as connection:
            for table in ("owners", "credentials", "recordings"):
                row = await fetch_one(connection, f"SELECT count(*) AS n FROM {table}")  # noqa: S608
                assert row is not None
                assert int(row["n"]) == 0, table
        assert await owners.get_by_token(token) is None

    with_db(work)


def test_concurrent_cleanup_runs_delete_an_owner_exactly_once() -> None:
    """Race B, against real PostgreSQL.

    The pool is warmed first, and that is load-bearing: ``min_size`` is 1, so
    without it the second task spends the race opening a connection and the two
    never overlap. Step 10.2 learned that the hard way.
    """

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        await _age_owner(database, owner.owner_id, 400)
        owners = PostgresOwnerRepository(database)
        await _warm_pool(database, 6)

        cutoff = run_at_utc() - timedelta(days=30)
        claimed: list[bool] = []

        async def attempt() -> None:
            claimed.append(await owners.claim_expired_owner(owner.owner_id, cutoff))

        async with anyio.create_task_group() as group:
            for _ in range(6):
                group.start_soon(attempt)

        assert claimed.count(True) == 1, f"{claimed.count(True)} runs each deleted the owner"
        assert await owners.get(owner.owner_id) is None

    with_db(work)


def test_a_concurrent_visit_saves_an_owner_from_cleanup() -> None:
    """Race A/C: ``touch`` and the claim contend for the same row lock."""

    async def work(database: Database) -> None:
        owner, _ = await _make_owner(database)
        await _age_owner(database, owner.owner_id, 400)
        owners = PostgresOwnerRepository(database)
        await _warm_pool(database, 4)

        cutoff = run_at_utc() - timedelta(days=30)
        results: list[bool] = []

        async def visit() -> None:
            await owners.touch(owner.owner_id, timedelta(0))

        async def clean() -> None:
            results.append(await owners.claim_expired_owner(owner.owner_id, cutoff))

        async with anyio.create_task_group() as group:
            group.start_soon(visit)
            group.start_soon(clean)

        # Whichever won, the two outcomes are consistent: either the owner is
        # gone and was never touched, or it survived and is no longer eligible.
        survived = await owners.get(owner.owner_id) is not None
        assert survived == (results == [False])

    with_db(work)


def test_the_activity_index_exists_and_the_query_can_use_it() -> None:
    """A schema assertion, not a plan assertion.

    Which plan PostgreSQL picks depends on how much data there is, and on a
    thirty-row table a sequential scan is *correct* — the first version of this
    test asserted "the plan mentions the index" and failed for exactly that
    reason. What is worth protecting is volume-independent: the index exists on
    the column the candidate query filters and orders by, and that query does
    not wrap the column in anything that would make the index unusable.

    Measured at 50 000 owners (45 542 eligible): with the index, an index scan
    reads 519 rows in 1.20 ms; with index scans disabled, a sequential scan
    reads 47 944 rows and sorts them in 16.19 ms. Recorded in
    ``docs/architecture.md``.
    """

    async def work(database: Database) -> None:
        async with database.connection() as connection:
            rows = await fetch_all(
                connection,
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'owners'",
            )
        defs = [str(row["indexdef"]) for row in rows]
        assert any("owners_last_seen_idx" in d and "last_seen_at" in d for d in defs), defs

        # The predicate compares the bare column. Wrapping it — date_trunc,
        # a cast, an expression — silently makes the index unusable at any size.
        sql = inspect.getsource(PostgresOwnerRepository.expired_owner_ids)
        assert "o.last_seen_at < %s" in sql, sql
        assert "ORDER BY o.last_seen_at" in sql, sql

    with_db(work)


def test_an_owner_predating_the_activity_column_gets_a_sensible_backfill(tmp_path: Any) -> None:
    """Migration 0003, applied to a database seeded through the old schema.

    Inventing ``now()`` would make every identity look active; inventing
    ``created_at`` would make a long-lived one look abandoned. The backfill uses
    the newest thing the owner demonstrably did.
    """

    async def work(database: Database) -> None:
        staged = tmp_path / "staged"
        staged.mkdir()
        files = migration_files()
        # Pinned to 0003 by name, not by position. This used to stage
        # ``files[:-1]`` and apply ``files[-1:]``, which meant "everything before
        # the newest migration" — correct only while 0003 *was* the newest. Step
        # 10.10 added 0004 and the test kept passing while testing nothing: with
        # 0003 already staged, ``last_seen_at`` existed before the owner was
        # inserted, so the assertions below were satisfied by the column's
        # ``DEFAULT now()`` rather than by the backfill they exist to check.
        activity = next(p for p in files if p.name.startswith("0003_"))
        split = files.index(activity)
        before, rest = files[:split], files[split:]
        assert activity in rest, "the migration under test must be one that is applied"
        for path in before:
            (staged / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        owner_id = uuid.uuid4()
        recording_id = uuid.uuid4().hex
        try:
            async with database.transaction() as connection:
                await connection.execute("CREATE SCHEMA retention_probe")
                await connection.execute("SET LOCAL search_path TO retention_probe")
                await apply_migrations(connection, staged)

            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO retention_probe")
                await connection.execute(
                    "INSERT INTO owners (id, created_at) VALUES (%s, now() - interval '400 days')",
                    (owner_id,),
                )
                await connection.execute(
                    """
                    INSERT INTO credentials (id, owner_id, credential_hash, label, created_at)
                    VALUES (%s, %s, %s, 'k', now() - interval '400 days')
                    """,
                    (uuid.uuid4(), owner_id, hash_token(new_owner_token())),
                )
                # The newest evidence of life: an upload three days ago.
                await connection.execute(
                    """
                    INSERT INTO recordings (id, owner_id, original_filename, audio_format,
                                            duration_seconds, sample_rate, channels, size_bytes,
                                            created_at, document)
                    VALUES (%s, %s, 't.wav', 'wav', 2.0, 22050, 1, 1024,
                            now() - interval '3 days', '{}'::jsonb)
                    """,
                    (recording_id, owner_id),
                )

            async with database.transaction() as connection:
                await connection.execute("SET LOCAL search_path TO retention_probe")
                assert await apply_migrations(connection, MIGRATIONS_DIR) == [p.name for p in rest]

            async with database.connection() as connection:
                await connection.execute("SET search_path TO retention_probe")
                row = await fetch_one(
                    connection,
                    """
                    SELECT last_seen_at,
                           last_seen_at > now() - interval '4 days'  AS looks_recent,
                           last_seen_at < now() + interval '1 minute' AS not_invented
                      FROM owners WHERE id = %s
                    """,
                    (owner_id,),
                )
                assert row is not None
                assert row["looks_recent"] is True, "the backfill ignored the recent upload"
                assert row["not_invented"] is True, "the backfill invented a future timestamp"
        finally:
            async with database.transaction() as connection:
                await connection.execute("DROP SCHEMA IF EXISTS retention_probe CASCADE")

    with_db(work)
