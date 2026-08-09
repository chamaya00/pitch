"""Tests for the provider boundary and the mock implementations.

Three things are being pinned down here:

1. The mocks are deterministic, so anything built on top of them is testable.
2. A mock result is unmistakably a mock, at every layer that can see it.
3. A provider failure reaches the client as our own generic wording, and the
   vendor's detail stays in the logs.
"""

import inspect
from pathlib import Path

import anyio
import pytest

from app.core.errors import STATUS_BY_CODE, ApiError, ErrorCode
from app.services.ai.errors import (
    EmptyTranscriptError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.mock import (
    MOCK_PROVIDER_NAME,
    MockFeedbackProvider,
    MockSpeechToTextProvider,
)
from app.services.ai.protocols import FeedbackProvider, SpeechToTextProvider
from app.services.analysis.metrics import compute_metrics
from app.services.analysis.models import SpeechMetrics, Transcript

PROVIDER_ERRORS = [
    (ProviderNotConfiguredError, ErrorCode.ANALYSIS_NOT_CONFIGURED),
    (ProviderUnavailableError, ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE),
    (ProviderTimeoutError, ErrorCode.ANALYSIS_PROVIDER_TIMEOUT),
    (ProviderRateLimitedError, ErrorCode.ANALYSIS_RATE_LIMITED),
    (ProviderResponseError, ErrorCode.ANALYSIS_PROVIDER_ERROR),
    (EmptyTranscriptError, ErrorCode.TRANSCRIPT_EMPTY),
]


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    """A file standing in for a stored recording. The mock only hashes it."""
    path = tmp_path / "recording.wav"
    path.write_bytes(b"RIFF" + b"deterministic bytes" * 64)
    return path


def transcribe(provider: MockSpeechToTextProvider, path: Path) -> Transcript:
    """Drive one async provider call from a synchronous test.

    ``anyio`` already ships with Starlette, so this needs no test-only
    dependency and no event-loop fixture.
    """
    return anyio.run(lambda: provider.transcribe(path))


# --- The protocol boundary -------------------------------------------------


def test_the_mocks_satisfy_their_protocols():
    assert isinstance(MockSpeechToTextProvider(), SpeechToTextProvider)
    assert isinstance(MockFeedbackProvider(), FeedbackProvider)


def test_a_feedback_provider_cannot_be_handed_audio():
    """The architectural guarantee, enforced by the signature itself.

    Feedback is generated from a transcript and numbers. If a path or bytes ever
    appear in this signature, the layer has stopped being an explanation step.
    """
    parameters = inspect.signature(FeedbackProvider.generate).parameters
    assert set(parameters) == {"self", "transcript", "metrics"}
    for forbidden in ("audio", "path", "file", "recording"):
        assert not any(forbidden in name for name in parameters)


def test_a_speech_to_text_provider_receives_a_resolved_path():
    """Providers are given a path; they never build one from an id or a name."""
    parameters = inspect.signature(SpeechToTextProvider.transcribe).parameters
    assert set(parameters) == {"self", "audio_path"}
    assert parameters["audio_path"].annotation is Path


# --- Mock transcription ----------------------------------------------------


def test_the_mock_transcript_is_marked_as_a_mock(recording: Path):
    transcript = transcribe(MockSpeechToTextProvider(), recording)
    assert transcript.provenance.is_mock is True
    assert transcript.provenance.provider == MOCK_PROVIDER_NAME


def test_the_mock_transcript_says_what_it_is_in_its_own_words(recording: Path):
    """Belt and braces: a mock transcript that reached a screen would still read as one."""
    text = transcribe(MockSpeechToTextProvider(), recording).text.lower()
    assert "mock" in text or "placeholder" in text


def test_the_same_recording_always_produces_the_same_transcript(recording: Path):
    provider = MockSpeechToTextProvider()
    assert transcribe(provider, recording) == transcribe(provider, recording)


def test_a_fresh_provider_instance_produces_the_same_transcript(recording: Path):
    """Determinism comes from the file, not from instance state."""
    assert transcribe(MockSpeechToTextProvider(), recording) == transcribe(
        MockSpeechToTextProvider(), recording
    )


def test_different_recordings_produce_different_transcripts(tmp_path: Path):
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    first.write_bytes(b"one set of bytes")
    second.write_bytes(b"a different set of bytes")

    provider = MockSpeechToTextProvider()
    assert transcribe(provider, first) != transcribe(provider, second)


def test_the_mock_supplies_complete_word_timings(recording: Path):
    transcript = transcribe(MockSpeechToTextProvider(), recording)

    assert transcript.has_word_timings is True
    assert len(transcript.words) == len(transcript.text.split())
    for word in transcript.words:
        assert word.start_seconds is not None
        assert word.end_seconds is not None
        assert word.end_seconds > word.start_seconds


def test_the_mock_timeline_moves_forward(recording: Path):
    words = transcribe(MockSpeechToTextProvider(), recording).words
    starts = [word.start_seconds for word in words]
    assert starts == sorted(starts)


def test_the_mock_declares_that_it_is_verbatim(recording: Path):
    """Its script is verbatim by construction, so filler counting is meaningful."""
    transcript = transcribe(MockSpeechToTextProvider(), recording)
    assert transcript.includes_disfluencies is True
    assert compute_metrics(transcript).filler_words is not None


def test_the_mock_transcript_supports_the_full_metric_set(recording: Path):
    """The point of the mock: exercising the pipeline end to end."""
    metrics = compute_metrics(transcribe(MockSpeechToTextProvider(), recording))

    assert metrics.word_count > 0
    assert metrics.words_per_minute is not None
    assert metrics.articulation_rate_wpm is not None
    assert metrics.pause_count is not None and metrics.pause_count > 0
    assert metrics.filler_words is not None and metrics.filler_words.hesitation_count > 0


def test_a_script_with_no_speech_is_reported_as_an_empty_transcript(recording: Path):
    with pytest.raises(EmptyTranscriptError) as caught:
        transcribe(MockSpeechToTextProvider(script="   "), recording)
    assert caught.value.error_code is ErrorCode.TRANSCRIPT_EMPTY


def test_an_unreadable_recording_becomes_a_provider_error(tmp_path: Path):
    missing = tmp_path / "gone.wav"
    with pytest.raises(ProviderResponseError) as caught:
        transcribe(MockSpeechToTextProvider(), missing)

    assert caught.value.error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR
    # Neither the path nor the OS error class may reach the client.
    assert "gone.wav" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert "FileNotFoundError" not in str(caught.value)


def test_transcribing_neither_writes_nor_modifies_anything(tmp_path: Path, recording: Path):
    """The provider reads the recording; it does not touch the filesystem."""
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    transcribe(MockSpeechToTextProvider(), recording)

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


# --- Mock feedback ---------------------------------------------------------


def generate(metrics: SpeechMetrics, transcript: Transcript):
    return anyio.run(
        lambda: MockFeedbackProvider().generate(transcript=transcript, metrics=metrics)
    )


@pytest.fixture
def mock_transcript(recording: Path) -> Transcript:
    return transcribe(MockSpeechToTextProvider(), recording)


def test_feedback_is_marked_as_a_mock(mock_transcript: Transcript):
    feedback = generate(compute_metrics(mock_transcript), mock_transcript)
    assert feedback.provenance.is_mock is True
    assert "not by a language model" in feedback.summary


def test_feedback_is_deterministic(mock_transcript: Transcript):
    metrics = compute_metrics(mock_transcript)
    assert generate(metrics, mock_transcript) == generate(metrics, mock_transcript)


def test_feedback_quotes_only_measured_numbers(mock_transcript: Transcript):
    metrics = compute_metrics(mock_transcript)
    feedback = generate(metrics, mock_transcript)
    text = " ".join((feedback.summary, *feedback.strengths, *feedback.areas_to_improve))

    assert str(metrics.word_count) in text
    assert str(metrics.words_per_minute) in text
    assert str(metrics.pause_count) in text


def test_feedback_makes_no_claim_about_a_metric_that_was_not_measured(
    mock_transcript: Transcript,
):
    """The rule a real feedback provider has to follow, pinned down on the mock."""
    bare = SpeechMetrics(word_count=42)
    feedback = generate(bare, mock_transcript)
    text = " ".join(
        (feedback.summary, *feedback.strengths, *feedback.areas_to_improve, feedback.next_action)
    ).lower()

    assert "42" in text
    for absent in ("per minute", "pause", "hesitation", "filler"):
        assert absent not in text


def test_feedback_always_offers_something_to_say(mock_transcript: Transcript):
    feedback = generate(SpeechMetrics(word_count=0), mock_transcript)
    assert feedback.summary
    assert feedback.strengths
    assert feedback.areas_to_improve
    assert feedback.next_action


# --- Provider errors -------------------------------------------------------


@pytest.mark.parametrize(("error_class", "code"), PROVIDER_ERRORS)
def test_each_provider_failure_maps_to_its_error_code(
    error_class: type[ProviderError], code: ErrorCode
):
    error = error_class("deepgram")
    api_error = error.to_api_error()

    assert error.error_code is code
    assert isinstance(api_error, ApiError)
    assert api_error.code is code
    assert api_error.status_code == STATUS_BY_CODE[code]


@pytest.mark.parametrize(("error_class", "code"), PROVIDER_ERRORS)
def test_a_provider_failure_never_carries_vendor_detail_to_the_client(
    error_class: type[ProviderError], code: ErrorCode
):
    del code
    leak = "HTTPStatusError: 401 from https://api.vendor.test?key=sk-secret"
    error = error_class("deepgram", reason=leak)

    for visible in (str(error), error.to_api_error().message):
        assert leak not in visible
        assert "sk-secret" not in visible
        assert "HTTPStatusError" not in visible
        assert "https://" not in visible
        assert visible == error_class.public_message


@pytest.mark.parametrize(("error_class", "code"), PROVIDER_ERRORS)
def test_every_provider_failure_has_its_own_wording(
    error_class: type[ProviderError], code: ErrorCode
):
    del code
    assert error_class.public_message.strip()
    assert error_class.public_message.endswith((".", "!"))


def test_the_logged_reason_is_kept_out_of_the_response_but_available_to_logs():
    error = ProviderTimeoutError("deepgram", reason="ReadTimeout")
    context = error.log_context()

    assert context["provider"] == "deepgram"
    assert context["error_code"] == ErrorCode.ANALYSIS_PROVIDER_TIMEOUT.value
    assert context["reason"] == "ReadTimeout"


def test_an_oversized_reason_is_truncated_before_it_reaches_the_logs():
    error = ProviderResponseError("deepgram", reason="x" * 5_000)
    assert len(error.log_context()["reason"]) <= 120


def test_a_failure_without_a_reason_logs_nothing_extra():
    assert "reason" not in ProviderUnavailableError("deepgram").log_context()
