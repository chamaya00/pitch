from app.core.config import Settings


def test_defaults_match_documented_limits():
    settings = Settings(_env_file=None)
    assert settings.max_audio_size_mb == 50
    assert settings.max_audio_duration_seconds == 300
    assert settings.max_audio_size_bytes == 50 * 1024 * 1024


def test_cors_origins_accepts_comma_separated_string():
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


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
