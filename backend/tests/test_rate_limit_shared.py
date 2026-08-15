"""One limiter contract, run against both places a count can live.

``test_rate_limit.py`` tests the in-process algorithm on its own. This file
tests the *contract* — what any :class:`~app.services.limits.limiter.RateLimiter`
must do — against the in-process limiter and the PostgreSQL one, so a shared
counter that quietly disagreed with the one it replaces fails the same assertion
the original passes. It is the same trade ``test_repository_contract.py`` makes,
for the same reason: two implementations of one interface are only
interchangeable if something checks.

Without ``TEST_DATABASE_URL`` the PostgreSQL parameter skips and the in-process
one still runs, so the contract is always exercised and fully exercised wherever
a server exists.

**Nothing here sleeps.** Time is advanced per backend: the in-process limiter
gets a hand-cranked clock, and the PostgreSQL one has its stored window
backdated, which is the same thing happening to the only clock it can see.

The second half of the file is the part that has no in-process equivalent, and
is the entire reason the step exists: two limiter instances that share nothing
but a database must enforce **one** limit between them, including when they
count at the same instant.
"""

import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.rate_limit import COSTLY_BUCKET, NEW_IDENTITIES_BUCKET, RateLimits
from app.core.config import Settings, get_settings
from app.db.migrate import apply_migrations
from app.db.pool import Database, fetch_all, fetch_one
from app.main import create_app
from app.services.limits.limiter import Decision, RateLimiter
from app.services.limits.postgres import PostgresRateLimiter
from app.services.limits.window import InProcessRateLimiter
from tests.doubles import build_doubles, override_repositories

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
IDENTITY_URL = "/api/v1/identity"


def run(factory: Any) -> Any:
    return anyio.run(factory)


class Clock:
    """A hand-cranked monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Backend:
    """A way to build limiters, and a way to make time pass for them."""

    def __init__(
        self,
        build: Callable[..., RateLimiter],
        advance: Callable[[float], Any],
        database: Database | None = None,
    ) -> None:
        self.build = build
        self._advance = advance
        self.database = database

    async def advance(self, seconds: float) -> None:
        result = self._advance(seconds)
        if result is not None:
            await result


@asynccontextmanager
async def _memory_backend() -> AsyncIterator[Backend]:
    clock = Clock()

    def build(limit: int = 3, window: int = 60, bucket: str = "test") -> RateLimiter:
        return InProcessRateLimiter(limit=limit, window_seconds=window, clock=clock)

    yield Backend(build=build, advance=clock.advance)


@asynccontextmanager
async def _postgres_backend() -> AsyncIterator[Backend]:
    database = Database(DATABASE_URL)
    await database.open()
    try:
        async with database.transaction() as connection:
            await apply_migrations(connection)
        async with database.transaction() as connection:
            # No foreign key ties these rows to an owner, so truncating owners
            # does not reach them. Each test starts from an empty table.
            await connection.execute("TRUNCATE rate_limit_windows")

        def build(limit: int = 3, window: int = 60, bucket: str = "test") -> RateLimiter:
            return PostgresRateLimiter(
                bucket=bucket, limit=limit, window_seconds=window, database=lambda: database
            )

        async def advance(seconds: float) -> None:
            """Make the stored window that much older.

            The PostgreSQL limiter anchors windows to the server's ``now()``,
            which is not injectable. Backdating the row is the same arithmetic
            from the other side, and it exercises the real ``CASE`` arm in the
            upsert rather than a test-only branch.
            """
            async with database.transaction() as connection:
                await connection.execute(
                    "UPDATE rate_limit_windows "
                    "SET window_started_at = window_started_at - %s * INTERVAL '1 second'",
                    (seconds,),
                )

        yield Backend(build=build, advance=advance, database=database)
    finally:
        await database.close()


BACKENDS: dict[str, Any] = {"memory": _memory_backend, "postgres": _postgres_backend}


@pytest.fixture(params=list(BACKENDS))
def backend(request: pytest.FixtureRequest) -> str:
    if request.param == "postgres" and not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; the PostgreSQL limiter needs a real database")
    name: str = request.param
    return name


def with_backend(name: str, scenario: Callable[[Backend], Any]) -> Any:
    async def go() -> Any:
        async with BACKENDS[name]() as wired:
            return await scenario(wired)

    return anyio.run(go)


# --- The contract both implementations keep ---------------------------------


def test_attempts_up_to_the_limit_are_allowed(backend: str) -> None:
    async def scenario(wired: Backend) -> list[bool]:
        bucket = wired.build(limit=3)
        return [(await bucket.check("a")).allowed for _ in range(3)]

    assert with_backend(backend, scenario) == [True, True, True]


def test_the_attempt_past_the_limit_is_refused(backend: str) -> None:
    async def scenario(wired: Backend) -> bool:
        bucket = wired.build(limit=3)
        for _ in range(3):
            await bucket.check("a")
        return (await bucket.check("a")).allowed

    assert with_backend(backend, scenario) is False


def test_keys_are_counted_separately(backend: str) -> None:
    async def scenario(wired: Backend) -> bool:
        bucket = wired.build(limit=1)
        await bucket.check("a")
        return (await bucket.check("b")).allowed

    assert with_backend(backend, scenario) is True


def test_hammering_never_earns_an_early_allowance(backend: str) -> None:
    """A refusal that reset the count would hand out free retries forever."""

    async def scenario(wired: Backend) -> list[bool]:
        bucket = wired.build(limit=2, window=60)
        for _ in range(2):
            await bucket.check("a")
        refusals = [(await bucket.check("a")).allowed for _ in range(20)]
        # Nearly the whole window has passed, and the allowance is still spent.
        await wired.advance(59)
        refusals.append((await bucket.check("a")).allowed)
        return refusals

    assert with_backend(backend, scenario) == [False] * 21


def test_hammering_does_not_extend_the_window(backend: str) -> None:
    """The mirror failure: a refusal that restarted the window is a permanent ban."""

    async def scenario(wired: Backend) -> bool:
        bucket = wired.build(limit=1, window=60)
        await bucket.check("a")
        for _ in range(10):
            await wired.advance(1)
            await bucket.check("a")
        # Ten seconds of hammering, then the rest of the original window.
        await wired.advance(50)
        return (await bucket.check("a")).allowed

    assert with_backend(backend, scenario) is True, "attempts pushed the window's end away"


def test_the_window_ends(backend: str) -> None:
    async def scenario(wired: Backend) -> bool:
        bucket = wired.build(limit=2, window=60)
        for _ in range(2):
            await bucket.check("a")
        assert (await bucket.check("a")).allowed is False
        await wired.advance(60)
        return (await bucket.check("a")).allowed

    assert with_backend(backend, scenario) is True


def test_a_new_window_starts_the_count_again_rather_than_continuing_it(backend: str) -> None:
    """The reset arm must reset to 1, not merely move the window's start."""

    async def scenario(wired: Backend) -> list[bool]:
        bucket = wired.build(limit=2, window=60)
        for _ in range(5):
            await bucket.check("a")
        await wired.advance(60)
        return [(await bucket.check("a")).allowed for _ in range(3)]

    assert with_backend(backend, scenario) == [True, True, False]


