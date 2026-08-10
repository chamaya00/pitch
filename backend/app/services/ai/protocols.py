"""The two provider boundaries of the AI layer.

Speech analysis needs two different things from two different vendors, and the
separation is architectural rather than incidental:

* :class:`SpeechToTextProvider` is the only thing in the system that is given
  audio. It returns words and timings.
* :class:`FeedbackProvider` is given *text and numbers only* — a transcript and
  the deterministic metrics computed from it — and returns prose. Its signature
  has no path and no bytes, so it cannot receive audio even by mistake.
* :class:`AudioFeedbackProvider` is the same bargain for the audio half: it is
  given a structured set of measurements and returns prose about them. It too
  has nowhere to put audio.

That second boundary is not a style preference. Claude has no audio input and no
speech-to-text endpoint, so it can only ever be the feedback provider; writing
the protocol this way keeps that fact visible in the code instead of in a
comment somewhere. It also means the interpretation step can never quietly
become a second, unvalidated measurement step: a feedback provider explains
numbers it was handed and has no way to produce new ones.

Both methods are ``async`` because every real implementation is a network call.
Both raise :class:`~app.services.ai.errors.ProviderError` — nothing else — so a
caller has one failure vocabulary to handle.

Implementations receive an already-resolved :class:`~pathlib.Path` produced by
``RecordingStorage``. A provider must never build a path itself: not from a
recording id, not from a client filename, and never from its own response.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from app.services.analysis.models import Feedback, SpeechMetrics, Transcript
from app.services.audio_analysis.models import AudioFeedback, AudioFeedbackRequest


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Turns a stored recording into a transcript."""

    @property
    def name(self) -> str:
        """Short provider label, matching the one it puts in its provenance."""
        ...

    async def transcribe(self, audio_path: Path) -> Transcript:
        """Transcribe the recording at ``audio_path``.

        The returned transcript must carry provenance, and must set
        ``includes_disfluencies`` only if this provider genuinely transcribes
        verbatim — that flag is what allows filler words to be counted.

        Raises:
            ProviderError: for every failure, including
                :class:`~app.services.ai.errors.EmptyTranscriptError` when the
                recording contained no speech.
        """
        ...


@runtime_checkable
class AudioFeedbackProvider(Protocol):
    """Explains measured audio characteristics in plain language.

    A second protocol rather than a second argument on
    :class:`FeedbackProvider`, because the two describe genuinely different
    things: one is handed words and speaking rates, the other pitch and
    amplitude. One method taking both would force every implementation and every
    caller to carry the half it does not use.

    The *implementations* are shared — the same Claude adapter and the same mock
    satisfy both — so there is one client, one error vocabulary and one factory
    behind the two protocols.
    """

    @property
    def name(self) -> str:
        """Short provider label, matching the one it puts in its provenance."""
        ...

    async def interpret_audio(self, request: AudioFeedbackRequest) -> AudioFeedback:
        """Write feedback about a recording's measured audio.

        The same prohibition as :meth:`FeedbackProvider.generate`, and it
        matters more here: an implementation must not state a number that is not
        in ``request``, must not convert an absent measurement into zero, and
        must not turn a spectral measurement into a timbre label. None of those
        are supported by the measurements it was given.

        Raises:
            ProviderError: for every failure.
        """
        ...


@runtime_checkable
class FeedbackProvider(Protocol):
    """Explains measured metrics in plain language."""

    @property
    def name(self) -> str:
        """Short provider label, matching the one it puts in its provenance."""
        ...

    async def generate(self, *, transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
        """Write feedback about a transcript and the metrics measured from it.

        Implementations must not state a number that is not present in
        ``metrics``: a metric left ``None`` was not measurable, and inventing a
        plausible value for it is the one thing this layer must never do.

        Raises:
            ProviderError: for every failure.
        """
        ...
