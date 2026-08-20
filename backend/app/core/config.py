"""Application configuration.

All configuration is read from the environment (or a local ``.env`` file).
Secrets must never be hardcoded — see ``.env.example`` at the repository root.
"""

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
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

    # --- Rate limiting (Step 10.3) -----------------------------------------
    #: New identities one client address may create per window.
    #:
    #: Kept deliberately generous. The measured abuse rate was ~128 identities
    #: per second from a single client, so even a limit in the tens is four
    #: orders of magnitude below it — there is nothing to buy by being strict,
    #: and a shared address (an office, a school, CGNAT) is a real place where
    #: several people legitimately arrive for the first time.
    #:
    #: A caller presenting a *recognised* key never touches this limit, so
    #: returning users behind a shared address are never affected by it.
    rate_limit_new_identities: int = Field(default=20, ge=1, le=10_000)
    rate_limit_new_identities_window_seconds: int = Field(default=3600, ge=1, le=86_400)

    #: Costly requests one *owner* may make per window: uploading, starting an
    #: analysis, asking for generated feedback, adding a key. Keyed by owner
    #: rather than by address so one person on a shared address can never spend
    #: another's allowance.
    rate_limit_costly_requests: int = Field(default=60, ge=1, le=100_000)
    rate_limit_costly_requests_window_seconds: int = Field(default=3600, ge=1, le=86_400)

    #: How many *trusted* proxies sit in front of this application.
    #:
    #: ``0`` — the default — means the peer address is the client, which is true
    #: for the bundled ``docker-compose.yml`` because it has no proxy. Any other
    #: value takes that many hops back from the right-hand end of
    #: ``X-Forwarded-For``, which is the only part of that header a proxy you
    #: trust actually wrote.
    #:
    #: The header is **never** trusted at the default. A client can send any
    #: ``X-Forwarded-For`` it likes, so honouring it unconditionally would make
    #: the limit bypassable by anyone who read this comment — strictly worse
    #: than having no limit, because it would look like protection.
    rate_limit_trusted_proxies: int = Field(default=0, ge=0, le=10)

    # --- Where the rate limit counts (Step 10.10) --------------------------
    #: ``memory`` counts in this process; ``database`` counts in a table every
    #: worker shares.
    #:
    #: The default stays ``memory`` because it is not a compromise for the
    #: deployment this repository ships: ``docker-compose.yml`` runs a single
    #: uvicorn worker, and with one worker there is no second counter to
    #: disagree with. It costs no statement, needs no table and is exactly
    #: correct there.
    #:
    #: ``database`` is what a deployment scaling past one worker turns on, and
    #: the reason it must is measured rather than asserted: four workers sharing
    #: a limit of five new identities minted twenty, exactly 4.0x. See
    #: ``services/limits/limiter.py``.
    rate_limit_backend: Literal["memory", "database"] = "memory"

    # --- Database pool (Step 10.17) ----------------------------------------
    #: How many connections **this process** may hold at once.
    #:
    #: A per-process quantity against a server-wide limit, and that is the whole
    #: reason it is configuration rather than the constant it used to be. One
    #: worker holding ten is unremarkable; twelve workers holding ten is every
    #: connection a default PostgreSQL grants, after which nothing else can
    #: connect at all — not another worker, not ``cleanup_identities``, not
    #: ``psql``. Measured in Step 10.17: twelve workers took 97 of 97.
    #:
    #: Four is the default because four is what one worker can use. Raising the
    #: pool from four to ten changed no read's throughput at sixteen concurrent
    #: clients by more than the run-to-run noise, because the work those reads
    #: are made of is decompression and parsing rather than waiting on a socket.
    #:
    #: The startup log reports how many processes of this size the server it is
    #: pointed at has room for, so an operator adding workers has the arithmetic
    #: in front of them; a pool too large to fit at all is refused rather than
    #: opened.
    db_pool_max_size: int = Field(default=4, ge=1, le=1000)

    #: How many connections this process keeps open while idle.
    #:
    #: One, so a worker that serves nothing costs the server one connection
    #: rather than a full pool. The pool grows on demand and — psycopg's
    #: default — hands back what has been idle for ten minutes, so a burst is
    #: paid for at its peak and for a while afterwards.
    db_pool_min_size: int = Field(default=1, ge=1, le=1000)

    # --- Identity retention (Step 10.6) ------------------------------------
    #: How long an identity that owns **nothing** may sit unused before it is
    #: reclaimed.
    #:
    #: **The repository specifies no retention period anywhere**, so this
    #: default is a choice rather than a requirement, and it is configuration
    #: rather than a constant for exactly that reason. Thirty days is long
    #: enough that somebody who wandered off and came back would have to have
    #: been gone a month, and short enough that crawler and probe traffic is
    #: reclaimed while the table is still small.
    #:
    #: The reason a wrong value here is cheap: only identities holding **no
    #: recordings** are ever eligible, and losing an empty identity is invisible
    #: to whoever held it — their next request mints a fresh one, and what they
    #: lost was nothing. Identities that own recordings are never reclaimed at
    #: any age; see ``services/owners/retention.py``.
    identity_retention_days: int = Field(default=30, ge=1, le=3650)

    #: How stale ``owners.last_seen_at`` must be before a request rewrites it.
    #:
    #: The throttle exists so recording activity does not become a write on
    #: every read — the same objection Step 10.3 raised against a
    #: database-backed rate limiter. An hour is far finer than a retention
    #: period measured in days, so the loss of precision cannot affect
    #: eligibility.
    identity_activity_throttle_seconds: int = Field(default=3600, ge=1, le=86_400)

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

    @model_validator(mode="after")
    def _shared_rate_limit_needs_a_database(self) -> "Settings":
        """A shared counter with nowhere to count is a configuration error.

        Refused at construction, so it fails at startup rather than on the first
        request that would have been limited. The alternative — falling back to
        this process's memory — is the silent downgrade this codebase refuses
        everywhere else: an operator who set ``RATE_LIMIT_BACKEND=database``
        because they run four workers would get four counters and no indication
        that the setting did nothing.
        """
        if self.rate_limit_backend == "database" and not self.database_url:
            raise ValueError(
                "RATE_LIMIT_BACKEND=database requires DATABASE_URL: a shared "
                "rate-limit counter has nowhere to count without one"
            )
        return self

    @model_validator(mode="after")
    def _a_pool_cannot_keep_more_than_it_holds(self) -> "Settings":
        """``min_size`` above ``max_size`` is refused, not silently clamped."""
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError(
                "DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE: a pool cannot "
                "keep more connections open than it may hold"
            )
        return self

    @property
    def identity_retention(self) -> timedelta:
        return timedelta(days=self.identity_retention_days)

    @property
    def identity_activity_throttle(self) -> timedelta:
        return timedelta(seconds=self.identity_activity_throttle_seconds)

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
