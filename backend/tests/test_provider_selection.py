"""Provider selection, wiring, and the contract both implementations share.

Two things are being pinned down:

1. A configured real provider never silently becomes a mock. That is the single
   most dangerous failure this layer could have — an operator would have no way
   to tell demo data from analysis.
2. Whichever provider is selected, callers see the same domain shapes. Step 7D
   should not be able to tell which one is underneath.
"""

import inspect
from pathlib import Path
from typing import Any

import anyio
import pytest
from anthropic.resources.messages.messages import parse_response
from anthropic.types import Message
from deepgram.types.listen_v1response import ListenV1Response
from fastapi.testclient import TestClient

from app.api.deps import get_feedback_provider, get_speech_to_text_provider
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.services.ai.claude import ClaudeFeedbackProvider, _FeedbackPayload
from app.services.ai.deepgram import DeepgramSpeechToTextProvider
from app.services.ai.errors import ProviderNotConfiguredError
from app.services.ai.factory import build_feedback_provider, build_speech_to_text_provider
from app.services.ai.mock import MockFeedbackProvider, MockSpeechToTextProvider
from app.services.ai.protocols import FeedbackProvider, SpeechToTextProvider
from app.services.analysis.metrics import compute_metrics
from app.services.analysis.models import Feedback, SpeechMetrics, Transcript
from tests.test_claude_provider import VALID_OUTPUT, FakeParse
from tests.test_deepgram_provider import FakeTranscribeFile, deepgram_payload


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- Selection -------------------------------------------------------------


def test_the_default_configuration_is_mock_and_says_so():
    """Development runs on demo data — visibly, never by accident."""
    config = settings()

    assert config.speech_to_text_provider == "mock"
    assert config.feedback_provider == "mock"
    assert config.uses_mock_providers is True
    assert config.public_config()["speech_to_text_provider"] == "mock"
    assert config.public_config()["feedback_provider"] == "mock"

    assert isinstance(build_speech_to_text_provider(config), MockSpeechToTextProvider)
    assert isinstance(build_feedback_provider(config), MockFeedbackProvider)


def test_selecting_deepgram_builds_deepgram():
    config = settings(speech_to_text_provider="deepgram", deepgram_api_key="key")
    provider = build_speech_to_text_provider(config)

    assert isinstance(provider, DeepgramSpeechToTextProvider)
    assert config.uses_mock_providers is True  # feedback is still mock
    assert provider.name == "deepgram"


def test_selecting_claude_builds_claude():
    config = settings(feedback_provider="claude", anthropic_api_key="key")
    provider = build_feedback_provider(config)

    assert isinstance(provider, ClaudeFeedbackProvider)
    assert provider.name == "claude"


def test_both_real_providers_leaves_no_mock_in_the_deployment():
    config = settings(
        speech_to_text_provider="deepgram",
        deepgram_api_key="key",
        feedback_provider="claude",
        anthropic_api_key="key",
    )
    assert config.uses_mock_providers is False


def test_an_unknown_provider_name_is_rejected_at_startup():
    with pytest.raises(ValueError):
        settings(speech_to_text_provider="whisper")
    with pytest.raises(ValueError):
        settings(feedback_provider="gpt")


def test_provider_selection_reads_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    """The path a deployment actually uses, not just an init kwarg."""
    monkeypatch.setenv("SPEECH_TO_TEXT_PROVIDER", "deepgram")
    monkeypatch.setenv("FEEDBACK_PROVIDER", "claude")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")

    config = Settings(_env_file=None)
    assert isinstance(build_speech_to_text_provider(config), DeepgramSpeechToTextProvider)
    assert isinstance(build_feedback_provider(config), ClaudeFeedbackProvider)


# --- No silent fallback ----------------------------------------------------


def test_deepgram_without_a_key_fails_rather_than_falling_back():
    """The failure this design exists to prevent."""
    config = settings(speech_to_text_provider="deepgram")
    with pytest.raises(ProviderNotConfiguredError) as caught:
        build_speech_to_text_provider(config)
    assert caught.value.error_code is ErrorCode.ANALYSIS_NOT_CONFIGURED


def test_claude_without_a_key_fails_rather_than_falling_back():
    config = settings(feedback_provider="claude")
    with pytest.raises(ProviderNotConfiguredError) as caught:
        build_feedback_provider(config)
    assert caught.value.error_code is ErrorCode.ANALYSIS_NOT_CONFIGURED


def test_an_unavailable_real_provider_never_yields_a_mock():
    """Whatever goes wrong, a mock is only ever returned when it was chosen."""
    for config in (
        settings(speech_to_text_provider="deepgram"),
        settings(speech_to_text_provider="deepgram", deepgram_api_key=""),
    ):
        with pytest.raises(ProviderNotConfiguredError):
            build_speech_to_text_provider(config)

    for config in (
        settings(feedback_provider="claude"),
        settings(feedback_provider="claude", anthropic_api_key=""),
    ):
        with pytest.raises(ProviderNotConfiguredError):
            build_feedback_provider(config)


# --- Dependency injection --------------------------------------------------


def test_the_dependencies_return_the_configured_providers():
    config = settings()
    assert isinstance(get_speech_to_text_provider(config), SpeechToTextProvider)
    assert isinstance(get_feedback_provider(config), FeedbackProvider)


@pytest.mark.parametrize(
    ("dependency", "config"),
    [
        (get_speech_to_text_provider, settings(speech_to_text_provider="deepgram")),
        (get_feedback_provider, settings(feedback_provider="claude")),
    ],
)
def test_a_missing_credential_becomes_the_documented_envelope(dependency: Any, config: Settings):
    """Not a 500, and not a mock: the documented 503 with its error code."""
    with pytest.raises(ApiError) as caught:
        dependency(config)

    assert caught.value.code is ErrorCode.ANALYSIS_NOT_CONFIGURED
    assert caught.value.status_code == 503


