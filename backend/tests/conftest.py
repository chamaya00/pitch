"""Shared fixtures.

Since Step 7M the application's source of truth is PostgreSQL, so the fixtures
here point the repository dependencies at the in-memory doubles in
``tests/doubles.py``. That keeps this suite runnable with no database and no
network, exactly as before, while ``tests/test_database.py`` and
``tests/test_repository_contract.py`` exercise the real SQL — the latter against
*both* implementations, so the doubles cannot quietly disagree with it.
"""

import anyio
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.doubles import Doubles, build_doubles, override_repositories


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
