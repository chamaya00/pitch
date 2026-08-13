"""The speech-analysis domain model.

These are the application's own types, independent of any provider's response
shape and of how an analysis is stored or serialised. A provider adapter's job
is to translate *into* these types; nothing here imports a vendor SDK.

Two rules shape almost every decision below.

**Missing data is missing.** Every measurement that the source data may not
support is ``X | None`` and is left ``None`` when it cannot be computed. There
is no "default" speaking rate, no ``0`` standing in for "we don't know", and no
placeholder confidence. A caller can therefore trust that a number it reads was
actually derived from the recording's transcript.

**Generated content carries its origin.** :class:`Provenance` is a required
field of everything a provider produced, so a transcript or a piece of feedback
cannot exist in this system without recording which provider made it and
whether that provider was a mock. Presentation layers key their "demo data"
treatment off ``is_mock``.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.errors import ErrorCode
from app.services.recordings.models import utc_now

#: Identifiers are ``uuid4().hex`` — the same shape ``RecordingStorage`` uses.
#: Constraining them here means an id that reached us from a URL, a provider
#: response or a stored document can never be interpolated into a filesystem
#: path, because a value that is not 32 hex characters cannot become an
#: ``Analysis`` in the first place.
#:
#: The end anchor is ``\z``, not Python's ``\Z``: pydantic compiles field
#: patterns with Rust's regex engine, which spells it the other way and rejects
#: ``\Z`` outright. Patterns elsewhere in this repository go through ``re`` and
#: are written the Python way.
_ID_PATTERN: Final = r"\A[0-9a-f]{32}\z"

#: Provider and model names are labels, not free text. The bounded character
#: sets keep credentials and header values out of stored records: a bearer
#: token or an ``Authorization`` header cannot satisfy either pattern.
_PROVIDER_PATTERN: Final = r"\A[a-z0-9][a-z0-9._-]{0,63}\z"
_MODEL_PATTERN: Final = r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z"


def new_analysis_id() -> str:
    """Return a fresh, server-generated analysis identifier."""
    return uuid.uuid4().hex


class Provenance(BaseModel):
    """Which provider produced a piece of generated content.

    ``extra="forbid"`` is deliberate. Provenance is written into stored analysis
    records, so it is exactly the place where an adapter might be tempted to
    stash "just the request id" — or, worse, the API key it used. Only the three
    fields below are storable, and the value patterns are narrow enough that a
    credential cannot be smuggled through one of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Short provider label, e.g. ``"mock"`` or ``"deepgram"``. Never a URL.
    provider: str = Field(pattern=_PROVIDER_PATTERN)
    #: Provider-side model identifier, when the provider names one.
    model: str | None = Field(default=None, pattern=_MODEL_PATTERN)
    #: Required, with no default: a provider must state what it is. Content
    #: produced by a mock must never be presentable as genuine analysis.
    is_mock: bool


class AnalysisProvenance(BaseModel):
    """Provenance for a whole analysis, aggregated from its two providers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcription: Provenance | None = None
    feedback: Provenance | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_mock(self) -> bool:
        """``True`` if any content in this analysis came from a mock provider."""
        recorded = [p for p in (self.transcription, self.feedback) if p is not None]
        return any(p.is_mock for p in recorded)


class TranscriptWord(BaseModel):
    """One token of a transcript, with timings when the provider supplied them.

    ``start_seconds`` and ``end_seconds`` are both present or both absent.
    "Absent" means the provider returned no timing for this word — it does not
    mean the word began at zero, and the validator below refuses the halfway
    state that would make that ambiguous.

    ``text`` keeps the provider's punctuation so the transcript can be
    reconstructed; the metrics module normalises tokens for counting.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_timings(self) -> Self:
        start, end = self.start_seconds, self.end_seconds
        if (start is None) != (end is None):
            raise ValueError("word timings must supply both start and end, or neither")
        if start is not None and end is not None and end < start:
            raise ValueError("word timing ends before it starts")
        return self

    @property
    def is_timed(self) -> bool:
        return self.start_seconds is not None and self.end_seconds is not None


