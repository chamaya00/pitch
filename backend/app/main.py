"""VocalLens FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.owner import OWNER_HEADER
from app.api.rate_limit import RateLimits
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import ApiSecurityPolicyMiddleware, MaxBodySizeMiddleware
from app.db.migrate import apply_migrations
from app.db.pool import Database
from app.schemas.health import HealthResponse
from app.version import __version__

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("api_started", extra={"version": __version__, **settings.public_config()})
    if settings.uses_mock_providers:
        # Loud on purpose: a deployment must never look like it is producing
        # real analysis while a mock is wired in.
        logger.warning(
            "mock_analysis_providers_active",
            extra={
                "speech_to_text_provider": settings.speech_to_text_provider,
                "feedback_provider": settings.feedback_provider,
                "detail": "analysis output will be demo data, not real analysis",
            },
        )

    # The pool is opened once, here, and every repository shares it. Migrations
    # run in the same startup so a deployment cannot serve requests against a
    # schema older than the code — and they take an advisory lock, so several
    # processes starting together apply them once between them.
    #
    # The DSN itself is never logged. A failure to connect surfaces as a startup
    # error, which is the point of opening eagerly rather than lazily.
    database = (
        Database(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )
        if settings.database_url
        else None
    )
    # The limiters were built in ``create_app`` holding a callable that reads
    # this attribute, so the database backend finds the pool here without
    # anything being rebuilt. ``Settings`` has already refused the combination
    # where that callable would keep returning ``None``.
    app.state.database = database
    if database is not None:
        await database.open()
        async with database.transaction() as connection:
            applied = await apply_migrations(connection)
        logger.info("database_migrations_applied", extra={"count": len(applied)})
    else:
        logger.error(
            "database_not_configured",
            extra={"detail": "DATABASE_URL is unset; recording endpoints will fail"},
        )

    try:
        yield
    finally:
        if database is not None:
            await database.close()
        logger.info("api_stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Deterministic audio analysis API for VocalLens.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # A cross-origin response header is invisible to JavaScript unless it is
        # named here. Without this the browser receives the minted owner token
        # and silently withholds it, so every request would mint a new identity
        # and history would never accumulate — a failure with no error message
        # anywhere. `allow_headers=["*"]` covers the request direction only.
        expose_headers=[OWNER_HEADER],
    )

    # Registered after CORS so it runs *inside* it: an oversized upload still
    # gets the headers a browser needs to read the error.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_audio_size_bytes)

    # Registered last, so it runs *outermost* and every response carries the
    # policy — including the ones produced above without reaching a route. The
    # exempt set is read from the application rather than written out, so
    # turning a documentation UI off or moving it cannot leave a stale path
    # here, and adding one cannot silently escape the policy.
    app.add_middleware(
        ApiSecurityPolicyMiddleware,
        exempt_paths=frozenset(
            path
            for path in (app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url)
            if path
        ),
    )

    # Per-application rather than module-level: two applications in one test
    # process must not share counters and refuse each other's requests. Built
    # here rather than in the lifespan so a test client that never runs the
    # lifespan is still limited exactly as production is.
    #
    # The pool does not exist yet — the lifespan opens it — so the database
    # backend is handed a callable that reads ``app.state.database`` when a
    # request is actually being counted, rather than a pool captured as ``None``
    # at construction and silently never counted against.
    app.state.database = None

    def current_database() -> Database | None:
        database: Database | None = app.state.database
        return database

    app.state.rate_limits = RateLimits(settings, current_database)

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def root_health() -> HealthResponse:
        """Unversioned health probe for infrastructure checks."""
        return HealthResponse(
            service=settings.app_name,
            version=__version__,
            environment=settings.environment,
        )

    return app


app = create_app()
