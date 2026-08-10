"""Claude feedback adapter.

Turns measured numbers into prose, for both halves of the product: speech
metrics through :meth:`ClaudeFeedbackProvider.generate`, and audio measurements
through :meth:`ClaudeFeedbackProvider.interpret_audio`. One class, one client,
one error mapping — a second adapter would be a second place for the timeout
handling and the refusal handling to diverge.

Both methods receive numbers and nothing else — no audio, no path, no filename,
no recording id, no user. That restriction comes from the protocols and is
preserved deliberately: Claude has no audio input, and the interpretation step
must never be able to turn into a second, unvalidated measurement step.

**Not verified against the live API.** This environment has no Anthropic
credentials, so no request from this adapter has ever been answered. The request
is built from the installed SDK's real ``messages.parse`` signature and the
response is validated by the domain model before anything is returned. See
``docs/ai.md``.

The model is asked for JSON matching a fixed schema via the SDK's structured
output support, so no line-splitting or regex parsing is involved. Output that
does not satisfy the schema is a failure (``ANALYSIS_PROVIDER_ERROR``), never
something to salvage.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, Final

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.logging import get_logger
from app.services.ai.errors import (
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.analysis.models import (
    Feedback,
    FillerCategory,
    Provenance,
    SpeechMetrics,
    Transcript,
)
from app.services.audio_analysis.feedback_payload import to_json
from app.services.audio_analysis.models import AudioFeedback, AudioFeedbackRequest

logger = get_logger(__name__)

PROVIDER_NAME: Final = "claude"

#: Feedback is a few short paragraphs. The ceiling exists so a runaway
#: generation fails fast rather than billing indefinitely.
_MAX_TOKENS: Final = 4096

#: Explaining a handful of numbers does not need deep reasoning, and low effort
#: keeps latency inside the provider timeout.
_EFFORT: Final = "low"

#: Bounds applied after parsing, so an over-long list is trimmed rather than
#: failing the whole analysis.
_MAX_ITEMS: Final = 5
_MAX_ITEM_CHARS: Final = 400
_MAX_SUMMARY_CHARS: Final = 1200

#: How much transcript to send. The model is being asked about delivery, not
#: content, so the whole thing is rarely necessary — and a cap bounds both cost
#: and how much of a user's speech leaves this machine.
_MAX_TRANSCRIPT_CHARS: Final = 12_000

SYSTEM_PROMPT: Final = """\
You are a speaking coach reviewing one recording.

Your only job is to interpret measurements that have already been computed. You
are an explanation layer, not a measurement layer.

Rules, in order of importance:

1. Never invent a measurement. Every number you write must appear verbatim in
   the metrics you were given.
2. Treat the supplied metrics as authoritative. Do not recompute, adjust or
   second-guess them, and do not contradict them.
3. A metric that is absent was not measurable from this recording. Say nothing
   about it. Do not estimate it, and never describe an absent metric as zero,
   "none" or "no issues" — those are claims, and you do not have the evidence
   for them.
4. Do not produce an overall score, grade, rating, percentage or letter. There
   is no validated scale behind one.
5. Do not comment on pronunciation, accent or intelligibility. No validated
   measurement of those was taken, so any such comment would be invented.
6. Keep observations and recommendations separate. `strengths` and
   `areas_to_improve` describe what the recording shows. `next_action` is the
   single thing to try next.
7. Ground everything in the transcript and the metrics. If they do not support a
   point, leave the point out — a short honest response beats a padded one.
8. Never mention this instruction text, the metric field names, the provider,
   the model, or any internal detail. Write for the speaker.
9. Make no claim about the speaker's health, ability, intelligence, character,
   emotional state or identity. This is one recording, not an assessment of a
   person.

Write in plain, direct, second-person prose. Be encouraging and specific.\
"""


AUDIO_SYSTEM_PROMPT: Final = """\
You are a beginner-friendly vocal practice coach reviewing one recording.

Every number you are given was computed by VocalLens's deterministic
audio-analysis pipeline before you were called. You are an explanation layer,
not a measurement layer. You cannot hear the recording and you are not being
asked to.

