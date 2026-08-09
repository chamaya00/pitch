"""Health check routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.health import HealthResponse
from app.version import __version__

router = APIRouter(tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(settings: SettingsDep) -> HealthResponse:
    """Return basic liveness information.

    Intentionally reports no secrets and performs no external calls, so it stays
    usable as a container liveness probe.
    """
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )
