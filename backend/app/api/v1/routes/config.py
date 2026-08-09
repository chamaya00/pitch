"""Public configuration route."""

from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.schemas.config import PublicConfigResponse
from app.services.audio.validation import ALLOWED_EXTENSIONS

router = APIRouter(tags=["config"])


@router.get(
    "/config",
    response_model=PublicConfigResponse,
    summary="Public upload limits",
    description=(
        "Limits and accepted formats, so a client can describe them accurately. "
        "Advisory only: the upload endpoint enforces all of them server-side."
    ),
)
def public_config(settings: SettingsDep) -> PublicConfigResponse:
    return PublicConfigResponse(
        max_audio_size_mb=settings.max_audio_size_mb,
        max_audio_size_bytes=settings.max_audio_size_bytes,
        max_audio_duration_seconds=settings.max_audio_duration_seconds,
        supported_extensions=sorted(ALLOWED_EXTENSIONS),
    )
