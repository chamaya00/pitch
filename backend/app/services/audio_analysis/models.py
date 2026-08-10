"""The audio-analysis domain model.

Deliberately a sibling of ``services/analysis/models.py`` rather than an
extension of it. That module measures **speech** from a transcript — words,
pace, pauses. This one measures **audio** from the signal — pitch, notes,
range, level, spectrum. They describe the same recording and answer different
questions, so they are stored, versioned and presented separately. There is no
combined object and no combined score; see ``docs/architecture.md``.

The same two rules apply as everywhere else in this codebase:

**Missing data is missing.** Every measurement the signal may not support is
``X | None``. A recording with no reliable pitch has ``pitch=None``, not a range
of zero semitones. Callers may rely on a number they read having been measured.

**Nothing here is a verdict.** No skill score, no grade, no timbre label. The
fields are measurements with documented definitions, and the units are in the
names.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.errors import ErrorCode
from app.services.recordings.models import utc_now

#: ``uuid4().hex``, the same shape every other identifier in this system uses.
#: Constraining it in the model means a value that cannot be an id cannot become
#: a record, and therefore cannot reach a filesystem path.
_ID_PATTERN: Final = r"\A[0-9a-f]{32}\z"

#: Scientific pitch notation: a letter, an optional sharp, an octave. Bounded so
#: a note name can never carry arbitrary text into a stored document.
_NOTE_PATTERN: Final = r"\A[A-G]#?-?[0-9]\z"

#: A voiced frame within this many cents of its semitone counts as in tune.
#:
#: Lives here, in the domain, rather than beside the detector: the recording-level
#: ``in_tune_ratio`` and the per-note one must mean the same thing, and the note
#: aggregation must be able to reach it without importing numpy and a decoder.
#: Roughly where a trained listener stops hearing a note as bent.
IN_TUNE_CENTS: Final = 25.0


def new_audio_analysis_id() -> str:
    """Return a fresh, server-generated audio-analysis identifier."""
    return uuid.uuid4().hex


class AnalysisSettings(BaseModel):
    """The parameters a result was produced with.

    Stored on every record rather than assumed, so a stored analysis stays
    interpretable after a threshold changes: a range measured with a 0.80
    clarity threshold is not the same measurement as one taken at 0.90, and
    without this the two would be indistinguishable on disk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_rate_hz: int = Field(gt=0, description="Rate the analysis ran at, in Hz.")
    frame_length_samples: int = Field(gt=0)
    hop_length_samples: int = Field(gt=0)
    min_frequency_hz: float = Field(gt=0)
    max_frequency_hz: float = Field(gt=0)
    clarity_threshold: float = Field(ge=0, le=1)
    silence_rms: float = Field(ge=0)


class PitchPoint(BaseModel):
    """One voiced frame.

    **Only voiced frames exist.** Unvoiced frames — silence, noise, consonants,
    anything below the clarity threshold — are omitted from the timeline rather
    than stored with null fields. Two reasons: a point that exists is a point
    that was measured, so no consumer has to filter before plotting; and
    ``voiced_ratio`` on the stability block already states exactly how much of
    the recording was left out. A gap in the timeline is therefore meaningful
    and should be drawn as a gap, never interpolated across.
    """

    model_config = ConfigDict(frozen=True)

    timestamp_seconds: float = Field(ge=0, description="Frame centre, in seconds.")
    frequency_hz: float = Field(gt=0)
    #: Nearest equal-tempered semitone. The fractional part lives in ``cents``.
    midi_note: int = Field(ge=0, le=127)
    note_name: str = Field(pattern=_NOTE_PATTERN)
    #: Signed distance to ``midi_note``. Within ±50 by construction.
    cents: float = Field(ge=-50, le=50)
    #: Measured NSDF peak height, 0–1. Not an invented score.
    confidence: float = Field(ge=0, le=1)


