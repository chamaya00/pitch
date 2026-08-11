"""Rate limiting through the real application.

Step 10.3 was written against a measurement, not a worry: sixty concurrent
requests carrying no credential created sixty owner rows and sixty credential
rows in 0.47 seconds — roughly 128 identities per second from a single client.
Every owner-scoped route mints an identity when the key is absent or
unrecognised, so **an unauthenticated read is a write**, and nothing bounded it.

So the assertions here count rows, not status codes. A test that only checked
for `429` would pass just as happily against an implementation that returned the
error *after* writing, which is no protection at all.

Three properties carry the design:

1. a refused request creates nothing;
2. a caller presenting a **recognised** key is never refused by the
   identity limit, so people behind a shared address are unaffected by
   strangers;
3. reading your own history is never rate-limited — only work that costs disk,
   CPU or provider budget is.
"""

import os
import uuid
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.api.rate_limit import RateLimits
from app.core.config import Settings, get_settings
from app.db.migrate import apply_migrations
from app.db.pool import Database, fetch_one
from app.main import create_app
from app.services.owners.models import new_owner
from app.services.owners.repository import PostgresOwnerRepository
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories
from tests.fixtures import write_wav

IDENTITY_URL = "/api/v1/identity"
RECORDINGS_URL = "/api/v1/recordings"
CREDENTIALS_URL = "/api/v1/identity/credentials"
PROGRESS_URL = "/api/v1/recordings/progress"


def run(factory: Any) -> Any:
    return anyio.run(factory)


def wav_bytes(tmp_path: Path) -> bytes:
    return write_wav(tmp_path / "src.wav").read_bytes()


def settings_with(**overrides: Any) -> Settings:
    return Settings(_env_file=None, environment="test", **overrides)


@pytest.fixture
def storage_settings(tmp_path: Path) -> dict[str, Any]:
    return {"storage_root": tmp_path / "storage"}


