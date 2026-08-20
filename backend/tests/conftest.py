"""Shared fixtures, and the guard that stops a partial run looking complete.

Since Step 7M the application's source of truth is PostgreSQL, so the fixtures
here point the repository dependencies at the in-memory doubles in
``tests/doubles.py``. That keeps this suite runnable with no database and no
network, exactly as before, while ``tests/test_database.py`` and
``tests/test_repository_contract.py`` exercise the real SQL — the latter against
*both* implementations, so the doubles cannot quietly disagree with it.

**A skip is a silent pass**, and Step 10.20 measured how much of one. Without
``TEST_DATABASE_URL`` the suite reports 1 584 passed and 187 skipped, in green,
and 185 of those skips are every test that touches SQL: the whole PostgreSQL
half of the contract suite, every migration, every statement-shape assertion,
the shared rate limiter and the connection budget. That is where nearly all of
Steps 10.11 to 10.19 lives. ``test_database.py`` claimed such a run "is not
skipped quietly in CI: a run with no database says so in the skip reason" — but
a reason printed in a log that nothing reads is exactly what quiet means, and
until 10.20 there was no CI to read it.

:func:`pytest_configure` is the fix. It changes nothing for a checkout with no
database, which still skips and still says so; it makes a run that was *meant*
to be complete fail loudly instead. Which runs those are is stated by whoever
starts them — ``REQUIRE_DATABASE_TESTS=1`` — rather than sniffed from the
environment, so a contributor is never surprised by it and CI can never mean
something else by it.
"""

import os

import anyio
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.doubles import Doubles, build_doubles, override_repositories

#: Set by a run that must not silently skip the PostgreSQL suites.
REQUIRE_DATABASE_TESTS = "REQUIRE_DATABASE_TESTS"


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a run that claims to be complete and has no database to be complete with.

    A ``UsageError`` rather than a failed test: there is nothing to report about
    the code, the *run* is misconfigured, and saying so before collection means
    no time is spent producing a green result that would have meant nothing.
    """
    if os.environ.get(REQUIRE_DATABASE_TESTS, "").strip().lower() not in {"1", "true", "yes"}:
        return
    if os.environ.get("TEST_DATABASE_URL", "").strip():
        return
    raise pytest.UsageError(
        f"{REQUIRE_DATABASE_TESTS} is set but TEST_DATABASE_URL is empty: this run "
        "would skip every test that touches SQL — the PostgreSQL half of the "
        "contract suite, the migrations, the statement shapes, the shared rate "
        "limiter and the connection budget — and report success. Point "
        "TEST_DATABASE_URL at a scratch database, or unset "
        f"{REQUIRE_DATABASE_TESTS} to accept a partial run."
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def doubles() -> Doubles:
    """A wired set of in-memory repositories with one registered owner."""
    return anyio.run(build_doubles)


@pytest.fixture
def owner_headers(doubles: Doubles) -> dict[str, str]:
    """The header that identifies the fixture owner on every request."""
    return {"X-VocalLens-Owner": doubles.token}


@pytest.fixture
def client(doubles: Doubles) -> TestClient:
    app = create_app()
    override_repositories(app, doubles)
    return TestClient(app)
