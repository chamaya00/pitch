"""Deepgram speech-to-text adapter.

Translates Deepgram's pre-recorded transcription response into this
application's :class:`~app.services.analysis.models.Transcript`. Nothing above
this module knows that Deepgram exists: no Deepgram type, exception, option name
or response field crosses the boundary, and every failure leaves here as a
:class:`~app.services.ai.errors.ProviderError`.

**Not verified against the live API.** This environment has no Deepgram
credentials and its network policy blocks the Deepgram host outright, so no
request from this adapter has ever reached the service. What *is* verified is
everything on this side of the wire: the request is built from the installed
SDK's real signature, and the response is parsed through the SDK's own response
models. See ``docs/speech-analysis.md``.

Audio is streamed to the SDK in chunks rather than read into memory, so a
50 MB upload costs a buffer, not 50 MB of resident bytes.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Final

import httpx
from deepgram import AsyncDeepgramClient
from deepgram.core.api_error import ApiError
from deepgram.core.parse_error import ParsingError
from deepgram.core.request_options import RequestOptions
from deepgram.types.listen_v1response import ListenV1Response

from app.core.logging import get_logger
from app.services.ai.errors import (
    EmptyTranscriptError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.analysis.models import Provenance, Transcript, TranscriptWord

logger = get_logger(__name__)

PROVIDER_NAME: Final = "deepgram"

#: Read size when streaming the recording to the provider.
_UPLOAD_CHUNK_BYTES: Final = 1024 * 1024

#: Deepgram models known to accept ``filler_words``. The check exists so that a
#: request routed to some other model does not silently produce a transcript we
#: would then mark as verbatim — being wrong here would mean reporting "no
#: fillers" for a recording full of them. It is a second guard behind the
#: ``deepgram_filler_words`` setting, not a substitute for it: this list has not
#: been confirmed against a live response from this environment.
_VERBATIM_CAPABLE_MODELS: Final[frozenset[str]] = frozenset({"nova-2", "nova-3"})

#: HTTP statuses that mean "the request was fine, the service was not".
_UNAVAILABLE_STATUSES: Final[frozenset[int]] = frozenset({502, 503, 504})


#: The single SDK method this adapter calls, kept loose because the real
#: ``transcribe_file`` carries every transcription option in its signature and
#: only a handful of them matter here. Tests substitute it to exercise the
#: adapter against controlled responses.
_TranscribeFile = Callable[..., Awaitable[Any]]


async def _stream_file(path: Path) -> AsyncIterator[bytes]:
    """Yield the recording in chunks. Nothing is logged about its contents."""
    with path.open("rb") as handle:
        while chunk := handle.read(_UPLOAD_CHUNK_BYTES):
            yield chunk


def _model_names(metadata: Any) -> frozenset[str]:
    """Names of the models Deepgram reports having run.

    ``model_info`` is a loosely-typed dictionary in the SDK, so this reads it
    defensively: an unreadable shape yields an empty set, which downgrades the
    transcript to non-verbatim rather than assuming the best.
    """
    info = getattr(metadata, "model_info", None)
    if not isinstance(info, dict):
        return frozenset()
    names = {
        entry["name"]
        for entry in info.values()
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    return frozenset(names)


class DeepgramSpeechToTextProvider:
    """Real speech-to-text, backed by Deepgram's pre-recorded transcription API.

    Word timings are requested unconditionally — they are what the pause,
    articulation-rate and speaking-span metrics are computed from, and without
    them most of the analysis is omitted.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        request_filler_words: bool = False,
        transcribe_file: _TranscribeFile | None = None,
    ) -> None:
        if not api_key:
            # Defensive: the factory checks this too. Neither the key nor its
            # length is recorded anywhere.
            raise ProviderNotConfiguredError(PROVIDER_NAME, reason="missing_api_key")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._request_filler_words = request_filler_words
        if transcribe_file is not None:
            self._transcribe_file = transcribe_file
        else:
            # ``max_retries=0``: one attempt, one clear outcome. Whether a
            # failed analysis is retried is Phase 7D's decision.
            client = AsyncDeepgramClient(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
            self._transcribe_file = client.listen.v1.media.transcribe_file

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def transcribe(self, audio_path: Path) -> Transcript:
        try:
            response = await self._transcribe_file(
                request=_stream_file(audio_path),
                model=self._model,
                # Punctuation and casing make the transcript readable; neither
                # affects the metrics, which normalise tokens themselves.
                punctuate=True,
                smart_format=True,
                # The metrics depend on these two.
                utterances=True,
                paragraphs=False,
                filler_words=self._request_filler_words,
                request_options=RequestOptions(
                    timeout_in_seconds=int(self._timeout_seconds),
                    max_retries=0,
                ),
            )
        except OSError as exc:
            raise ProviderResponseError(PROVIDER_NAME, reason=type(exc).__name__) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(PROVIDER_NAME, reason=type(exc).__name__) from exc
        except httpx.TransportError as exc:
            # Connection refused, DNS failure, TLS failure: reached nothing.
            raise ProviderUnavailableError(PROVIDER_NAME, reason=type(exc).__name__) from exc
        except ParsingError as exc:
            raise ProviderResponseError(PROVIDER_NAME, reason="unparseable_response") from exc
        except ApiError as exc:
            raise self._error_for_status(exc) from exc

        return self._to_transcript(response)

    @staticmethod
    def _error_for_status(exc: ApiError) -> ProviderError:
        """Map an HTTP status onto our own vocabulary.

        The provider's response body is never read into the error: it can
        contain the request, and on some services the transcript itself.
        """
        status = exc.status_code
        reason = f"http_{status}" if status is not None else "http_error"
        if status in (401, 403):
            # Credentials exist but are wrong or unauthorised. That is a
            # deployment problem, the same class as having none at all.
            return ProviderNotConfiguredError(PROVIDER_NAME, reason=reason)
        if status == 429:
            return ProviderRateLimitedError(PROVIDER_NAME, reason=reason)
        if status == 408:
            return ProviderTimeoutError(PROVIDER_NAME, reason=reason)
        if status is not None and status in _UNAVAILABLE_STATUSES:
            return ProviderUnavailableError(PROVIDER_NAME, reason=reason)
        return ProviderResponseError(PROVIDER_NAME, reason=reason)

    def _to_transcript(self, response: object) -> Transcript:
        if not isinstance(response, ListenV1Response):
            # ``transcribe_file`` also returns an "accepted" envelope when a
            # callback URL is supplied. We never supply one, so this is a
            # contract violation rather than a mode we support.
            raise ProviderResponseError(PROVIDER_NAME, reason="unexpected_response_type")

        channels = response.results.channels
        alternatives = channels[0].alternatives if channels else None
        best = alternatives[0] if alternatives else None
        if best is None:
            raise ProviderResponseError(PROVIDER_NAME, reason="no_alternatives")

        text = (best.transcript or "").strip()
        words = self._to_words(best.words)
        if not text and not words:
            # A successful response containing no speech. Not a fault.
            raise EmptyTranscriptError(PROVIDER_NAME)

        verbatim = self._is_verbatim(response)
        duration = response.metadata.duration

        logger.info(
            "transcription_completed",
            extra={
                "provider": PROVIDER_NAME,
                "model": self._model,
                "word_count": len(words),
                "audio_duration_seconds": duration,
                "includes_disfluencies": verbatim,
            },
        )

        return Transcript(
            text=text,
            words=words,
            language=channels[0].detected_language if channels else None,
            audio_duration_seconds=duration if duration > 0 else None,
            includes_disfluencies=verbatim,
            provenance=Provenance(provider=PROVIDER_NAME, model=self._model, is_mock=False),
        )

    @staticmethod
    def _to_words(raw_words: object) -> tuple[TranscriptWord, ...]:
        """Convert Deepgram's word list, dropping anything unusable.

        Every field on a Deepgram word is optional in the SDK's model. A word
        with no text is not a word; a word with a partial or reversed timing is
        not usable, and is kept untimed rather than guessed at — the metrics
        then omit every timing-derived measurement, which is the correct
        outcome for data we cannot trust.
        """
        if not isinstance(raw_words, list):
            return ()

        words: list[TranscriptWord] = []
        for raw in raw_words:
            text = getattr(raw, "word", None)
            if not isinstance(text, str) or not text.strip():
                continue
            start = getattr(raw, "start", None)
            end = getattr(raw, "end", None)
            start_seconds: float | None = None
            end_seconds: float | None = None
            if (
                isinstance(start, int | float)
                and isinstance(end, int | float)
                and start >= 0
                and end >= start
            ):
                start_seconds, end_seconds = float(start), float(end)

            words.append(
                TranscriptWord(
                    text=text.strip(),
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
        return tuple(words)

    def _is_verbatim(self, response: ListenV1Response) -> bool:
        """Whether this transcript can be trusted to contain disfluencies.

        Two conditions, both required: we asked for them, and the model that
        actually ran is one known to honour the request. Deepgram does not echo
        the option back, so this is the strongest check the response supports.
        """
        if not self._request_filler_words:
            return False

        names = _model_names(response.metadata)
        if names and names <= _VERBATIM_CAPABLE_MODELS:
            return True

        logger.warning(
            "disfluencies_requested_but_unconfirmed",
            extra={
                "provider": PROVIDER_NAME,
                "requested_model": self._model,
                "reported_models": sorted(names),
            },
        )
        return False
