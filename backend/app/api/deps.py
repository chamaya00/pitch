"""Shared FastAPI dependencies.

Services are constructed per request from settings. Construction is trivial —
both hold a path and no open resources — so this costs nothing and keeps tests
able to point the whole stack at a temporary directory by overriding
``get_settings`` alone.
"""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
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


RecordingStorageDep = Annotated[RecordingStorage, Depends(get_recording_storage)]
RecordingRepositoryDep = Annotated[RecordingRepository, Depends(get_recording_repository)]