def test_an_allowed_decision_asks_for_no_wait(backend: str) -> None:
    async def scenario(wired: Backend) -> Decision:
        return await wired.build(limit=2).check("a")

    assert with_backend(backend, scenario) == Decision(allowed=True, retry_after_seconds=0)


def test_retry_after_counts_down_and_is_never_zero(backend: str) -> None:
    async def scenario(wired: Backend) -> tuple[int, int]:
        bucket = wired.build(limit=1, window=60)
        await bucket.check("a")
        early = await bucket.check("a")
        await wired.advance(59.5)
        late = await bucket.check("a")
        return early.retry_after_seconds, late.retry_after_seconds

    early, late = with_backend(backend, scenario)
    assert early > late
    assert late >= 1, "Retry-After: 0 is an invitation to retry now"
    assert early <= 61


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)])
def test_a_nonsense_configuration_is_refused_at_construction(
    backend: str, limit: int, window: int
) -> None:
    async def scenario(wired: Backend) -> None:
        with pytest.raises(ValueError):
            wired.build(limit=limit, window=window)

    with_backend(backend, scenario)


def test_both_implementations_satisfy_the_protocol(backend: str) -> None:
    async def scenario(wired: Backend) -> bool:
        return isinstance(wired.build(), RateLimiter)

    assert with_backend(backend, scenario) is True


# --- What only a shared counter can do --------------------------------------

pytestmark_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is unset; the shared counter needs a real database",
)


@asynccontextmanager
async def _shared_database() -> AsyncIterator[Database]:
    database = Database(DATABASE_URL)
    await database.open()
    try:
        async with database.transaction() as connection:
            await apply_migrations(connection)
        async with database.transaction() as connection:
            await connection.execute("TRUNCATE rate_limit_windows")
        yield database
    finally:
        await database.close()


def limiter_on(database: Database, *, limit: int, bucket: str = "test") -> PostgresRateLimiter:
    """A limiter that shares nothing with any other but the database.

    Separate instances stand in for separate uvicorn workers: in production each
    has its own process, its own pool and its own memory, and the only thing
    they have in common is the table.
    """
    return PostgresRateLimiter(
        bucket=bucket, limit=limit, window_seconds=3600, database=lambda: database
    )


