"""VocalLens FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import MaxBodySizeMiddleware
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
    yield
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
    )

    # Registered after CORS so it runs *inside* it: an oversized upload still
    # gets the headers a browser needs to read the error.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_audio_size_bytes)

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