class NoteSummary(BaseModel):
    """How much of the pitched time was spent on one musical note.

    One entry per semitone, not per frequency: every frame whose nearest note is
    this one contributes, and how far those frames sat from it is reported by
    ``average_cents`` and ``mean_abs_cents`` rather than by splitting them into
    separate entries.

    ``percentage_of_voiced_time`` is a share of the **pitched** time, not of the
    recording. A recording that is half silence would otherwise report every
    note at half its real share.
    """

    model_config = ConfigDict(frozen=True)

    midi_note: int = Field(ge=0, le=127)
    note_name: str = Field(pattern=_NOTE_PATTERN)
    #: Frames on this note times the hop. See ``notes.py`` for why the hop.
    duration_seconds: float = Field(ge=0)
    percentage_of_voiced_time: float = Field(ge=0, le=100)
    frame_count: int = Field(ge=1)
    #: Signed mean deviation from this note. Negative reads flat.
    average_cents: float = Field(ge=-50, le=50)
    #: Mean distance from the note regardless of direction.
    mean_abs_cents: float = Field(ge=0, le=50)
    #: Share of this note's frames within :data:`IN_TUNE_CENTS` of it.
    in_tune_ratio: float = Field(ge=0, le=1)


class VocalRange(BaseModel):
    """The pitch range **detected in this recording**.

    Not a physiological range, not a maximum, and not a voice type. It is
    bounded by what was actually performed, by the microphone, by the room, and
    by the detector's own search limits. Every presentation of it says so.
    """

    model_config = ConfigDict(frozen=True)

    lowest_frequency_hz: float = Field(gt=0)
    highest_frequency_hz: float = Field(gt=0)
    lowest_note: str = Field(pattern=_NOTE_PATTERN)
    highest_note: str = Field(pattern=_NOTE_PATTERN)
    #: Whole semitones between the extremes, rounded to nearest.
    semitone_span: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.highest_frequency_hz < self.lowest_frequency_hz:
            raise ValueError("highest frequency is below the lowest")
        return self


class UnstableSection(BaseModel):
    """A stretch where the pitch moved around more than the threshold allows.

    Descriptive, not a fault. Vibrato, a slide, a bend and a laugh all land
    here, and so does an octave error in the detector. Presented as "where the
    pitch moved", never as "where you went wrong".
    """

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    #: Standard deviation of the cents deviation across the section.
    cents_std: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.end_seconds < self.start_seconds:
            raise ValueError("section ends before it starts")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class PitchStability(BaseModel):
    """How steady the detected pitch was.

    Explicitly **not** a singing-ability score. Every field is a defined
    statistic over the voiced frames, and intentional pitch content — vibrato,
    portamento, blues intonation, any non-equal-tempered system — moves these
    numbers without anything being wrong.
    """

    model_config = ConfigDict(frozen=True)

    #: Voiced frames over total analysed frames, 0–1. Always available.
    voiced_ratio: float = Field(ge=0, le=1)
    total_frames: int = Field(ge=0)
    voiced_frames: int = Field(ge=0)

    #: Signed mean deviation from the nearest semitone. Negative reads flat.
    #: ``None`` when nothing was voiced.
    mean_cents_deviation: float | None = None
    #: Mean *absolute* deviation: how far from a note, regardless of direction.
    mean_abs_cents_deviation: float | None = Field(default=None, ge=0)
    #: Standard deviation of the signed deviation across voiced frames.
    cents_std: float | None = Field(default=None, ge=0)
    #: Variance of the fractional MIDI value, in semitones². This is how much
    #: the *pitch itself* moved, as distinct from how far it sat from a note.
    semitone_variance: float | None = Field(default=None, ge=0)
    #: Share of voiced frames within :data:`IN_TUNE_CENTS` of a semitone.
    #: Reported with its definition attached; it is not a percentage score.
    in_tune_ratio: float | None = Field(default=None, ge=0, le=1)

    unstable_sections: tuple[UnstableSection, ...] = ()

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.voiced_frames > self.total_frames:
            raise ValueError("more voiced frames than frames")
        return self


