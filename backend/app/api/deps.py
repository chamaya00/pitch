"""Shared FastAPI dependencies.

Services are constructed per request from settings and from the application's
one open connection pool. Construction stays trivial — a repository holds a
reference to the pool and nothing else — so this costs nothing and keeps tests
able to point the whole stack somewhere else by overriding a single dependency.

**The pool is not built here.** It is opened once at startup and closed at
shutdown (see ``app/main.py``); this module only reads it off the application
state. A pool built per request would open a connection per request, which is
the problem a pool exists to solve.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response

from app.api.owner import OwnerTokenHeader, resolve_owner
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.db.pool import Database
from app.services.ai.errors import ProviderError
from app.services.ai.factory import (
    build_audio_feedback_provider,
    build_feedback_provider,
    build_speech_to_text_provider,
)
from app.services.ai.protocols import (
    AudioFeedbackProvider,
    FeedbackProvider,
    SpeechToTextProvider,
)
from app.services.analysis.postgres_repository import (
    AsyncAnalysisRepository,
    PostgresAnalysisRepository,
)
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.analyzer import AudioAnalyzer, SignalAudioAnalyzer
from app.services.audio_analysis.postgres_repository import (
    AsyncAudioAnalysisRepository,
    PostgresAudioAnalysisRepository,
)
from app.services.comparison.service import ComparisonService
from app.services.orchestration.analysis import AnalysisService
from app.services.orchestration.audio_analysis import AudioAnalysisService
from app.services.owners.models import Owner
from app.services.owners.repository import OwnerRepository, PostgresOwnerRepository
from app.services.progress.service import ProgressService
from app.services.recordings.postgres_repository import (
    OwnedRecordingRepository,
    PostgresRecordingRepository,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database:
    """The pool opened at startup.

    Raises:
        ApiError: ``INTERNAL_ERROR`` if no pool is attached. That means the
            application started without ``DATABASE_URL``; failing loudly here
            beats serving requests against a store that is not there.
    """
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise ApiError(ErrorCode.INTERNAL_ERROR, "Storage is not available.")
    return database


DatabaseDep = Annotated[Database, Depends(get_database)]


def get_recording_storage(settings: SettingsDep) -> RecordingStorage:
    return RecordingStorage(settings.storage_root)


def get_recording_repository(database: DatabaseDep) -> OwnedRecordingRepository:
    """Return the active repository, annotated as the protocol.

    Every method on that protocol takes an owner id, so no route can read a
    recording without saying whose it is.
    """
    return PostgresRecordingRepository(database)


def get_owner_repository(database: DatabaseDep) -> OwnerRepository:
    return PostgresOwnerRepository(database)


OwnerRepositoryDep = Annotated[OwnerRepository, Depends(get_owner_repository)]


async def get_owner(
    request: Request,
    response: Response,
    owners: OwnerRepositoryDep,
    token: OwnerTokenHeader = None,
) -> Owner:
    """Resolve — or mint — the caller's identity. See ``app/api/owner.py``."""
    return await resolve_owner(request, response, owners, token)


OwnerDep = Annotated[Owner, Depends(get_owner)]


async def get_owner_id(owner: OwnerDep) -> uuid.UUID:
    """The id every owner-scoped service call is given.

    Routes depend on this rather than on the ``Owner``: an id is all a service
    needs, and handing it the whole record would invite reading anything else
    off it.
    """
    return owner.owner_id


OwnerIdDep = Annotated[uuid.UUID, Depends(get_owner_id)]


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


def get_audio_feedback_provider(settings: SettingsDep) -> AudioFeedbackProvider:
    """Return the configured provider, seen through the audio protocol.

    The same adapter ``get_feedback_provider`` returns — one credential, one
    client — so a deployment cannot have real feedback on one half of the
    product and demo data on the other.
    """
    try:
        return build_audio_feedback_provider(settings)
    except ProviderError as exc:
        raise exc.to_api_error() from exc


def get_analysis_repository(database: DatabaseDep) -> AsyncAnalysisRepository:
    """Return the active analysis repository, annotated as the protocol."""
    return PostgresAnalysisRepository(database)


