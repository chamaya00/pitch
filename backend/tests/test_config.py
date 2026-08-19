import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app


def test_defaults_match_documented_limits():
    settings = Settings(_env_file=None)
    assert settings.max_audio_size_mb == 50
    assert settings.max_audio_duration_seconds == 300
    assert settings.max_audio_size_bytes == 50 * 1024 * 1024


def test_cors_origins_accepts_comma_separated_string():
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("http://a.test,http://b.test", ["http://a.test", "http://b.test"]),
        ("http://a.test, http://b.test", ["http://a.test", "http://b.test"]),
        ('["http://a.test", "http://b.test"]', ["http://a.test", "http://b.test"]),
    ],
)
def test_cors_origins_parses_from_the_environment(monkeypatch, raw, expected):
    """Env parsing is a separate path from init kwargs, and the one deployments use.

    pydantic-settings JSON-decodes complex fields read from the environment
    before validators run, so the documented comma-separated form raised
    SettingsError at import time until the field was marked NoDecode. Passing
    the value as an init kwarg never exercised that path.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected


def test_app_starts_with_the_documented_env_value(monkeypatch):
    """The exact value shipped in .env.example and docker-compose must boot."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    create_app()


def test_public_config_excludes_secrets():
    settings = Settings(
        _env_file=None,
        anthropic_api_key="secret-value",
        database_url="postgresql://user:pw@localhost/db",
    )
    public = settings.public_config()
    assert "secret-value" not in str(public)
    assert "anthropic_api_key" not in public
    assert "database_url" not in public


def test_public_config_endpoint_reports_the_configured_limits(client):
    response = client.get("/api/v1/config")

    assert response.status_code == 200
    body = response.json()
    assert body["max_audio_size_mb"] == 50
    assert body["max_audio_size_bytes"] == 50 * 1024 * 1024
    assert body["max_audio_duration_seconds"] == 300
    assert body["supported_extensions"] == [".mp3", ".wav"]


def test_public_config_exposes_no_secrets(client):
    body = client.get("/api/v1/config").json()

    assert "anthropic_api_key" not in body
    assert "database_url" not in body
    assert "storage_root" not in body
    assert "environment" not in body


# --- The database pool, sized per process (Step 10.17) ---------------------


def test_the_pool_defaults_to_a_size_several_workers_can_afford():
    """The default is a per-process share of a server-wide limit.

    Ten was the constant while this was one process. Step 10.10 made several
    workers supported and Step 10.17 measured what that did: twelve workers of
    ten took all 97 connections a default PostgreSQL grants, after which nothing
    else could connect — not another worker, not ``cleanup_identities``, not
    ``psql``. Four is what one worker can actually use, and it leaves room for
    twenty-four processes where ten left room for nine.
    """
    settings = Settings(_env_file=None)
    assert settings.db_pool_max_size == 4
    assert settings.db_pool_min_size == 1


def test_a_pool_that_keeps_more_than_it_holds_is_refused():
    """Clamping would be the silent downgrade this codebase refuses elsewhere."""
    with pytest.raises(ValidationError, match="DB_POOL_MIN_SIZE"):
        Settings(_env_file=None, db_pool_min_size=5, db_pool_max_size=2)


def test_pool_bounds_are_read_from_the_environment(monkeypatch):
    """The path a deployment actually uses, which is not the one above."""
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "12")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "3")
    settings = Settings(_env_file=None)
    assert (settings.db_pool_min_size, settings.db_pool_max_size) == (3, 12)
