import pytest

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