def test_the_dependencies_are_annotated_with_the_protocols():
    """Routes depend on the interface, never on a vendor's class."""
    assert inspect.signature(get_speech_to_text_provider).return_annotation is SpeechToTextProvider
    assert inspect.signature(get_feedback_provider).return_annotation is FeedbackProvider


# --- Secrets ---------------------------------------------------------------


def test_no_provider_key_is_exposed_through_public_config():
    config = settings(
        deepgram_api_key="dg-secret-value",
        anthropic_api_key="sk-ant-secret-value",
        speech_to_text_provider="deepgram",
        feedback_provider="claude",
    )
    public = str(config.public_config())

    assert "dg-secret-value" not in public
    assert "sk-ant-secret-value" not in public
    assert "deepgram_api_key" not in public
    assert "anthropic_api_key" not in public


def test_the_public_config_endpoint_exposes_no_provider_keys(client: TestClient):
    body = client.get("/api/v1/config").json()

    for field in ("deepgram_api_key", "anthropic_api_key", "anthropic_model", "deepgram_model"):
        assert field not in body


def test_provider_keys_never_reach_the_stored_record():
    """Provenance is persisted; a key must not be able to ride along in it."""
    secret = "dg-secret-value"
    fake = FakeTranscribeFile(response=ListenV1Response.model_validate(deepgram_payload()))
    provider = DeepgramSpeechToTextProvider(
        api_key=secret, model="nova-3", timeout_seconds=30, transcribe_file=fake
    )

    transcript = anyio.run(lambda: provider.transcribe(_recording()))
    written = transcript.model_dump_json()

    assert secret not in written
    assert transcript.provenance.provider == "deepgram"
    assert transcript.provenance.model == "nova-3"

    feedback = claude_feedback(transcript, compute_metrics(transcript))
    assert "sk-ant" not in feedback.model_dump_json()
    assert feedback.provenance.provider == "claude"


# --- Cross-provider contract -----------------------------------------------


def stt_transcript() -> Transcript:
    """A transcript produced by the real Deepgram adapter, SDK boundary faked."""
    fake = FakeTranscribeFile(response=ListenV1Response.model_validate(deepgram_payload()))
    provider = DeepgramSpeechToTextProvider(
        api_key="key", model="nova-3", timeout_seconds=30, transcribe_file=fake
    )
    return anyio.run(lambda: provider.transcribe(_recording()))


def _recording() -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp()) / "recording.wav"
    path.write_bytes(b"RIFF" + b"audio" * 128)
    return path


def claude_feedback(transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
    import json

    message = Message.model_validate(
        {
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps(VALID_OUTPUT)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    fake = FakeParse(response=parse_response(response=message, output_format=_FeedbackPayload))
    provider = ClaudeFeedbackProvider(
        api_key="key", model="claude-opus-5", timeout_seconds=60, messages_parse=fake
    )
    return anyio.run(lambda: provider.generate(transcript=transcript, metrics=metrics))


def mock_transcript() -> Transcript:
    provider = MockSpeechToTextProvider()
    return anyio.run(lambda: provider.transcribe(_recording()))


def mock_feedback(transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
    provider = MockFeedbackProvider()
    return anyio.run(lambda: provider.generate(transcript=transcript, metrics=metrics))


def test_both_transcription_providers_produce_the_same_domain_shape():
    real, mock = stt_transcript(), mock_transcript()

    assert type(real) is type(mock) is Transcript
    assert real.has_word_timings and mock.has_word_timings
    assert real.provenance.is_mock is False
    assert mock.provenance.is_mock is True
    # And the deterministic layer reads both the same way.
    for transcript in (real, mock):
        metrics = compute_metrics(transcript)
        assert metrics.word_count > 0
        assert metrics.words_per_minute is not None
        assert metrics.pause_count is not None


def test_both_feedback_providers_produce_the_same_domain_shape():
    transcript = stt_transcript()
    metrics = compute_metrics(transcript)

    real = claude_feedback(transcript, metrics)
    mock = mock_feedback(transcript, metrics)

    assert type(real) is type(mock) is Feedback
    for feedback in (real, mock):
        assert feedback.summary
        assert feedback.next_action
        assert isinstance(feedback.strengths, tuple)
        assert isinstance(feedback.areas_to_improve, tuple)
    assert real.provenance.is_mock is False
    assert mock.provenance.is_mock is True


def test_the_real_providers_are_interchangeable_with_the_mocks():
    """7D will hold protocols, and must not care which is underneath."""
    for provider in (
        MockSpeechToTextProvider(),
        DeepgramSpeechToTextProvider(
            api_key="key", model="nova-3", timeout_seconds=30, transcribe_file=FakeTranscribeFile()
        ),
    ):
        assert isinstance(provider, SpeechToTextProvider)

    for feedback_provider in (
        MockFeedbackProvider(),
        ClaudeFeedbackProvider(
            api_key="key", model="claude-opus-5", timeout_seconds=60, messages_parse=FakeParse()
        ),
    ):
        assert isinstance(feedback_provider, FeedbackProvider)


def test_the_real_feedback_provider_is_never_handed_audio():
    """The 7B guarantee, still true once a real vendor is behind it."""
    parameters = inspect.signature(ClaudeFeedbackProvider.generate).parameters
    assert set(parameters) == {"self", "transcript", "metrics"}