def build(doubles: Doubles, settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    # The limiters are built from the *real* settings in ``create_app``; rebuild
    # them from the overridden ones so a test's limits are the ones enforced.
    app.state.rate_limits = RateLimits(settings)
    override_repositories(app, doubles)
    return TestClient(app, raise_server_exceptions=False)


def owners_count(doubles: Doubles) -> int:
    return len(doubles.owners._by_id)  # noqa: SLF001 - counting rows is the assertion


def credentials_count(doubles: Doubles) -> int:
    return len(doubles.owners.credentials._by_hash)  # noqa: SLF001 - same


def seed_recording(doubles: Doubles, owner_id: uuid.UUID) -> str:
    recording = Recording.model_validate(
        {
            "recording_id": uuid.uuid4().hex,
            "original_filename": "take.wav",
            "audio_format": "wav",
            "duration_seconds": 12.0,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 4096,
        }
    )
    run(lambda: doubles.recordings.create(recording, owner_id))
    return recording.recording_id


# --- Bounding anonymous identity creation ----------------------------------


def test_anonymous_requests_stop_creating_identities_at_the_limit(doubles: Doubles) -> None:
    """The measured failure, now bounded — asserted on rows, not on statuses."""
    client = build(doubles, settings_with(rate_limit_new_identities=3))
    before = owners_count(doubles)

    statuses = [client.get(IDENTITY_URL).status_code for _ in range(10)]

    assert owners_count(doubles) - before == 3, "more identities were created than the limit allows"
    assert statuses.count(200) == 3
    assert statuses.count(429) == 7


def test_a_refused_request_creates_no_credential_either(doubles: Doubles) -> None:
    """Minting writes two rows. Neither may survive a refusal."""
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    client.get(IDENTITY_URL)
    owners_before = owners_count(doubles)
    credentials_before = credentials_count(doubles)

    assert client.get(IDENTITY_URL).status_code == 429

    assert owners_count(doubles) == owners_before
    assert credentials_count(doubles) == credentials_before


def test_the_refusal_is_the_documented_envelope_with_a_retry_after(doubles: Doubles) -> None:
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    client.get(IDENTITY_URL)

    response = client.get(IDENTITY_URL)

    assert response.status_code == 429
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "RATE_LIMITED"
    assert body["message"]
    assert int(response.headers["Retry-After"]) >= 1


def test_no_owner_token_is_issued_with_a_refusal(doubles: Doubles) -> None:
    """A minted key in the header of a refusal would mean the row was written."""
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    client.get(IDENTITY_URL)

    response = client.get(IDENTITY_URL)

    assert response.status_code == 429
    assert OWNER_HEADER not in response.headers


def test_an_unrecognised_key_is_bounded_too(doubles: Doubles) -> None:
    """Random well-formed keys mint, so they are the same amplifier."""
    from app.services.owners.models import new_owner_token

    client = build(doubles, settings_with(rate_limit_new_identities=2))
    before = owners_count(doubles)

    for _ in range(8):
        client.get(IDENTITY_URL, headers={OWNER_HEADER: new_owner_token()})

    assert owners_count(doubles) - before == 2


def test_every_owner_scoped_route_is_bounded_not_just_identity(doubles: Doubles) -> None:
    """The mint happens at the identity boundary, so all 17 routes share the bound."""
    client = build(doubles, settings_with(rate_limit_new_identities=2))
    before = owners_count(doubles)

    for url in (RECORDINGS_URL, PROGRESS_URL, IDENTITY_URL, RECORDINGS_URL, PROGRESS_URL):
        client.get(url)

    assert owners_count(doubles) - before == 2


# --- People who already have a key are never caught in it -------------------


def test_a_recognised_key_is_never_refused_by_the_identity_limit(doubles: Doubles) -> None:
    """The property that makes a per-address limit safe on a shared address."""
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    # Exhaust the allowance with anonymous requests from the same client.
    for _ in range(5):
        client.get(IDENTITY_URL)

    for _ in range(20):
        response = client.get(IDENTITY_URL, headers={OWNER_HEADER: doubles.token})
        assert response.status_code == 200

    assert response.json()["recordings"] == 0


def test_an_existing_owner_keeps_reading_everything_while_newcomers_are_refused(
    doubles: Doubles,
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    for _ in range(4):
        client.get(IDENTITY_URL)

    headers = {OWNER_HEADER: doubles.token}
    assert client.get(RECORDINGS_URL, headers=headers).status_code == 200
    assert client.get(f"{RECORDINGS_URL}/{recording_id}", headers=headers).status_code == 200
    assert client.get(PROGRESS_URL, headers=headers).status_code == 200
    assert client.get(IDENTITY_URL, headers=headers).json()["recordings"] == 1


def test_reading_your_own_history_is_never_rate_limited(doubles: Doubles) -> None:
    """Only work that costs something is charged. Looking is not work."""
    seed_recording(doubles, doubles.owner.owner_id)
    client = build(doubles, settings_with(rate_limit_costly_requests=1))
    headers = {OWNER_HEADER: doubles.token}

    statuses = {client.get(RECORDINGS_URL, headers=headers).status_code for _ in range(30)}
    statuses |= {client.get(PROGRESS_URL, headers=headers).status_code for _ in range(30)}
    statuses |= {client.get(IDENTITY_URL, headers=headers).status_code for _ in range(30)}

    assert statuses == {200}


# --- Costly requests, charged to the owner ----------------------------------


def test_adding_keys_is_charged_to_the_owner(doubles: Doubles) -> None:
    client = build(doubles, settings_with(rate_limit_costly_requests=2))
    headers = {OWNER_HEADER: doubles.token}
    before = credentials_count(doubles)

    statuses = [
        client.post(CREDENTIALS_URL, json={"label": "k"}, headers=headers).status_code
        for _ in range(6)
    ]

    assert statuses.count(201) == 2
    assert statuses.count(429) == 4
    assert credentials_count(doubles) - before == 2, "a refused request still created a key"


def test_uploading_is_charged_to_the_owner(
    doubles: Doubles, storage_settings: dict[str, Any], tmp_path: Path
) -> None:
    client = build(doubles, settings_with(rate_limit_costly_requests=2, **storage_settings))
    headers = {OWNER_HEADER: doubles.token}
    audio = wav_bytes(tmp_path)

    statuses = []
    for _ in range(5):
        response = client.post(
            RECORDINGS_URL,
            files={"file": ("take.wav", audio, "audio/wav")},
            headers=headers,
        )
        statuses.append(response.status_code)

    assert statuses.count(201) == 2
    assert statuses.count(429) == 3
    stored = list(doubles.recordings.records())
    assert len(stored) == 2, "a refused upload still stored a recording"


def test_a_refused_upload_writes_no_file(
    doubles: Doubles, storage_settings: dict[str, Any], tmp_path: Path
) -> None:
    """Disk is the resource; the row count alone would not prove it was spared."""
    settings = settings_with(rate_limit_costly_requests=1, **storage_settings)
    client = build(doubles, settings)
    headers = {OWNER_HEADER: doubles.token}
    audio = wav_bytes(tmp_path)

    client.post(RECORDINGS_URL, files={"file": ("a.wav", audio, "audio/wav")}, headers=headers)
    directory = settings.storage_root / "recordings"
    after_first = len(list(directory.iterdir())) if directory.is_dir() else 0

    refused = client.post(
        RECORDINGS_URL, files={"file": ("b.wav", audio, "audio/wav")}, headers=headers
    )

    assert refused.status_code == 429
    after_second = len(list(directory.iterdir())) if directory.is_dir() else 0
    assert after_second == after_first


def test_one_owners_costly_allowance_is_their_own(doubles: Doubles) -> None:
    """Keyed by owner, so a shared address cannot spend somebody else's budget."""
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    client = build(doubles, settings_with(rate_limit_costly_requests=1))

    mine = {OWNER_HEADER: doubles.token}
    theirs = {OWNER_HEADER: stranger_token}
    assert client.post(CREDENTIALS_URL, json={"label": "a"}, headers=mine).status_code == 201
    assert client.post(CREDENTIALS_URL, json={"label": "b"}, headers=mine).status_code == 429

    # The stranger's own allowance is untouched by mine being spent.
    assert client.post(CREDENTIALS_URL, json={"label": "c"}, headers=theirs).status_code == 201


def test_the_costly_refusal_carries_retry_after(doubles: Doubles) -> None:
    client = build(doubles, settings_with(rate_limit_costly_requests=1))
    headers = {OWNER_HEADER: doubles.token}
    client.post(CREDENTIALS_URL, json={"label": "a"}, headers=headers)

    response = client.post(CREDENTIALS_URL, json={"label": "b"}, headers=headers)

    assert response.status_code == 429
    assert response.json()["error_code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) >= 1


def test_deleting_an_identity_is_not_rate_limited(doubles: Doubles) -> None:
    """Somebody asking for their data to be gone is never told to come back later."""
    client = build(doubles, settings_with(rate_limit_costly_requests=1))
    headers = {OWNER_HEADER: doubles.token}
    client.post(CREDENTIALS_URL, json={"label": "spend it"}, headers=headers)

    assert client.delete(IDENTITY_URL, headers=headers).status_code == 200


def test_revoking_a_key_is_not_rate_limited(doubles: Doubles) -> None:
    """Revocation is a safety action. Being told to wait is the wrong answer."""
    client = build(doubles, settings_with(rate_limit_costly_requests=2))
    headers = {OWNER_HEADER: doubles.token}
    created = client.post(CREDENTIALS_URL, json={"label": "Phone"}, headers=headers).json()
    client.post(CREDENTIALS_URL, json={"label": "spend it"}, headers=headers)

    response = client.delete(f"{CREDENTIALS_URL}/{created['credential_id']}", headers=headers)

    assert response.status_code == 200


# --- Nothing leaks through the refusal --------------------------------------


def test_a_refusal_reveals_nothing_about_the_defence_or_the_caller(doubles: Doubles) -> None:
    client = build(doubles, settings_with(rate_limit_new_identities=1))
    client.get(IDENTITY_URL)

    text = client.get(IDENTITY_URL).text

    assert set(client.get(IDENTITY_URL).json()) == {"status", "error_code", "message"}
    for leak in ("testclient", "127.0.0.1", "owner_id", "hash", "limit=", "window"):
        assert leak not in text.lower()


def test_a_costly_refusal_never_names_the_owner(doubles: Doubles) -> None:
    client = build(doubles, settings_with(rate_limit_costly_requests=1))
    headers = {OWNER_HEADER: doubles.token}
    client.post(CREDENTIALS_URL, json={"label": "a"}, headers=headers)

    response = client.post(CREDENTIALS_URL, json={"label": "b"}, headers=headers)

    assert str(doubles.owner.owner_id) not in response.text
    assert doubles.token not in response.text


# --- Two applications do not share counters ---------------------------------


def test_limiters_are_per_application(doubles: Doubles) -> None:
    """Otherwise one test file's requests would refuse another's."""
    first = build(doubles, settings_with(rate_limit_new_identities=1))
    first.get(IDENTITY_URL)
    assert first.get(IDENTITY_URL).status_code == 429

    second = build(doubles, settings_with(rate_limit_new_identities=1))

    assert second.get(IDENTITY_URL).status_code == 200


# --- Concurrency, against a real database -----------------------------------
#
# The limiter's ``check`` contains no ``await``, so it is atomic with respect to
# other coroutines on one loop — but "atomic in theory" is what the ``asyncio``
# lock this project replaced in Step 7M also claimed. These tests fire genuinely
# concurrent requests at the real application over a real PostgreSQL pool and
# count the rows that survive.
#
# The pool is warmed first, and that is load-bearing rather than tidy-up:
# ``min_size`` is 1, so without it the later tasks spend the race opening
# connections while the first runs to completion, and the requests never
# actually overlap. Step 10.2 learned that the hard way — a concurrency test
# passed with the row lock removed.

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

concurrency = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is unset; the concurrency tests need a real database",
)


async def _warm(database: Database, connections: int) -> None:
    """Open ``connections`` real connections at once so the race is a race."""

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


async def _fire(app: Any, count: int, headers: dict[str, str] | None = None) -> list[int]:
    """Issue ``count`` requests concurrently and return their statuses."""
    statuses: list[int] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def one() -> None:
            response = await client.get(IDENTITY_URL, headers=headers)
            statuses.append(response.status_code)

        async with anyio.create_task_group() as group:
            for _ in range(count):
                group.start_soon(one)
    return statuses


@concurrency
def test_concurrent_anonymous_requests_cannot_exceed_the_identity_limit() -> None:
    """The measured attack, run concurrently against real PostgreSQL.

    Sixty concurrent unauthenticated requests created sixty owners before this
    step. Here they must create exactly the allowance and no more, however they
    interleave.
    """

    async def work() -> None:
        database = Database(DATABASE_URL)
        await database.open()
        try:
            async with database.transaction() as connection:
                await apply_migrations(connection)
            async with database.transaction() as connection:
                await connection.execute("TRUNCATE owners CASCADE")
            await _warm(database, 8)

            settings = settings_with(rate_limit_new_identities=5)
            app = create_app()
            app.dependency_overrides[get_settings] = lambda: settings
            app.state.database = database
            app.state.rate_limits = RateLimits(settings)

            statuses = await _fire(app, 40)

            async with database.connection() as connection:
                row = await fetch_one(connection, "SELECT count(*) AS n FROM owners")
                owners = 0 if row is None else int(row["n"])
                row = await fetch_one(connection, "SELECT count(*) AS n FROM credentials")
                credentials = 0 if row is None else int(row["n"])

            assert owners == 5, f"{owners} identities were created; the limit was 5"
            assert credentials == 5, f"{credentials} credentials survived a refusal"
            assert statuses.count(200) == 5
            assert statuses.count(429) == 35
        finally:
            async with database.transaction() as connection:
                await connection.execute("TRUNCATE owners CASCADE")
            await database.close()

    anyio.run(work)


@concurrency
def test_concurrent_requests_from_an_existing_owner_are_never_refused() -> None:
    """Concurrency must not turn a returning user into a newcomer."""

    async def work() -> None:
        database = Database(DATABASE_URL)
        await database.open()
        try:
            async with database.transaction() as connection:
                await apply_migrations(connection)
            async with database.transaction() as connection:
                await connection.execute("TRUNCATE owners CASCADE")
            await _warm(database, 8)

            owner, token = new_owner()
            await PostgresOwnerRepository(database).create(owner, token)

            settings = settings_with(rate_limit_new_identities=1)
            app = create_app()
            app.dependency_overrides[get_settings] = lambda: settings
            app.state.database = database
            app.state.rate_limits = RateLimits(settings)

            statuses = await _fire(app, 30, headers={OWNER_HEADER: token})

            assert set(statuses) == {200}, "an existing owner was rate-limited"
            async with database.connection() as connection:
                row = await fetch_one(connection, "SELECT count(*) AS n FROM owners")
            assert row is not None
            assert int(row["n"]) == 1, "resolving an existing key created an identity"
        finally:
            async with database.transaction() as connection:
                await connection.execute("TRUNCATE owners CASCADE")
            await database.close()

    anyio.run(work)
