"""Tests for the Deepgram adapter.

The SDK boundary is where the fake goes — one callable standing in for
``client.listen.v1.media.transcribe_file``. Everything on our side of it is the
real code path, and the responses the fake returns are parsed through
Deepgram's own ``ListenV1Response`` model rather than a hand-rolled stand-in.
So these tests answer the question that matters here:

    provider response → adapter → domain model

They do not, and cannot, verify that Deepgram returns what the fixtures claim:
this environment has no credentials and the host is unreachable.
"""

from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from deepgram.core.api_error import ApiError
from deepgram.core.parse_error import ParsingError
from deepgram.errors import BadRequestError
from deepgram.types.listen_v1response import ListenV1Response

from app.core.errors import ErrorCode
from app.services.ai.deepgram import DeepgramSpeechToTextProvider
from app.services.ai.errors import (
    EmptyTranscriptError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.protocols import SpeechToTextProvider
from app.services.analysis.metrics import compute_metrics
from app.services.analysis.models import Transcript

API_KEY = "test-key-not-a-real-credential"

MODEL_ID = "e1f2a3b4-c5d6-7890-abcd-ef0123456789"


def deepgram_payload(
    *,
    transcript: str = "Um, hello there. This is, uh, a test recording.",
    words: list[dict[str, Any]] | None = None,
    model_name: str = "nova-3",
    duration: float = 6.5,
) -> dict[str, Any]:
    """A response body shaped like Deepgram's pre-recorded transcription API.

    Passed through ``ListenV1Response`` in every test, so a field that the SDK
    would reject here would fail the test rather than silently pass.
    """
    if words is None:
        words = [
            {"word": "um", "start": 0.50, "end": 0.80, "confidence": 0.91},
            {"word": "hello", "start": 0.90, "end": 1.30, "confidence": 0.99},
            {"word": "there", "start": 1.35, "end": 1.70, "confidence": 0.99},
            {"word": "this", "start": 2.60, "end": 2.85, "confidence": 0.98},
            {"word": "is", "start": 2.90, "end": 3.05, "confidence": 0.99},
            {"word": "uh", "start": 3.10, "end": 3.40, "confidence": 0.88},
            {"word": "a", "start": 3.45, "end": 3.55, "confidence": 0.97},
            {"word": "test", "start": 3.60, "end": 3.95, "confidence": 0.99},
            {"word": "recording", "start": 4.00, "end": 4.70, "confidence": 0.99},
        ]
    return {
        "metadata": {
            "request_id": "0e6d1e6a-1111-2222-3333-444455556666",
            "sha256": "a" * 64,
            "created": "2026-08-10T09:00:00.000Z",
            "duration": duration,
            "channels": 1,
            "models": [MODEL_ID],
            "model_info": {
                MODEL_ID: {"name": model_name, "version": "2024-01-09.0", "arch": model_name}
            },
        },
        "results": {
            "channels": [
                {
                    "detected_language": "en",
                    "alternatives": [
                        {"transcript": transcript, "confidence": 0.98, "words": words}
                    ],
                }
            ]
        },
    }


class FakeTranscribeFile:
    """Stands in for the SDK method, recording what the adapter asked for."""

    def __init__(self, *, response: object = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        # Drain the audio stream the way the real client would, so a broken
        # stream surfaces here rather than being skipped.
        request = kwargs.get("request")
        if hasattr(request, "__aiter__"):
            kwargs = {**kwargs, "request": b"".join([chunk async for chunk in request])}
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def build(
    fake: FakeTranscribeFile,
    *,
    model: str = "nova-3",
    request_filler_words: bool = False,
) -> DeepgramSpeechToTextProvider:
    return DeepgramSpeechToTextProvider(
        api_key=API_KEY,
        model=model,
        timeout_seconds=30,
        request_filler_words=request_filler_words,
        transcribe_file=fake,
    )


def transcribe(provider: DeepgramSpeechToTextProvider, path: Path) -> Transcript:
    return anyio.run(lambda: provider.transcribe(path))


def responding(payload: dict[str, Any]) -> FakeTranscribeFile:
    return FakeTranscribeFile(response=ListenV1Response.model_validate(payload))


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    path = tmp_path / "recording.wav"
    path.write_bytes(b"RIFF" + b"audio bytes" * 512)
    return path


# --- The request -----------------------------------------------------------


def test_the_provider_satisfies_the_protocol():
    assert isinstance(build(FakeTranscribeFile()), SpeechToTextProvider)
    assert build(FakeTranscribeFile()).name == "deepgram"


def test_word_timings_are_always_requested(recording: Path):
    """Without them the pause and articulation metrics cannot be computed."""
    fake = responding(deepgram_payload())
    transcribe(build(fake), recording)

    call = fake.calls[0]
    assert call["utterances"] is True
    assert call["model"] == "nova-3"
    assert call["request_options"]["max_retries"] == 0
    assert call["request_options"]["timeout_in_seconds"] == 30


def test_filler_words_are_only_requested_when_configured(recording: Path):
    fake = responding(deepgram_payload())
    transcribe(build(fake, request_filler_words=False), recording)
    assert fake.calls[0]["filler_words"] is False

    fake = responding(deepgram_payload())
    transcribe(build(fake, request_filler_words=True), recording)
    assert fake.calls[0]["filler_words"] is True


def test_the_recording_is_streamed_rather_than_read_into_memory(recording: Path):
    fake = responding(deepgram_payload())
    transcribe(build(fake), recording)

    # The fake drained an async iterator; the bytes still arrive intact.
    assert fake.calls[0]["request"] == recording.read_bytes()


def test_an_unreadable_recording_is_a_provider_error(tmp_path: Path):
    fake = responding(deepgram_payload())
    with pytest.raises(ProviderResponseError) as caught:
        transcribe(build(fake), tmp_path / "missing.wav")

    assert "missing.wav" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


# --- Response mapping ------------------------------------------------------


def test_a_valid_response_becomes_a_transcript(recording: Path):
    transcript = transcribe(build(responding(deepgram_payload())), recording)

    assert transcript.text == "Um, hello there. This is, uh, a test recording."
    assert transcript.language == "en"
    assert transcript.audio_duration_seconds == pytest.approx(6.5)
    assert transcript.provenance.provider == "deepgram"
    assert transcript.provenance.model == "nova-3"
    assert transcript.provenance.is_mock is False


def test_word_timings_are_mapped_faithfully(recording: Path):
    transcript = transcribe(build(responding(deepgram_payload())), recording)

    assert transcript.has_word_timings is True
    assert len(transcript.words) == 9
    first, second = transcript.words[0], transcript.words[1]
    assert (first.text, first.start_seconds, first.end_seconds) == ("um", 0.50, 0.80)
    assert (second.text, second.start_seconds, second.end_seconds) == ("hello", 0.90, 1.30)


def test_the_mapped_transcript_supports_the_metrics(recording: Path):
    """The point of requesting timings: the deterministic layer can use them."""
    transcript = transcribe(build(responding(deepgram_payload())), recording)
    metrics = compute_metrics(transcript)

    assert metrics.word_count == 9
    assert metrics.words_per_minute is not None
    assert metrics.articulation_rate_wpm is not None
    # 2.60 - 1.70 = 0.90s, which clears the 0.5s threshold.
    assert metrics.pause_count == 1


def test_a_word_without_text_is_dropped(recording: Path):
    payload = deepgram_payload(
        words=[
            {"word": "hello", "start": 0.1, "end": 0.4},
            {"word": "   ", "start": 0.5, "end": 0.6},
            {"start": 0.7, "end": 0.8},
        ]
    )
    transcript = transcribe(build(responding(payload)), recording)
    assert [w.text for w in transcript.words] == ["hello"]


@pytest.mark.parametrize(
    "bad_timing",
    [
        {"word": "hello", "start": 0.5},
        {"word": "hello", "end": 0.5},
        {"word": "hello"},
        {"word": "hello", "start": 2.0, "end": 1.0},
        {"word": "hello", "start": -1.0, "end": 1.0},
    ],
)
def test_an_unusable_timing_keeps_the_word_but_drops_the_timing(
    recording: Path, bad_timing: dict[str, Any]
):
    """Never guessed at: the metrics omit timing-derived values instead."""
    payload = deepgram_payload(words=[bad_timing])
    transcript = transcribe(build(responding(payload)), recording)

    assert [w.text for w in transcript.words] == ["hello"]
    assert transcript.words[0].is_timed is False
    assert transcript.has_word_timings is False
    assert compute_metrics(transcript).pause_count is None


def test_a_response_with_no_speech_is_reported_as_empty(recording: Path):
    payload = deepgram_payload(transcript="", words=[])
    with pytest.raises(EmptyTranscriptError) as caught:
        transcribe(build(responding(payload)), recording)
    assert caught.value.error_code is ErrorCode.TRANSCRIPT_EMPTY


def test_a_zero_duration_is_omitted_rather_than_stored(recording: Path):
    payload = deepgram_payload(duration=0.0)
    transcript = transcribe(build(responding(payload)), recording)
    assert transcript.audio_duration_seconds is None


@pytest.mark.parametrize(
    "results",
    [
        {"channels": []},
        {"channels": [{"alternatives": []}]},
        {"channels": [{"detected_language": "en"}]},
    ],
)
def test_a_response_missing_its_alternatives_is_a_provider_error(
    recording: Path, results: dict[str, Any]
):
    payload = deepgram_payload()
    payload["results"] = results
    with pytest.raises(ProviderResponseError) as caught:
        transcribe(build(responding(payload)), recording)
    assert caught.value.error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR


def test_an_unexpected_response_type_is_a_provider_error(recording: Path):
    """``transcribe_file`` has a second return shape we never ask for."""
    fake = FakeTranscribeFile(response={"request_id": "abc"})
    with pytest.raises(ProviderResponseError):
        transcribe(build(fake), recording)


# --- Disfluency reporting --------------------------------------------------


def test_disfluencies_are_not_claimed_when_they_were_not_requested(recording: Path):
    provider = build(responding(deepgram_payload()), request_filler_words=False)
    transcript = transcribe(provider, recording)

    assert transcript.includes_disfluencies is False
    # And so the filler statistics are omitted rather than reported as zero.
    assert compute_metrics(transcript).filler_words is None


def test_disfluencies_are_claimed_when_requested_and_the_model_supports_them(recording: Path):
    provider = build(responding(deepgram_payload(model_name="nova-3")), request_filler_words=True)
    transcript = transcribe(provider, recording)

    assert transcript.includes_disfluencies is True
    stats = compute_metrics(transcript).filler_words
    assert stats is not None
    assert stats.hesitation_count == 2  # "um" and "uh"


def test_disfluencies_are_not_claimed_when_an_unknown_model_answered(recording: Path):
    """A request routed elsewhere must not be reported as verbatim."""
    provider = build(
        responding(deepgram_payload(model_name="whisper-large")), request_filler_words=True
    )
    transcript = transcribe(provider, recording)

    assert transcript.includes_disfluencies is False
    assert compute_metrics(transcript).filler_words is None


def test_disfluencies_are_not_claimed_when_the_model_cannot_be_identified(recording: Path):
    payload = deepgram_payload()
    payload["metadata"]["model_info"] = {}
    provider = build(responding(payload), request_filler_words=True)

    assert transcribe(provider, recording).includes_disfluencies is False


# --- Failure translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (
            httpx.ReadTimeout("timed out"),
            ProviderTimeoutError,
            ErrorCode.ANALYSIS_PROVIDER_TIMEOUT,
        ),
        (
            httpx.ConnectTimeout("timed out"),
            ProviderTimeoutError,
            ErrorCode.ANALYSIS_PROVIDER_TIMEOUT,
        ),
        (
            httpx.ConnectError("connection refused"),
            ProviderUnavailableError,
            ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE,
        ),
        (
            ApiError(status_code=429, body={"err_msg": "rate limited"}),
            ProviderRateLimitedError,
            ErrorCode.ANALYSIS_RATE_LIMITED,
        ),
        (
            ApiError(status_code=401, body={"err_msg": "bad key"}),
            ProviderNotConfiguredError,
            ErrorCode.ANALYSIS_NOT_CONFIGURED,
        ),
        (
            ApiError(status_code=403, body={"err_msg": "forbidden"}),
            ProviderNotConfiguredError,
            ErrorCode.ANALYSIS_NOT_CONFIGURED,
        ),
        (
            ApiError(status_code=503, body={"err_msg": "unavailable"}),
            ProviderUnavailableError,
            ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE,
        ),
        (
            ApiError(status_code=500, body={"err_msg": "boom"}),
            ProviderResponseError,
            ErrorCode.ANALYSIS_PROVIDER_ERROR,
        ),
        (
            BadRequestError(body={"err_msg": "bad option"}),
            ProviderResponseError,
            ErrorCode.ANALYSIS_PROVIDER_ERROR,
        ),
        (
            ParsingError(status_code=200, body={"unexpected": True}),
            ProviderResponseError,
            ErrorCode.ANALYSIS_PROVIDER_ERROR,
        ),
    ],
)
def test_every_sdk_failure_becomes_one_of_ours(
    recording: Path,
    error: Exception,
    expected: type[ProviderError],
    code: ErrorCode,
):
    fake = FakeTranscribeFile(error=error)
    with pytest.raises(expected) as caught:
        transcribe(build(fake), recording)
    assert caught.value.error_code is code