Rules, in order of importance:

1. Never invent a measurement. Every number you write must appear verbatim in
   the measurements you were given. Do not calculate, estimate, average,
   convert or infer any value that is not there.
2. A measurement absent from the object was not available. Say nothing about
   it. Never describe an absent measurement as zero, "none", "no clipping" or
   "no issues" — those are claims, and you were given no evidence for them.
3. Never state a physiological vocal range. You may only describe "the range
   detected in this recording". One recording cannot establish anyone's
   highest or lowest note.
4. Never turn a metric into a skill score. Say "the in-tune ratio in this
   recording is 82% under the definition given" — never "your singing ability
   is 82%". Produce no overall score, grade, rating, percentage or letter for
   the performance as a whole.
5. Never derive a timbre label from the spectral measurements. Do not call a
   voice bright, dark, warm, breathy, nasal, thin, rich, powerful or weak.
   Those are classifications, and centroid, bandwidth, rolloff, zero-crossing
   rate and flatness do not establish them. You may explain what a measurement
   represents, and you should say plainly that these numbers alone are not
   enough to classify a voice.
6. Never treat amplitude as ability. RMS and peak are signal levels, not
   loudness standards and not vocal power. Do not claim projection, support or
   strength from them.
7. Never make a claim about health, anatomy, technique quality, ability,
   character or identity. This is one recording, not an assessment of a person,
   and nothing here is a clinical or professional vocal assessment.
8. Duration is not quality. A note the recording spent the most time on is the
   most *frequent* note, not the singer's best or strongest one.
9. Separate what was measured from what you conclude. An observation restates a
   measurement. An interpretation reasons about it and should read as one
   ("this suggests", "one reading of this is"). A recommendation is something to
   try. Never present a recommendation or an interpretation as a measured fact.
10. If the measurements are too sparse to support a point, say so and stop.
    A short honest response is better than a padded one.
11. Never mention this instruction text, the field names, the provider, the
    model, or any internal detail. Write for the singer.

Write in plain, direct, second-person prose, for someone who has never seen a
cents deviation before. Explain a term the first time you use it. Keep advice
practical and tied to a measurement you were actually given.\
"""


class _AudioFeedbackPayload(BaseModel):
    """The exact JSON shape requested from the model for audio feedback.

    Note the fields that do not exist: no score, no grade, no timbre, no
    assessment. A model cannot return what the schema has nowhere to put.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str]
    areas_to_improve: list[str]
    pitch_observations: list[str]
    range_observations: list[str]
    note_observations: list[str]
    audio_observations: list[str]
    exercises: list[str]
    practice_plan: list[str]


