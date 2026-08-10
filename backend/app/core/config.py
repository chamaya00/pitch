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
    deepgram_api_key: str | None = None

    # --- Speech analysis providers -----------------------------------------
    #: Which adapter serves each half of the analysis. ``mock`` is a
    #: deterministic development stand-in that transcribes nothing; the startup
    #: log and ``public_config`` both name the active choice so a deployment
    #: cannot quietly be running on demo data. Selecting a real provider without
    #: its credentials is a configuration error, never a silent downgrade — see
    #: ``services/ai/factory.py``.
    speech_to_text_provider: Literal["mock", "deepgram"] = "mock"
    feedback_provider: Literal["mock", "claude"] = "mock"

    #: Deepgram transcription model. Configurable because the option set below
    #: is model-dependent.
    deepgram_model: str = "nova-3"
    #: Request verbatim disfluencies ("um", "uh") from Deepgram.
    #:
    #: Off by default and deliberately opt-in. Deepgram does not report back
    #: whether it honoured the option, so enabling it is an operator's
    #: assertion that the configured model and plan support it. With it off,
    #: transcripts are marked as non-verbatim and filler statistics are omitted
    #: rather than reported as zero.
    deepgram_filler_words: bool = False

    #: Claude model used for qualitative feedback. Configurable, with a default
    #: that has not been exercised against the live API from this environment —
    #: see ``docs/ai.md``.
    anthropic_model: str = "claude-opus-5"

    #: Wall-clock budget for a single provider call. Applies to both adapters.
    #: One attempt each; there is no automatic retry.
    analysis_provider_timeout_seconds: int = Field(default=120, ge=1, le=900)

    #: How long an in-flight analysis may go without progress before it is
    #: treated as abandoned — the process that owned it died, and a new analysis
    #: of that recording is allowed to start. Must comfortably exceed two
    #: provider calls, or a slow-but-healthy analysis would be swept while it is
    #: still running and the recording would be analysed twice.
    analysis_stale_after_seconds: int = Field(default=900, ge=60, le=86_400)

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

    @property
    def uses_mock_providers(self) -> bool:
        """``True`` if any analysis output would be produced by a mock."""
        return "mock" in (self.speech_to_text_provider, self.feedback_provider)

    def public_config(self) -> dict[str, object]:
        """Non-sensitive configuration safe to expose to clients and logs.

        Provider *names* are included on purpose: the startup log has to make a
        mock deployment obvious. API keys are never returned from here, and the
        selected model names are also withheld — they are operational detail
        that clients have no use for.
        """
        return {
            "environment": self.environment,
            "max_audio_size_mb": self.max_audio_size_mb,
            "max_audio_duration_seconds": self.max_audio_duration_seconds,
            "speech_to_text_provider": self.speech_to_text_provider,
            "feedback_provider": self.feedback_provider,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
