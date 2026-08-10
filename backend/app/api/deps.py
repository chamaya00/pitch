"""Shared FastAPI dependencies.

Services are constructed per request from settings. Construction is trivial —
both hold a path and no open resources — so this costs nothing and keeps tests
able to point the whole stack at a temporary directory by overriding
``get_settings`` alone.
"""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.ai.errors import ProviderError
from app.services.ai.factory import build_feedback_provider, build_speech_to_text_provider
from app.services.ai.protocols import FeedbackProvider, SpeechToTextProvider
from app.services.audio.storage import RecordingStorage
from app.services.recordings.repository import (
    JsonFileRecordingRepository,
    RecordingRepository,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_recording_storage(settings: SettingsDep) -> RecordingStorage:
    return RecordingStorage(settings.storage_root)


def get_recording_repository(settings: SettingsDep) -> RecordingRepository:
    """Return the active repository.

    The annotation is the protocol, not the implementation: routes depend on
    the interface, so a database-backed repository can replace this in Phase 7
    without any route changing.
    """
    return JsonFileRecordingRepository(settings.storage_root)


def get_speech_to_text_provider(settings: SettingsDep) -> SpeechToTextProvider:
    """Return the configured transcription provider.

    Annotated as the protocol so nothing downstream can depend on which vendor
    is behind it. A missing credential surfaces here as the documented
    ``ANALYSIS_NOT_CONFIGURED`` envelope rather than a 500 — and never as a
    silent downgrade to the mock.
    """
    try:
        return build_speech_to_text_provider(settings)
    except ProviderError as exc:
        raise exc.to_api_error() from exc


def get_feedback_provider(settings: SettingsDep) -> FeedbackProvider:
    """Return the configured feedback provider. See above."""
    try:
        return build_feedback_provider(settings)
    except ProviderError as exc:
        raise exc.to_api_error() from exc


RecordingStorageDep = Annotated[RecordingStorage, Depends(get_recording_storage)]
RecordingRepositoryDep = Annotated[RecordingRepository, Depends(get_recording_repository)]
SpeechToTextProviderDep = Annotated[SpeechToTextProvider, Depends(get_speech_to_text_provider)]
FeedbackProviderDep = Annotated[FeedbackProvider, Depends(get_feedback_provider)]
