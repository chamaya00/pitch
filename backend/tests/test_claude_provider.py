"""Tests for the Claude feedback adapter.

The fake replaces ``client.messages.parse``, and the responses it returns are
built by the SDK's *own* parser from a raw ``Message`` — the same function the
real client runs on the wire response. So a schema mismatch fails here exactly
the way it would in production, rather than the way a hand-written stub decided
it should.

These tests do not verify that Claude returns what the fixtures contain: this
environment has no Anthropic credentials, so no request has ever been answered.
"""

import json
from typing import Any

import anthropic
import anyio
import httpx
import pytest
from anthropic.resources.messages.messages import parse_response
from anthropic.types import Message
from pydantic import ValidationError

from app.core.errors import ErrorCode
from app.services.ai.claude import SYSTEM_PROMPT, ClaudeFeedbackProvider, _FeedbackPayload
from app.services.ai.errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.protocols import FeedbackProvider
from app.services.analysis.models import (
    Feedback,
    FillerCategory,
    FillerTermCount,
    FillerWordStats,
    Provenance,
    SpeechMetrics,
    Transcript,
    TranscriptWord,
)

API_KEY = "sk-ant-test-not-a-real-credential"

VALID_OUTPUT: dict[str, Any] = {
    "summary": "You spoke for about a minute at a steady, easy-to-follow pace.",
    "strengths": ["Your pace of 132.0 words per minute is comfortable to follow."],
    "areas_to_improve": ["You paused 4 times; a couple ran long enough to feel like a stall."],
    "next_action": "Record the same passage again and aim to shorten the longest pause.",
}

FULL_METRICS = SpeechMetrics(
    word_count=140,
    speaking_duration_seconds=63.6,
    words_per_minute=132.0,
    articulation_rate_wpm=148.2,
    pause_threshold_seconds=0.5,
    pause_count=4,
    total_pause_seconds=6.4,
    longest_pause_seconds=2.1,
    mean_pause_seconds=1.6,
    filler_words=FillerWordStats(
        hesitation_count=3,
        discourse_marker_count=2,
        by_term=(
            FillerTermCount(term="um", category=FillerCategory.HESITATION, count=3),
            FillerTermCount(term="like", category=FillerCategory.DISCOURSE_MARKER, count=2),
        ),
    ),
)

BARE_METRICS = SpeechMetrics(word_count=140)

TRANSCRIPT = Transcript(
    text="Um, this is the recording we are going to talk about.",
    words=(TranscriptWord(text="Um,", start_seconds=0.2, end_seconds=0.5),),
    includes_disfluencies=True,
    provenance=Provenance(provider="deepgram", model="nova-3", is_mock=False),
)


def message_with(text: str, *, stop_reason: str = "end_turn") -> Message:
    """A wire-shaped Message, as the API would return it."""
    return Message.model_validate(
        {
            "id": "msg_01ABCdefGHIjklMNOpqrSTU",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [] if stop_reason == "refusal" else [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 900, "output_tokens": 120},
        }
    )


