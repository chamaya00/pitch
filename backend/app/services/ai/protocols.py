"""The two provider boundaries of the AI layer.

Speech analysis needs two different things from two different vendors, and the
separation is architectural rather than incidental:

* :class:`SpeechToTextProvider` is the only thing in the system that is given
  audio. It returns words and timings.
* :class:`FeedbackProvider` is given *text and numbers only* — a transcript and
  the deterministic metrics computed from it — and returns prose. Its signature
  has no path and no bytes, so it cannot receive audio even by mistake.

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