class Loudness(BaseModel):
    """Amplitude measurements. **Not LUFS.**

    No loudness weighting, no gating, no reference level. Nothing derived from
    these may be described as broadcast- or mastering-grade loudness.
    """

    model_config = ConfigDict(frozen=True)

    #: Root-mean-square amplitude across the whole recording, 0–1.
    rms: float = Field(ge=0, le=1)
    #: Largest absolute sample, 0–1. At or above 1.0 the file is clipping.
    peak: float = Field(ge=0)
    #: 95th minus 5th percentile of per-frame RMS, in dB. An estimate; see
    #: ``features.dynamic_range_db``. ``None`` with too little audible signal.
    dynamic_range_db: float | None = Field(default=None, ge=0)
    #: Peak over RMS, in dB. High means peaky, low means dense or compressed.
    crest_factor_db: float | None = Field(default=None, ge=0)
    #: Share of samples at or beyond full scale. A measurement of clipping, not
    #: a judgement about it.
    clipped_sample_ratio: float = Field(ge=0, le=1)


class SpectralFeatures(BaseModel):
    """Averaged spectral shape over the audible frames.

    Raw measurable characteristics. **No timbre label is derived from these**
    anywhere in the system: "bright", "dark", "breathy", "nasal" are
    classifications, and no validated classifier exists here.
    """

    model_config = ConfigDict(frozen=True)

    centroid_hz: float = Field(ge=0)
    bandwidth_hz: float = Field(ge=0)
    rolloff_hz: float = Field(ge=0)
    zero_crossing_rate: float = Field(ge=0, le=1)
    flatness: float = Field(ge=0, le=1)


class AudioMetrics(BaseModel):
    """Everything measured from the signal, in one place.

    ``stability`` is always present — a recording always has a voiced ratio,
    even when it is zero. ``pitch`` is ``None`` when no frame cleared the
    clarity threshold, which is a normal outcome for speech at a whisper, a
    noisy room, or an instrumental recording.
    """

    model_config = ConfigDict(frozen=True)

    settings: AnalysisSettings
    duration_seconds: float = Field(gt=0)

    pitch: VocalRange | None = None
    stability: PitchStability
    loudness: Loudness
    spectral: SpectralFeatures | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_reliable_pitch(self) -> bool:
        """Whether any pitch measurement is available at all."""
        return self.pitch is not None


class AudioAnalysisStatus(StrEnum):
    """Lifecycle of one audio-analysis run.

    Shorter than the speech pipeline's: there is no provider to wait on, so
    there is no ``transcribing``. ``analyzing`` covers decoding and measuring.
    """

    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


#: States from which an audio analysis will not move again.
TERMINAL_STATUSES: Final[frozenset[AudioAnalysisStatus]] = frozenset(
    {AudioAnalysisStatus.COMPLETED, AudioAnalysisStatus.FAILED}
)


class AudioAnalysis(BaseModel):
    """One deterministic audio analysis of one recording.

    The validator encodes what makes a stored record trustworthy: a failure
    always says why, a success never carries a stale error code, and a completed
    record always has its metrics attached.

    ``pitch_points`` is the timeline. It can be long — tens of thousands of
    entries for a long recording — so the API exposes it on its own path rather
    than inside the summary response.
    """

    model_config = ConfigDict(frozen=True)

    audio_analysis_id: str = Field(pattern=_ID_PATTERN)
    recording_id: str = Field(pattern=_ID_PATTERN)
    status: AudioAnalysisStatus = AudioAnalysisStatus.PENDING

    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    error_code: ErrorCode | None = None

    metrics: AudioMetrics | None = None
    pitch_points: tuple[PitchPoint, ...] = ()

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.status is AudioAnalysisStatus.FAILED:
            if self.error_code is None:
                raise ValueError("a failed audio analysis must record an error_code")
        elif self.error_code is not None:
            raise ValueError("error_code is only valid on a failed audio analysis")

        if self.status is AudioAnalysisStatus.COMPLETED and self.metrics is None:
            raise ValueError("a completed audio analysis must have metrics")

        if self.pitch_points and self.metrics is None:
            raise ValueError("pitch points cannot exist without the metrics describing them")

        if self.completed_at is not None and self.status not in TERMINAL_STATUSES:
            raise ValueError("completed_at is only valid once the analysis has finished")

        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