@pytestmark_postgres
def test_two_workers_share_one_allowance() -> None:
    """The bug this step exists to fix, at the unit level.

    Measured against the real server before it was built: four uvicorn workers
    sharing a limit of five new identities minted twenty, exactly 4.0x.
    """

    async def scenario() -> list[bool]:
        async with _shared_database() as database:
            first = limiter_on(database, limit=3)
            second = limiter_on(database, limit=3)
            # Alternating, so neither instance sees three of its own.
            return [
                (await first.check("client")).allowed,
                (await second.check("client")).allowed,
                (await first.check("client")).allowed,
                (await second.check("client")).allowed,
            ]

    assert run(scenario) == [True, True, True, False]


@pytestmark_postgres
def test_a_limit_of_n_allows_exactly_n_however_many_workers_ask_at_once() -> None:
    """Concurrency is the whole correctness argument, so it is asserted.

    A read-then-write limiter passes every sequential test above and fails this
    one: between reading a count of 4 and writing 5, every other worker also
    reads 4, and all of them allow. Forty simultaneous attempts against a limit
    of five must yield five.
    """

    async def scenario() -> int:
        async with _shared_database() as database:
            workers = [limiter_on(database, limit=5) for _ in range(8)]
            allowed = 0

            async def attempt(worker: PostgresRateLimiter) -> None:
                nonlocal allowed
                if (await worker.check("one-client")).allowed:
                    allowed += 1

            async with anyio.create_task_group() as tasks:
                for _ in range(5):
                    for worker in workers:
                        tasks.start_soon(attempt, worker)
            return allowed

    assert run(scenario) == 5


@pytestmark_postgres
def test_two_limits_never_spend_each_others_allowance() -> None:
    """The bucket column, doing its job.

    An owner id and a client address are different kinds of thing counted in one
    table. If they shared a row, a busy owner would lock newcomers out.
    """

    async def scenario() -> bool:
        async with _shared_database() as database:
            identities = limiter_on(database, limit=1, bucket=NEW_IDENTITIES_BUCKET)
            costly = limiter_on(database, limit=1, bucket=COSTLY_BUCKET)
            same_key = "collision"
            await identities.check(same_key)
            return (await costly.check(same_key)).allowed

    assert run(scenario) is True


@pytestmark_postgres
def test_the_table_grows_with_callers_and_not_with_traffic() -> None:
    """One row per key. An attempt updates a row; it does not append one."""

    async def scenario() -> int:
        async with _shared_database() as database:
            limiter = limiter_on(database, limit=1000)
            for _ in range(50):
                await limiter.check("a")
            for _ in range(50):
                await limiter.check("b")
            async with database.connection() as connection:
                rows = await fetch_all(connection, "SELECT * FROM rate_limit_windows")
            return len(rows)

    assert run(scenario) == 2


@pytestmark_postgres
def test_the_count_survives_the_limiter_that_wrote_it() -> None:
    """A worker restarting must not hand its callers a fresh allowance."""

    async def scenario() -> bool:
        async with _shared_database() as database:
            before = limiter_on(database, limit=2)
            await before.check("a")
            await before.check("a")
            # A brand new instance, as a restarted worker would be.
            after = limiter_on(database, limit=2)
            return (await after.check("a")).allowed

    assert run(scenario) is False


# --- Keeping the table from becoming the exhaustion vector ------------------


@pytestmark_postgres
def test_ended_windows_are_swept_and_live_ones_are_left_alone() -> None:
    async def scenario() -> list[str]:
        async with _shared_database() as database:
            limiter = limiter_on(database, limit=5)
            await limiter.check("old")
            async with database.transaction() as connection:
                await connection.execute(
                    "UPDATE rate_limit_windows "
                    "SET window_started_at = window_started_at - INTERVAL '2 hours'"
                )
            await limiter.check("live")

            # The instance above has already pruned once, so it will not sweep
            # again inside its window. A fresh one prunes on its first check.
            await limiter_on(database, limit=5).check("trigger")

            async with database.connection() as connection:
                rows = await fetch_all(
                    connection, "SELECT limit_key FROM rate_limit_windows ORDER BY limit_key"
                )
            return [row["limit_key"] for row in rows]

    assert run(scenario) == ["live", "trigger"]


@pytestmark_postgres
def test_sweeping_does_not_forgive_a_caller_still_inside_its_window() -> None:
    """The sweep must be garbage collection, never an amnesty."""

    async def scenario() -> bool:
        async with _shared_database() as database:
            spent = limiter_on(database, limit=1)
            await spent.check("a")
            # A second instance prunes on its first check, with "a" still live.
            fresh = limiter_on(database, limit=1)
            return (await fresh.check("a")).allowed

    assert run(scenario) is False