def parsed(payload: dict[str, Any] | str, *, stop_reason: str = "end_turn") -> object:
    """Run the SDK's real parser over a raw message, as the client does."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    message = message_with(text, stop_reason=stop_reason)
    return parse_response(response=message, output_format=_FeedbackPayload)


class FakeParse:
    """Stands in for ``client.messages.parse``, recording the request."""

    def __init__(self, *, response: object = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def build(fake: FakeParse, *, model: str = "claude-opus-5") -> ClaudeFeedbackProvider:
    return ClaudeFeedbackProvider(
        api_key=API_KEY, model=model, timeout_seconds=60, messages_parse=fake
    )


def generate(
    provider: ClaudeFeedbackProvider,
    *,
    metrics: SpeechMetrics = FULL_METRICS,
    transcript: Transcript = TRANSCRIPT,
) -> Feedback:
    return anyio.run(lambda: provider.generate(transcript=transcript, metrics=metrics))


def answering(payload: dict[str, Any] | str, *, stop_reason: str = "end_turn") -> FakeParse:
    return FakeParse(response=parsed(payload, stop_reason=stop_reason))


def answering_unparseable(output: Any) -> FakeParse:
    """A fake that fails the way the real client does on bad model output.

    The SDK validates the model's JSON *inside* ``messages.parse``, so an
    unusable response raises out of the call rather than coming back as a
    return value. Building the fixture through the same parser keeps that
    faithful — and proves the payloads below really are rejected.
    """
    try:
        parsed(output)
    except ValidationError as exc:
        return FakeParse(error=exc)
    raise AssertionError(f"expected the SDK parser to reject: {output!r}")


def sent_prompt(fake: FakeParse) -> str:
    return str(fake.calls[0]["messages"][0]["content"])


# --- The request -----------------------------------------------------------


def test_the_provider_satisfies_the_protocol():
    assert isinstance(build(FakeParse()), FeedbackProvider)
    assert build(FakeParse()).name == "claude"


def test_structured_output_is_requested_rather_than_parsed_out_of_prose():
    fake = answering(VALID_OUTPUT)
    generate(build(fake))

    call = fake.calls[0]
    assert call["output_format"] is _FeedbackPayload
    assert call["model"] == "claude-opus-5"
    assert call["system"] == SYSTEM_PROMPT


def test_the_model_is_configurable():
    fake = answering(VALID_OUTPUT)
    feedback = generate(build(fake, model="claude-sonnet-5"))

    assert fake.calls[0]["model"] == "claude-sonnet-5"
    assert feedback.provenance.model == "claude-sonnet-5"


def test_the_prompt_states_the_rules_this_layer_depends_on():
    """The wording can change; these commitments cannot quietly disappear."""
    lowered = SYSTEM_PROMPT.lower()
    for rule in ("never invent", "authoritative", "absent", "zero", "pronunciation"):
        assert rule in lowered
    assert "score" in lowered


def test_only_measurements_and_the_transcript_are_sent():
    fake = answering(VALID_OUTPUT)
    generate(build(fake))
    prompt = sent_prompt(fake)

    assert "132.0" in prompt
    assert TRANSCRIPT.text in prompt
    # Nothing identifying, and nothing about where the audio lives.
    for forbidden in (API_KEY, "recording.wav", "/storage", "recording_id", "deepgram"):
        assert forbidden not in prompt


def test_a_metric_that_was_not_measured_is_absent_from_the_prompt():
    """Omitted rather than sent as null, so there is no blank to fill in."""
    fake = answering(VALID_OUTPUT)
    generate(build(fake), metrics=BARE_METRICS)
    prompt = sent_prompt(fake)

    assert "140" in prompt
    for absent in ("words_per_minute", "pause_count", "filler_words", "null"):
        assert absent not in prompt


def test_measured_metrics_are_sent_verbatim():
    fake = answering(VALID_OUTPUT)
    generate(build(fake))
    body = sent_prompt(fake).split("Measured metrics for this recording:")[1]
    payload = json.loads(body.split("\n\nA metric")[0])

    assert payload["words_per_minute"] == 132.0
    assert payload["pause_count"] == 4
    assert payload["filler_words"]["hesitation_count"] == 3


def test_the_discourse_marker_count_is_labelled_where_the_model_reads_it():
    """It is a word-frequency count, and must not be presented as hesitation."""
    fake = answering(VALID_OUTPUT)
    generate(build(fake))
    prompt = sent_prompt(fake)

    assert "discourse_marker_count_lexical_match_may_include_normal_use" in prompt


def test_a_very_long_transcript_is_truncated_and_said_to_be():
    fake = answering(VALID_OUTPUT)
    long_transcript = Transcript(
        text="word " * 5000,
        includes_disfluencies=False,
        provenance=TRANSCRIPT.provenance,
    )
    generate(build(fake), transcript=long_transcript)
    prompt = sent_prompt(fake)

    assert len(prompt) < 20_000
    assert "truncated" in prompt


# --- Response mapping ------------------------------------------------------


def test_a_valid_response_becomes_feedback():
    feedback = generate(build(answering(VALID_OUTPUT)))

    assert feedback.summary == VALID_OUTPUT["summary"]
    assert feedback.strengths == tuple(VALID_OUTPUT["strengths"])
    assert feedback.areas_to_improve == tuple(VALID_OUTPUT["areas_to_improve"])
    assert feedback.next_action == VALID_OUTPUT["next_action"]
    assert feedback.provenance.provider == "claude"
    assert feedback.provenance.is_mock is False


def test_blank_list_entries_are_dropped():
    payload = {**VALID_OUTPUT, "strengths": ["Good pace.", "   ", ""]}
    feedback = generate(build(answering(payload)))
    assert feedback.strengths == ("Good pace.",)


def test_a_runaway_list_is_capped():
    payload = {**VALID_OUTPUT, "areas_to_improve": [f"point {i}" for i in range(30)]}
    feedback = generate(build(answering(payload)))
    assert len(feedback.areas_to_improve) == 5


def test_empty_lists_are_acceptable():
    """A short honest response is better than a padded one."""
    payload = {**VALID_OUTPUT, "strengths": [], "areas_to_improve": []}
    feedback = generate(build(answering(payload)))
    assert feedback.strengths == ()
    assert feedback.areas_to_improve == ()


@pytest.mark.parametrize("field", ["summary", "next_action"])
def test_a_blank_required_field_is_a_provider_error(field: str):
    payload = {**VALID_OUTPUT, field: "   "}
    with pytest.raises(ProviderResponseError) as caught:
        generate(build(answering(payload)))
    assert caught.value.error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR


@pytest.mark.parametrize(
    "output",
    [
        {"summary": "s", "strengths": [], "next_action": "n"},  # missing field
        {**VALID_OUTPUT, "score": 87},  # extra field — no composite scores
        {"summary": 5, "strengths": [], "areas_to_improve": [], "next_action": "n"},
        "not json at all",
        '{"summary": "truncated mid',
    ],
)
def test_output_that_does_not_match_the_schema_is_a_provider_error(output: Any):
    """Never salvaged: unusable output fails rather than becoming feedback."""
    with pytest.raises(ProviderResponseError) as caught:
        generate(build(answering_unparseable(output)))
    assert caught.value.error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR


def test_a_refusal_is_a_provider_error():
    with pytest.raises(ProviderResponseError) as caught:
        generate(build(answering(VALID_OUTPUT, stop_reason="refusal")))
    assert caught.value.error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR


def test_output_truncated_by_the_token_ceiling_is_a_provider_error():
    """Structured output cut short is invalid JSON, not partial feedback."""
    with pytest.raises(ProviderResponseError):
        generate(build(answering(VALID_OUTPUT, stop_reason="max_tokens")))


def test_a_response_with_nothing_parsed_is_a_provider_error():
    empty = parse_response(
        response=Message.model_validate(
            {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        output_format=_FeedbackPayload,
    )
    with pytest.raises(ProviderResponseError):
        generate(build(FakeParse(response=empty)))


# --- Failure translation ---------------------------------------------------


def api_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": "nope"}})
    return anthropic.APIStatusError("nope", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (
            anthropic.AuthenticationError(
                "bad key",
                response=httpx.Response(
                    401, request=httpx.Request("POST", "https://api.anthropic.com/")
                ),
                body=None,
            ),
            ProviderNotConfiguredError,
            ErrorCode.ANALYSIS_NOT_CONFIGURED,
        ),
        (
            anthropic.PermissionDeniedError(
                "no access",
                response=httpx.Response(
                    403, request=httpx.Request("POST", "https://api.anthropic.com/")
                ),
                body=None,
            ),
            ProviderNotConfiguredError,
            ErrorCode.ANALYSIS_NOT_CONFIGURED,
        ),
        (
            anthropic.RateLimitError(
                "slow down",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.anthropic.com/")
                ),
                body=None,
            ),
            ProviderRateLimitedError,
            ErrorCode.ANALYSIS_RATE_LIMITED,
        ),
        (
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/")),
            ProviderTimeoutError,
            ErrorCode.ANALYSIS_PROVIDER_TIMEOUT,
        ),
        (
            anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/")
            ),
            ProviderUnavailableError,
            ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE,
        ),
        (api_error(500), ProviderUnavailableError, ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE),
        (api_error(529), ProviderUnavailableError, ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE),
        (api_error(400), ProviderResponseError, ErrorCode.ANALYSIS_PROVIDER_ERROR),
        (api_error(404), ProviderResponseError, ErrorCode.ANALYSIS_PROVIDER_ERROR),
    ],
)
def test_every_sdk_failure_becomes_one_of_ours(
    error: Exception, expected: type[ProviderError], code: ErrorCode
):
    with pytest.raises(expected) as caught:
        generate(build(FakeParse(error=error)))
    assert caught.value.error_code is code


def test_no_anthropic_exception_escapes_the_adapter():
    for error in (api_error(418), api_error(451)):
        with pytest.raises(ProviderError):
            generate(build(FakeParse(error=error)))


def test_a_provider_failure_leaks_nothing_to_the_client():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        401,
        request=request,
        json={"error": {"message": f"invalid x-api-key: {API_KEY}"}},
    )
    error = anthropic.AuthenticationError("auth", response=response, body=None)

    with pytest.raises(ProviderError) as caught:
        generate(build(FakeParse(error=error)))

    failure = caught.value
    visible = f"{failure} {failure.to_api_error().message}"
    for secret in (API_KEY, "x-api-key", "api.anthropic.com", "AuthenticationError"):
        assert secret not in visible
    assert API_KEY not in str(failure.log_context())


def test_a_provider_without_a_key_refuses_to_be_constructed():
    with pytest.raises(ProviderNotConfiguredError) as caught:
        ClaudeFeedbackProvider(api_key="", model="claude-opus-5", timeout_seconds=60)
    assert caught.value.error_code is ErrorCode.ANALYSIS_NOT_CONFIGURED