def test_no_deepgram_exception_escapes_the_adapter(recording: Path):
    """Callers handle one vocabulary, not a vendor's."""
    for error in (
        ApiError(status_code=418, body={"err_msg": "teapot"}),
        BadRequestError(body={}),
        httpx.ReadError("reset"),
    ):
        with pytest.raises(ProviderError):
            transcribe(build(FakeTranscribeFile(error=error)), recording)


def test_a_provider_failure_leaks_nothing_to_the_client(recording: Path):
    """The vendor's body can echo the request; it must not reach a response."""
    body = {
        "err_msg": "Invalid credentials for token sk-deepgram-supersecret",
        "request_id": "8f14e45f-0000-1111-2222-333344445555",
    }
    fake = FakeTranscribeFile(error=ApiError(status_code=401, body=body))

    with pytest.raises(ProviderError) as caught:
        transcribe(build(fake), recording)

    error = caught.value
    visible = f"{error} {error.to_api_error().message}"
    for secret in ("sk-deepgram-supersecret", "8f14e45f", "Invalid credentials", "ApiError"):
        assert secret not in visible

    logged = str(error.log_context())
    assert "sk-deepgram-supersecret" not in logged
    assert logged.count("deepgram") >= 1


def test_a_provider_without_a_key_refuses_to_be_constructed():
    with pytest.raises(ProviderNotConfiguredError) as caught:
        DeepgramSpeechToTextProvider(api_key="", model="nova-3", timeout_seconds=30)
    assert caught.value.error_code is ErrorCode.ANALYSIS_NOT_CONFIGURED