@pytestmark_postgres
def test_a_limiter_with_no_database_refuses_rather_than_counting_nothing() -> None:
    limiter = PostgresRateLimiter(bucket="test", limit=1, window_seconds=60, database=lambda: None)

    with pytest.raises(RuntimeError, match="no database is configured"):
        run(lambda: limiter.check("a"))


# --- The configuration that selects one --------------------------------------


def test_the_default_backend_counts_in_this_process() -> None:
    limits = RateLimits(Settings(rate_limit_backend="memory"))

    assert isinstance(limits.new_identities, InProcessRateLimiter)
    assert isinstance(limits.costly, InProcessRateLimiter)


def test_selecting_the_database_backend_builds_shared_limiters() -> None:
    settings = Settings(rate_limit_backend="database", database_url="postgresql://x/y")

    limits = RateLimits(settings, lambda: None)

    assert isinstance(limits.new_identities, PostgresRateLimiter)
    assert isinstance(limits.costly, PostgresRateLimiter)


def test_a_shared_counter_without_a_database_is_refused_at_startup() -> None:
    """Never a silent downgrade.

    An operator who set this because they run four workers would otherwise get
    four counters and no indication that the setting did nothing.
    """
    with pytest.raises(ValueError, match="RATE_LIMIT_BACKEND=database requires DATABASE_URL"):
        Settings(rate_limit_backend="database", database_url=None)


@pytestmark_postgres
def test_the_two_buckets_are_the_names_the_table_stores() -> None:
    """The log line and the row say the same word about the same limit."""

    async def scenario() -> list[str]:
        async with _shared_database() as database:
            settings = Settings(
                rate_limit_backend="database",
                database_url=DATABASE_URL,
                rate_limit_new_identities=5,
                rate_limit_costly_requests=5,
            )
            limits = RateLimits(settings, lambda: database)
            await limits.new_identities.check("an-address")
            await limits.costly.check(str(uuid.uuid4()))
            async with database.connection() as connection:
                rows = await fetch_all(
                    connection, "SELECT bucket FROM rate_limit_windows ORDER BY bucket"
                )
            return [row["bucket"] for row in rows]

    assert run(scenario) == sorted([COSTLY_BUCKET, NEW_IDENTITIES_BUCKET])


@pytestmark_postgres
def test_two_applications_sharing_a_database_enforce_one_identity_limit() -> None:
    """The measured failure, through the whole HTTP stack.

    Two applications stand in for two uvicorn workers: separate ``app.state``,
    separate limiter objects and — because each has its own doubles — separate
    owner stores, exactly as two processes would. The only thing they share is
    the table. Against a limit of three, the two of them together must mint
    three identities and refuse the fourth, where before this step they minted
    three *each*.
    """

    async def scenario() -> tuple[list[int], int]:
        async with _shared_database() as database:
            settings = Settings(
                _env_file=None,
                environment="test",
                rate_limit_backend="database",
                database_url=DATABASE_URL,
                rate_limit_new_identities=3,
            )

            async def worker() -> TestClient:
                app = create_app()
                app.dependency_overrides[get_settings] = lambda: settings
                app.state.rate_limits = RateLimits(settings, lambda: database)
                # Its own owner store, as a separate process would have.
                override_repositories(app, await build_doubles())
                return TestClient(app, raise_server_exceptions=False)

            first, second = await worker(), await worker()
            # Alternating, so neither application sees three of its own.
            codes = [
                first.get(IDENTITY_URL).status_code,
                second.get(IDENTITY_URL).status_code,
                first.get(IDENTITY_URL).status_code,
                second.get(IDENTITY_URL).status_code,
            ]
            async with database.connection() as connection:
                row = await fetch_one(
                    connection,
                    "SELECT count FROM rate_limit_windows WHERE bucket = %s",
                    (NEW_IDENTITIES_BUCKET,),
                )
            assert row is not None
            return codes, int(row["count"])

    codes, counted = run(scenario)
    assert codes == [200, 200, 200, 429]
    assert counted == 4, "the refused attempt is counted too, or hammering earns a retry"


@pytestmark_postgres
def test_the_migration_creates_the_table_the_limiter_writes_to() -> None:
    async def scenario() -> dict[str, Any] | None:
        async with _shared_database() as database, database.connection() as connection:
            return await fetch_one(
                connection,
                "SELECT count(*) AS columns FROM information_schema.columns "
                "WHERE table_name = 'rate_limit_windows'",
            )

    row = run(scenario)
    assert row is not None
    assert row["columns"] == 4
