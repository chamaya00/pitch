"""Provider selection.

One place decides which adapter serves each half of the analysis, so no route
and no orchestrator ever branches on a provider name. Callers depend on the
protocols in ``protocols.py``; what they get back is settings-driven.

**There is no fallback.** A configured provider that cannot be constructed
raises :class:`~app.services.ai.errors.ProviderNotConfiguredError`. It never
degrades to a mock — an operator who asked for Deepgram and got demo data
labelled as a result would have no way to know, and that is precisely the
failure this project exists to avoid. A mock runs when, and only when, it is the
configured provider.
"""

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.ai.claude import ClaudeFeedbackProvider
from app.services.ai.deepgram import DeepgramSpeechToTextProvider
from app.services.ai.errors import ProviderNotConfiguredError
from app.services.ai.mock import MockFeedbackProvider, MockSpeechToTextProvider
from app.services.ai.protocols import (
    AudioFeedbackProvider,
    FeedbackProvider,
    SpeechToTextProvider,
)

logger = get_logger(__name__)


def build_speech_to_text_provider(settings: Settings) -> SpeechToTextProvider:
    """Return the configured transcription provider.

    Raises:
        ProviderNotConfiguredError: the selected provider has no credentials.
    """
    if settings.speech_to_text_provider == "mock":
        logger.info("provider_selected", extra={"role": "speech_to_text", "provider": "mock"})
        return MockSpeechToTextProvider()

    if not settings.deepgram_api_key:
        raise ProviderNotConfiguredError("deepgram", reason="missing_api_key")

    logger.info(
        "provider_selected",
        extra={
            "role": "speech_to_text",
            "provider": "deepgram",
            "model": settings.deepgram_model,
            "filler_words_requested": settings.deepgram_filler_words,
        },
    )
    return DeepgramSpeechToTextProvider(
        api_key=settings.deepgram_api_key,
        model=settings.deepgram_model,
        timeout_seconds=settings.analysis_provider_timeout_seconds,
        request_filler_words=settings.deepgram_filler_words,
    )


def build_feedback_provider(settings: Settings) -> FeedbackProvider:
    """Return the configured feedback provider for speech metrics.

    Raises:
        ProviderNotConfiguredError: the selected provider has no credentials.
    """
    return _feedback_provider(settings)


def build_audio_feedback_provider(settings: Settings) -> AudioFeedbackProvider:
    """Return the configured feedback provider for audio measurements.

    The *same* adapter as :func:`build_feedback_provider`, seen through the
    other protocol. Both concrete providers implement both, so one credential,
    one client and one timeout serve both halves of the product — and a
    deployment cannot end up with real feedback on one and demo data on the
    other without noticing.

    Raises:
        ProviderNotConfiguredError: the selected provider has no credentials.
    """
    return _feedback_provider(settings)


def _feedback_provider(settings: Settings) -> ClaudeFeedbackProvider | MockFeedbackProvider:
    if settings.feedback_provider == "mock":
        logger.info("provider_selected", extra={"role": "feedback", "provider": "mock"})
        return MockFeedbackProvider()

    if not settings.anthropic_api_key:
        raise ProviderNotConfiguredError("claude", reason="missing_api_key")

    logger.info(
        "provider_selected",
        extra={"role": "feedback", "provider": "claude", "model": settings.anthropic_model},
    )
    return ClaudeFeedbackProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.analysis_provider_timeout_seconds,
    )