def get_audio_analysis_repository(database: DatabaseDep) -> AsyncAudioAnalysisRepository:
    """Return the active audio-analysis repository, annotated as the protocol."""
    return PostgresAudioAnalysisRepository(database)


def get_audio_analyzer() -> AudioAnalyzer:
    """Return the deterministic analyzer, annotated as the protocol.

    Takes no settings: its parameters are properties of the algorithm, chosen
    against test signals and documented in ``docs/audio-analysis.md``. Making
    them deployment knobs would let two installations report different ranges
    for the same recording while both calling the number "detected range".
    """
    return SignalAudioAnalyzer()


RecordingStorageDep = Annotated[RecordingStorage, Depends(get_recording_storage)]
RecordingRepositoryDep = Annotated[OwnedRecordingRepository, Depends(get_recording_repository)]
SpeechToTextProviderDep = Annotated[SpeechToTextProvider, Depends(get_speech_to_text_provider)]
FeedbackProviderDep = Annotated[FeedbackProvider, Depends(get_feedback_provider)]
AnalysisRepositoryDep = Annotated[AsyncAnalysisRepository, Depends(get_analysis_repository)]
AudioAnalysisRepositoryDep = Annotated[
    AsyncAudioAnalysisRepository, Depends(get_audio_analysis_repository)
]
AudioAnalyzerDep = Annotated[AudioAnalyzer, Depends(get_audio_analyzer)]
AudioFeedbackProviderDep = Annotated[AudioFeedbackProvider, Depends(get_audio_feedback_provider)]


def get_analysis_service(
    settings: SettingsDep,
    recordings: RecordingRepositoryDep,
    storage: RecordingStorageDep,
    analyses: AnalysisRepositoryDep,
    speech_to_text: SpeechToTextProviderDep,
    feedback: FeedbackProviderDep,
) -> AnalysisService:
    """Assemble the analysis workflow from the configured pieces.

    Everything it receives is an interface: swapping a provider or a repository
    is configuration, and the service is unaware of either choice.
    """
    return AnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        speech_to_text=speech_to_text,
        feedback=feedback,
        stale_after_seconds=settings.analysis_stale_after_seconds,
    )


AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]


def get_audio_analysis_service(
    settings: SettingsDep,
    recordings: RecordingRepositoryDep,
    storage: RecordingStorageDep,
    analyses: AudioAnalysisRepositoryDep,
    analyzer: AudioAnalyzerDep,
    feedback: AudioFeedbackProviderDep,
) -> AudioAnalysisService:
    """Assemble the audio-analysis workflow from the configured pieces.

    Note what is absent: a speech-to-text provider. Measuring the signal needs
    no transcription, which is why a deployment with no AI credentials can still
    analyse audio — the feedback provider is used only when someone asks for an
    interpretation of the measurements.
    """
    return AudioAnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=analyzer,
        feedback=feedback,
        stale_after_seconds=settings.analysis_stale_after_seconds,
    )


AudioAnalysisServiceDep = Annotated[AudioAnalysisService, Depends(get_audio_analysis_service)]


def get_comparison_service(recordings: RecordingRepositoryDep) -> ComparisonService:
    """Assemble the comparison workflow.

    Takes only the recording repository: a comparison reads two stored analyses
    and subtracts. It needs no storage, no analyzer and no provider — and the
    absence of a provider dependency is what makes it impossible for a model to
    reach a comparison number.
    """
    return ComparisonService(recordings=recordings)


ComparisonServiceDep = Annotated[ComparisonService, Depends(get_comparison_service)]


def get_progress_service(recordings: RecordingRepositoryDep) -> ProgressService:
    """Assemble the progress workflow.

    Like the comparison service, it takes only the recording repository — and
    like it, the absence of a provider dependency is the guarantee that no model
    can produce or judge a trend, rather than a promise that none will.
    """
    return ProgressService(recordings=recordings)


ProgressServiceDep = Annotated[ProgressService, Depends(get_progress_service)]
