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

from app.api.owner import OwnerTokenHeader, clean_key, resolve_owner
from app.api.rate_limit import limit_costly_request
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
from app.services.compatibility.repository import (
    PostgresSongReferenceRepository,
    SongReferenceRepository,
)
from app.services.compatibility.service import CompatibilityService, SongReferenceService
from app.services.orchestration.analysis import AnalysisService
from app.services.orchestration.audio_analysis import AudioAnalysisService
from app.services.owners.credential_repository import PostgresCredentialRepository
from app.services.owners.credentials import CredentialRepository
from app.services.owners.deletion import OwnerDeletionService
from app.services.owners.identity import OwnerDataRepository
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


def get_credential_repository(database: DatabaseDep) -> CredentialRepository:
    """The credential store, annotated as its own protocol.

    Separate from ``OwnerRepository`` so the resolver depends on the three
    credential operations it needs and cannot reach an owner's recordings
    through the same object.
    """
    return PostgresCredentialRepository(database)


CredentialRepositoryDep = Annotated[CredentialRepository, Depends(get_credential_repository)]


async def get_owner(
    request: Request,
    response: Response,
    owners: OwnerRepositoryDep,
    credentials: CredentialRepositoryDep,
    settings: SettingsDep,
    token: OwnerTokenHeader = None,
) -> Owner:
    """Resolve — or mint — the caller's identity.

    The single identity boundary in the application. Every owner-scoped route
    reaches it through ``OwnerIdDep``; none of them performs an identity check
    of its own, and none knows which resolver answered.
    """
    return await resolve_owner(
        request, response, owners, credentials, token, settings.identity_activity_throttle
    )


OwnerDep = Annotated[Owner, Depends(get_owner)]


def get_presented_key(token: OwnerTokenHeader = None) -> str | None:
    """The key the caller presented, normalised, or ``None``.

    Used for exactly one thing: marking which entry in a list of credentials is
    the one in the reader's hand. It is *not* an identity check — the owner has
    already been resolved by the time this is read — and it never leaves the
    process: it is turned into a credential id and discarded.
    """
    return clean_key(token)


PresentedKeyDep = Annotated[str | None, Depends(get_presented_key)]


async def get_owner_id(owner: OwnerDep) -> uuid.UUID:
    """The id every owner-scoped service call is given.

    Routes depend on this rather than on the ``Owner``: an id is all a service
    needs, and handing it the whole record would invite reading anything else
    off it.
    """
    return owner.owner_id


OwnerIdDep = Annotated[uuid.UUID, Depends(get_owner_id)]


async def costly_request(request: Request, owner_id: OwnerIdDep) -> None:
    """Charge this request against the owner's costly-request allowance.

    Declared on the handful of routes that consume disk, CPU or provider
    budget — uploading, starting either analysis, asking for feedback, adding a
    key. Reads are never charged: nobody should be told to slow down for looking
    at their own history.

    Keyed by owner, so the allowance follows the identity rather than the
    address it arrived from. Depends on ``OwnerIdDep``, so the caller is
    resolved first and the charge always lands on the right identity.
    """
    await limit_costly_request(request, owner_id)


CostlyRequestDep = Annotated[None, Depends(costly_request)]


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


def get_song_reference_repository(database: DatabaseDep) -> SongReferenceRepository:
    """The song-reference store, annotated as its protocol.

    Every method on that protocol takes an owner id, so no route can read a
    reference without saying whose it is.
    """
    return PostgresSongReferenceRepository(database)


SongReferenceRepositoryDep = Annotated[
    SongReferenceRepository, Depends(get_song_reference_repository)
]


def get_song_reference_service(
    references: SongReferenceRepositoryDep,
) -> SongReferenceService:
    """Assemble the reference workflow. One repository; there is nothing else."""
    return SongReferenceService(references=references)


SongReferenceServiceDep = Annotated[SongReferenceService, Depends(get_song_reference_service)]


def get_compatibility_service(
    recordings: RecordingRepositoryDep,
    references: SongReferenceRepositoryDep,
    analyses: AudioAnalysisRepositoryDep,
) -> CompatibilityService:
    """Assemble the compatibility workflow.

    Three repositories and nothing else: no storage, because there is no
    reference audio; no analyzer, because both ranges are already stored; and no
    provider, which is what makes it impossible for a model to reach one of
    these numbers rather than merely unlikely. The same guarantee the comparison
    and progress services get from the same absence.
    """
    return CompatibilityService(recordings=recordings, references=references, analyses=analyses)


CompatibilityServiceDep = Annotated[CompatibilityService, Depends(get_compatibility_service)]


def get_progress_service(recordings: RecordingRepositoryDep) -> ProgressService:
    """Assemble the progress workflow.

    Like the comparison service, it takes only the recording repository — and
    like it, the absence of a provider dependency is the guarantee that no model
    can produce or judge a trend, rather than a promise that none will.
    """
    return ProgressService(recordings=recordings)


ProgressServiceDep = Annotated[ProgressService, Depends(get_progress_service)]


def get_owner_data_repository(database: DatabaseDep) -> OwnerDataRepository:
    """The owner repository seen through the identity protocol.

    The same object ``get_owner_repository`` returns — one table, one
    implementation — annotated differently so the identity routes depend on the
    two operations they need rather than on everything an owner store can do.
    """
    return PostgresOwnerRepository(database)


OwnerDataRepositoryDep = Annotated[OwnerDataRepository, Depends(get_owner_data_repository)]


def get_owner_deletion_service(
    owners: OwnerDataRepositoryDep, storage: RecordingStorageDep
) -> OwnerDeletionService:
    """Assemble the deletion workflow.

    Takes storage as well as the repository, because deleting an owner's data
    means deleting the audio on disk and not only the rows that name it.
    """
    return OwnerDeletionService(owners=owners, storage=storage)


OwnerDeletionServiceDep = Annotated[OwnerDeletionService, Depends(get_owner_deletion_service)]