class Transcript(BaseModel):
    """What a speech-to-text provider returned, normalised into our own shape."""

    model_config = ConfigDict(frozen=True)

    #: The full transcript as the provider rendered it.
    text: str
    #: Per-word tokens. Empty when the provider returned text only — which is a
    #: real possibility, and the reason every timing-derived metric is optional.
    words: tuple[TranscriptWord, ...] = ()
    #: BCP-47-ish language tag, when the provider reports one.
    language: str | None = None
    #: Duration the provider observed. Kept separate from the container
    #: duration read at upload time, since the two can legitimately differ.
    audio_duration_seconds: float | None = Field(default=None, gt=0)
    #: Whether this provider transcribes verbatim, keeping "um" and "uh".
    #: Defaults to ``False`` because most providers clean disfluencies out by
    #: default, and a filler count taken from a cleaned transcript would read as
    #: "no fillers" when the truth is "not measurable". Metrics omit filler
    #: statistics entirely unless a provider sets this.
    includes_disfluencies: bool = False
    provenance: Provenance

    @property
    def has_word_timings(self) -> bool:
        """``True`` only if every word carries a timing — partial data is not usable."""
        return len(self.words) > 0 and all(word.is_timed for word in self.words)

    @property
    def is_empty(self) -> bool:
        """``True`` when the provider found no speech."""
        return not self.text.strip() and not self.words


class Pause(BaseModel):
    """A silent gap detected between two transcribed words."""

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.end_seconds < self.start_seconds:
            raise ValueError("pause ends before it starts")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class FillerCategory(StrEnum):
    """How much a filler match can be trusted.

    The distinction is not cosmetic. A hesitation sound has no other meaning, so
    counting it is a measurement. A discourse marker is an ordinary English word
    that is *sometimes* a filler, so counting it lexically is a word-frequency
    count and must be labelled as one rather than presented as "you said 'like'
    twelve times as a filler".
    """

    HESITATION = "hesitation"
    DISCOURSE_MARKER = "discourse_marker"


class FillerTermCount(BaseModel):
    """How often one filler term appeared."""

    model_config = ConfigDict(frozen=True)

    term: str = Field(min_length=1)
    category: FillerCategory
    count: int = Field(ge=1)