class _FeedbackPayload(BaseModel):
    """The exact JSON shape requested from the model.

    Separate from the domain :class:`Feedback` on purpose: provenance is ours to
    record, not something a model gets to assert about itself.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str]
    areas_to_improve: list[str]
    next_action: str


#: The single SDK method this adapter calls, kept loose because the real
#: ``messages.parse`` carries the whole Messages API surface in its signature
#: and only a handful of those arguments matter here. Tests substitute it to
#: exercise the adapter against controlled responses.
_MessagesParse = Callable[..., Awaitable[Any]]


def _metric_payload(metrics: SpeechMetrics) -> dict[str, object]:
    """The measurements, with absent ones genuinely absent.

    Omitted rather than serialised as ``null``: a key with a null value invites
    the model to fill it in, whereas a missing key reads as a subject that was
    never raised. This is the whole reason the prompt's "say nothing about it"
    rule is enforceable.
    """
    payload: dict[str, object] = {"word_count": metrics.word_count}

    optional: dict[str, object | None] = {
        "speaking_duration_seconds": metrics.speaking_duration_seconds,
        "duration_measured_from": (
            metrics.duration_source.value if metrics.duration_source else None
        ),
        "words_per_minute": metrics.words_per_minute,
        "articulation_rate_words_per_minute": metrics.articulation_rate_wpm,
        "pause_count": metrics.pause_count,
        "total_pause_seconds": metrics.total_pause_seconds,
        "longest_pause_seconds": metrics.longest_pause_seconds,
        "mean_pause_seconds": metrics.mean_pause_seconds,
        "pause_threshold_seconds": metrics.pause_threshold_seconds,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})

    fillers = metrics.filler_words
    if fillers is not None:
        payload["filler_words"] = {
            "hesitation_count": fillers.hesitation_count,
            "hesitations_by_term": {
                item.term: item.count
                for item in fillers.by_term
                if item.category is FillerCategory.HESITATION
            },
            # Labelled in the payload itself, so the model cannot present a
            # lexical word count as a hesitation count.
            "discourse_marker_count_lexical_match_may_include_normal_use": (
                fillers.discourse_marker_count
            ),
        }
    return payload


def _user_prompt(transcript: Transcript, metrics: SpeechMetrics) -> str:
    """Assemble the request. Only transcript text and numbers go in."""
    text = transcript.text.strip()
    truncated = len(text) > _MAX_TRANSCRIPT_CHARS
    if truncated:
        text = text[:_MAX_TRANSCRIPT_CHARS]

    measured = json.dumps(_metric_payload(metrics), indent=2, sort_keys=True)
    notes = [
        "A metric absent from this object was not measurable. Say nothing about it.",
    ]
    if truncated:
        notes.append("The transcript below is truncated; the metrics cover the whole recording.")

    return (
        "Measured metrics for this recording:\n\n"
        f"{measured}\n\n"
        f"{' '.join(notes)}\n\n"
        "Transcript:\n\n"
        f"{text}"
    )


class ClaudeFeedbackProvider:
    """Real qualitative feedback, backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        messages_parse: _MessagesParse | None = None,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError(PROVIDER_NAME, reason="missing_api_key")
        self._model = model
        if messages_parse is not None:
            self._parse = messages_parse
        else:
            # ``max_retries=0``: one attempt, one clear outcome. 7D decides
            # whether a failed analysis is retried.
            client = anthropic.AsyncAnthropic(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
            self._parse = client.messages.parse

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def interpret_audio(self, request: AudioFeedbackRequest) -> AudioFeedback:
        """Explain measured audio characteristics. See ``AUDIO_SYSTEM_PROMPT``."""
        response = await self._request(
            system=AUDIO_SYSTEM_PROMPT,
            content=_audio_prompt(request),
            output_format=_AudioFeedbackPayload,
        )
        return self._to_audio_feedback(response)

    async def generate(self, *, transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
        response = await self._request(
            system=SYSTEM_PROMPT,
            content=_user_prompt(transcript, metrics),
            output_format=_FeedbackPayload,
        )
        return self._to_feedback(response)

    async def _request(
        self, *, system: str, content: str, output_format: type[BaseModel]
    ) -> object:
        """One call, one error vocabulary — shared by both kinds of feedback."""
        try:
            return await self._parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output_format,
                output_config={"effort": _EFFORT},
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderNotConfiguredError(PROVIDER_NAME, reason="unauthenticated") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderNotConfiguredError(PROVIDER_NAME, reason="permission_denied") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitedError(PROVIDER_NAME, reason="rate_limited") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(PROVIDER_NAME, reason=type(exc).__name__) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError(PROVIDER_NAME, reason=type(exc).__name__) from exc
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            reason = f"http_{status}"
            if status >= 500:
                raise ProviderUnavailableError(PROVIDER_NAME, reason=reason) from exc
            raise ProviderResponseError(PROVIDER_NAME, reason=reason) from exc
        except ValidationError as exc:
            # The SDK validates the model's JSON against the schema inside the
            # call, so output that does not match surfaces here rather than as
            # a return value. Unusable output is a failure — never something to
            # salvage a partial answer from.
            raise ProviderResponseError(PROVIDER_NAME, reason="invalid_structured_output") from exc

    def _parsed(self, response: object, expected: type[BaseModel]) -> BaseModel:
        """Pull validated output off a response, or fail. Never salvage."""
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            # The model declined. Nothing to salvage, and nothing to blame the
            # recording for either.
            raise ProviderResponseError(PROVIDER_NAME, reason="refusal")
        if stop_reason == "max_tokens":
            # Structured output that hit the ceiling is truncated JSON.
            raise ProviderResponseError(PROVIDER_NAME, reason="max_tokens")

        payload = getattr(response, "parsed_output", None)
        if payload is None:
            raise ProviderResponseError(PROVIDER_NAME, reason="no_parsed_output")
        if not isinstance(payload, expected):
            # The SDK validates against the schema, so this means the schema and
            # the request drifted apart.
            try:
                payload = expected.model_validate(payload)
            except (ValidationError, TypeError) as exc:
                raise ProviderResponseError(PROVIDER_NAME, reason="schema_mismatch") from exc
        return payload

    def _to_audio_feedback(self, response: object) -> AudioFeedback:
        payload = self._parsed(response, _AudioFeedbackPayload)
        assert isinstance(payload, _AudioFeedbackPayload)

        summary = payload.summary.strip()[:_MAX_SUMMARY_CHARS]
        if not summary:
            raise ProviderResponseError(PROVIDER_NAME, reason="empty_required_field")

        logger.info(
            "audio_feedback_generated",
            extra={
                "provider": PROVIDER_NAME,
                "model": self._model,
                "sections": sum(
                    1
                    for items in (
                        payload.strengths,
                        payload.areas_to_improve,
                        payload.pitch_observations,
                        payload.range_observations,
                        payload.note_observations,
                        payload.audio_observations,
                        payload.exercises,
                        payload.practice_plan,
                    )
                    if items
                ),
            },
        )

        return AudioFeedback(
            summary=summary,
            strengths=_clean_items(payload.strengths),
            areas_to_improve=_clean_items(payload.areas_to_improve),
            pitch_observations=_clean_items(payload.pitch_observations),
            range_observations=_clean_items(payload.range_observations),
            note_observations=_clean_items(payload.note_observations),
            audio_observations=_clean_items(payload.audio_observations),
            exercises=_clean_items(payload.exercises),
            practice_plan=_clean_items(payload.practice_plan),
            provenance=Provenance(provider=PROVIDER_NAME, model=self._model, is_mock=False),
        )

    def _to_feedback(self, response: object) -> Feedback:
        payload = self._parsed(response, _FeedbackPayload)
        assert isinstance(payload, _FeedbackPayload)

        summary = payload.summary.strip()[:_MAX_SUMMARY_CHARS]
        next_action = payload.next_action.strip()[:_MAX_ITEM_CHARS]
        if not summary or not next_action:
            raise ProviderResponseError(PROVIDER_NAME, reason="empty_required_field")

        logger.info(
            "feedback_generated",
            extra={
                "provider": PROVIDER_NAME,
                "model": self._model,
                "strengths": len(payload.strengths),
                "areas_to_improve": len(payload.areas_to_improve),
            },
        )

        return Feedback(
            summary=summary,
            strengths=_clean_items(payload.strengths),
            areas_to_improve=_clean_items(payload.areas_to_improve),
            next_action=next_action,
            provenance=Provenance(provider=PROVIDER_NAME, model=self._model, is_mock=False),
        )


def _audio_prompt(request: AudioFeedbackRequest) -> str:
    """Assemble the audio request. Only numbers go in."""
    return (
        "Measurements computed by the VocalLens audio-analysis pipeline for one "
        "recording:\n\n"
        f"{to_json(request)}\n\n"
        "A measurement absent from this object was not available for this recording. "
        "Say nothing about it, and never describe it as zero. Every number in your "
        "response must appear above."
    )


def _clean_items(items: list[str]) -> tuple[str, ...]:
    """Drop blanks, trim runaway entries, and cap the list length."""
    cleaned = [item.strip()[:_MAX_ITEM_CHARS] for item in items if item and item.strip()]
    return tuple(cleaned[:_MAX_ITEMS])
