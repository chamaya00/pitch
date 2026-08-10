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
from app.services.analysis.repository import AnalysisRepository, JsonFileAnalysisRepository
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.analyzer import AudioAnalyzer, SignalAudioAnalyzer
from app.services.audio_analysis.repository import (
    AudioAnalysisRepository,
    JsonFileAudioAnalysisRepository,
)
from app.services.orchestration.analysis import AnalysisService
from app.services.orchestration.audio_analysis import AudioAnalysisService
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


def get_analysis_repository(settings: SettingsDep) -> AnalysisRepository:
    """Return the active analysis repository, annotated as the protocol."""
    return JsonFileAnalysisRepository(settings.storage_root)


def get_audio_analysis_repository(settings: SettingsDep) -> AudioAnalysisRepository:
    """Return the active audio-analysis repository, annotated as the protocol."""
    return JsonFileAudioAnalysisRepository(settings.storage_root)


def get_audio_analyzer() -> AudioAnalyzer:
    """Return the deterministic analyzer, annotated as the protocol.

    Takes no settings: its parameters are properties of the algorithm, chosen
    against test signals and documented in ``docs/audio-analysis.md``. Making
    them deployment knobs would let two installations report different ranges
    for the same recording while both calling the number "detected range".
    """
    return SignalAudioAnalyzer()


RecordingStorageDep = Annotated[RecordingStorage, Depends(get_recording_storage)]
RecordingRepositoryDep = Annotated[RecordingRepository, Depends(get_recording_repository)]
SpeechToTextProviderDep = Annotated[SpeechToTextProvider, Depends(get_speech_to_text_provider)]
FeedbackProviderDep = Annotated[FeedbackProvider, Depends(get_feedback_provider)]
AnalysisRepositoryDep = Annotated[AnalysisRepository, Depends(get_analysis_repository)]
AudioAnalysisRepositoryDep = Annotated[
    AudioAnalysisRepository, Depends(get_audio_analysis_repository)
]
AudioAnalyzerDep = Annotated[AudioAnalyzer, Depends(get_audio_analyzer)]


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
) -> AudioAnalysisService:
    """Assemble the audio-analysis workflow from the configured pieces.

    Note what is absent: no speech-to-text provider and no feedback provider.
    This pipeline measures the signal and needs neither, which is why a
    deployment with no AI credentials at all can still analyse audio.
    """
    return AudioAnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=analyzer,
        stale_after_seconds=settings.analysis_stale_after_seconds,
    )


AudioAnalysisServiceDep = Annotated[AudioAnalysisService, Depends(get_audio_analysis_service)]
