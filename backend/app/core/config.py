"""Application configuration.

All configuration is read from the environment (or a local ``.env`` file).
Secrets must never be hardcoded — see ``.env.example`` at the repository root.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the VocalLens API."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "VocalLens API"
    api_v1_prefix: str = "/api/v1"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- Audio limits (enforced from Phase 1 onwards) ----------------------
    max_audio_size_mb: int = Field(default=50, ge=1, le=500)
    max_audio_duration_seconds: int = Field(default=300, ge=1, le=3600)

    # --- Storage -----------------------------------------------------------
    #: Root directory for uploaded recordings. A relative path resolves against
    #: the process working directory, which suits local development; deployments
    #: set an absolute path pointing at a mounted volume.
    storage_root: Path = Path("storage")

    # --- Integrations ------------------------------------------------------
    # Optional in Phase 0; required once the relevant phase is implemented.
    database_url: str | None = None
    anthropic_api_key: str | None = None

    # --- CORS --------------------------------------------------------------
    #: ``NoDecode`` is required: without it pydantic-settings JSON-decodes any
    #: complex field read from the environment, so the documented
    #: comma-separated form raises before the validator below ever runs.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept ``CORS_ORIGINS`` as a comma-separated string or a JSON list."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @property
    def max_audio_size_bytes(self) -> int:
        return self.max_audio_size_mb * 1024 * 1024

    def public_config(self) -> dict[str, object]:
        """Non-sensitive configuration safe to expose to clients and logs."""
        return {
            "environment": self.environment,
            "max_audio_size_mb": self.max_audio_size_mb,
            "max_audio_duration_seconds": self.max_audio_duration_seconds,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