class FillerWordStats(BaseModel):
    """Filler counts, kept in two separately reportable categories.

    This object exists only when the transcript is verbatim. When it is absent
    from :class:`SpeechMetrics` the correct reading is "not measurable with this
    provider", never "none were said".
    """

    model_config = ConfigDict(frozen=True)

    #: Unambiguous hesitation sounds: "um", "uh", "er".
    hesitation_count: int = Field(ge=0)
    #: Lexical matches on context-dependent words: "like", "you know".
    #: Includes legitimate, non-filler uses; label it as such wherever shown.
    discourse_marker_count: int = Field(ge=0)
    by_term: tuple[FillerTermCount, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_count(self) -> int:
        return self.hesitation_count + self.discourse_marker_count


class DurationSource(StrEnum):
    """Which clock a duration-derived metric was computed against.

    Word timings measure the span from the first word to the last, so they
    exclude silence at either end of the file. A recording's own duration
    includes it. The two produce different speaking rates from the same words,
    so which one was used is part of the result.
    """

    WORD_TIMINGS = "word_timings"
    RECORDING_DURATION = "recording_duration"


class SpeechMetrics(BaseModel):
    """Deterministic measurements taken from a transcript.

    Only ``word_count`` is always available. Everything else is ``None`` unless
    the transcript actually supported computing it — see ``metrics.py``, which
    is the only thing that should construct this.
    """

    model_config = ConfigDict(frozen=True)

    word_count: int = Field(ge=0)

    duration_source: DurationSource | None = None
    #: The span the rate metrics were computed over, per ``duration_source``.
    speaking_duration_seconds: float | None = Field(default=None, gt=0)
    #: Words per minute across the whole span, pauses included.
    words_per_minute: float | None = Field(default=None, ge=0)
    #: Words per minute with detected pauses removed. Needs word timings.
    articulation_rate_wpm: float | None = Field(default=None, ge=0)

    #: Pause fields are all-or-nothing: without word timings there is nothing to
    #: detect, so they stay ``None`` rather than reporting zero pauses.
    pause_threshold_seconds: float | None = Field(default=None, gt=0)
    pause_count: int | None = Field(default=None, ge=0)
    total_pause_seconds: float | None = Field(default=None, ge=0)
    #: ``None`` when no pause was found — the mean and maximum of an empty set
    #: are undefined, unlike the count, which is a genuine zero.
    longest_pause_seconds: float | None = Field(default=None, ge=0)
    mean_pause_seconds: float | None = Field(default=None, ge=0)

    filler_words: FillerWordStats | None = None


class Feedback(BaseModel):
    """Qualitative interpretation of the metrics above.

    A feedback provider receives the transcript and the metrics — never audio,
    and never a filesystem path. It explains numbers it was given; it does not
    produce new ones.
    """

    model_config = ConfigDict(frozen=True)

    summary: str = Field(min_length=1)
    strengths: tuple[str, ...] = ()
    areas_to_improve: tuple[str, ...] = ()
    next_action: str = Field(min_length=1)
    provenance: Provenance


class AnalysisStatus(StrEnum):
    """Lifecycle of one analysis run."""

    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


#: States from which an analysis will not move again.
TERMINAL_STATUSES: Final[frozenset[AnalysisStatus]] = frozenset(
    {AnalysisStatus.COMPLETED, AnalysisStatus.FAILED}
)


class Analysis(BaseModel):
    """One analysis of one recording, at whatever stage it has reached.

    The validator encodes the invariants that make the record trustworthy to
    read: a failure always says why, a success never carries a stale error code,
    and "completed" always has the deterministic results attached.

    ``feedback`` is intentionally *not* required for completion. The measured
    numbers are produced without a language model, so an outage at the feedback
    provider degrades an analysis to "numbers without prose" rather than losing
    the analysis. The reverse is not true: prose is never shown without the
    numbers it describes.
    """

    model_config = ConfigDict(frozen=True)

    analysis_id: str = Field(pattern=_ID_PATTERN)
    recording_id: str = Field(pattern=_ID_PATTERN)
    status: AnalysisStatus = AnalysisStatus.PENDING

    created_at: datetime = Field(default_factory=utc_now)
    #: When work actually began — distinct from ``created_at``, since an
    #: analysis is queued before a worker picks it up.
    started_at: datetime | None = None
    completed_at: datetime | None = None

    #: Set only on failure, and always drawn from the shared error vocabulary so
    #: the API layer never has to invent one.
    error_code: ErrorCode | None = None

    transcript: Transcript | None = None
    metrics: SpeechMetrics | None = None
    feedback: Feedback | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.status is AnalysisStatus.FAILED:
            if self.error_code is None:
                raise ValueError("a failed analysis must record an error_code")
        elif self.error_code is not None:
            raise ValueError("error_code is only valid on a failed analysis")

        if self.status is AnalysisStatus.COMPLETED:
            if self.transcript is None:
                raise ValueError("a completed analysis must have a transcript")
            if self.metrics is None:
                raise ValueError("a completed analysis must have metrics")

        if self.feedback is not None and self.metrics is None:
            raise ValueError("feedback cannot be attached without the metrics it describes")

        if self.completed_at is not None and self.status not in TERMINAL_STATUSES:
            raise ValueError("completed_at is only valid once the analysis has finished")

        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provenance(self) -> AnalysisProvenance:
        """Origins of everything this analysis contains.

        Derived rather than stored, so it cannot drift away from the content it
        describes: if a transcript is present, its provenance is present.
        """
        return AnalysisProvenance(
            transcription=self.transcript.provenance if self.transcript else None,
            feedback=self.feedback.provenance if self.feedback else None,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
