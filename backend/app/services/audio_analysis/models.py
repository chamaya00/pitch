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
from typing import Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.errors import ErrorCode

# Provenance is shared with the speech analysis rather than redefined: "which
# provider produced this, and was it a mock" means the same thing whichever
# half of the product asked, and two copies would be two places for the
# is_mock guarantee to drift.
from app.services.analysis.models import Provenance
from app.services.recordings.models import utc_now

#: ``uuid4().hex``, the same shape every other identifier in this system uses.
#: Constraining it in the model means a value that cannot be an id cannot become
#: a record, and therefore cannot reach a filesystem path.
_ID_PATTERN: Final = r"\A[0-9a-f]{32}\z"

#: Scientific pitch notation: a letter, an optional sharp, an octave. Bounded so
#: a note name can never carry arbitrary text into a stored document.
_NOTE_PATTERN: Final = r"\A[A-G]#?-?[0-9]\z"

#: A pitch class: the same letter without the octave. ``A4``, ``A3`` and ``A5``
#: are three pitches and one pitch class, and the distinction is load-bearing —
#: see ``docs/phase-8-specification.md``.
_PITCH_CLASS_PATTERN: Final = r"\A[A-G]#?\z"

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


class KeyMode(StrEnum):
    """The two modes this project estimates. There are deliberately no others.

    Dorian, Mixolydian and the rest exist; a profile set containing only major
    and minor cannot recognise them, and would report the nearest of the two
    rather than admitting it does not know. That is a documented limitation
    rather than a gap to fill by adding profiles nobody here can validate.
    """

    MAJOR = "major"
    MINOR = "minor"


class KeyUnmeasuredReason(StrEnum):
    """Why no key was reported. **Not an error code.**

    A key that could not be established is a normal outcome, in exactly the way
    ``INSUFFICIENT_PITCH_SIGNAL`` is: the recording did not carry the evidence.
    These values never appear in the error envelope and never become an HTTP
    status. They exist so a reader is told *why* the answer is "not measured"
    instead of being asked to trust it.
    """

    #: Too few pitch classes were used to distinguish one key from another. One
    #: held note fits twenty-four keys about equally well.
    TOO_FEW_PITCH_CLASSES = "TOO_FEW_PITCH_CLASSES"
    #: Not enough pitched audio to aggregate.
    TOO_LITTLE_VOICED_TIME = "TOO_LITTLE_VOICED_TIME"
    #: Pitch classes enough, but no candidate stood clear of the next one.
    AMBIGUOUS = "AMBIGUOUS"


class PitchClassShare(BaseModel):
    """How much of the pitched time went to one pitch class.

    A **pitch class**, not a note: ``A2``, ``A4`` and ``A5`` all contribute here,
    because a key is a statement about pitch classes and octaves are irrelevant
    to it. ``NoteSummary`` is the octave-aware view of the same timeline, and the
    two answer different questions.

    The share is of **voiced** time, the same denominator ``NoteSummary`` uses,
    so the twelve values sum to 100 rather than to the recording's length.
    """

    model_config = ConfigDict(frozen=True)

    pitch_class: int = Field(ge=0, le=11)
    name: str = Field(pattern=_PITCH_CLASS_PATTERN)
    percentage_of_voiced_time: float = Field(ge=0, le=100)


class KeyCandidate(BaseModel):
    """One key, and how far it stood clear of the next one."""

    model_config = ConfigDict(frozen=True)

    tonic: str = Field(pattern=_PITCH_CLASS_PATTERN)
    mode: KeyMode
    #: The margin between this candidate's correlation and the next candidate's.
    #: **Not a raw correlation and not a probability.** A random pitch-class
    #: profile correlates +0.428 with a real key profile and a single held note
    #: correlates +0.684, so a raw correlation reports a confident key for a hum.
    #: The margin is what separates evidence from arithmetic. See
    #: ``docs/audio-analysis.md``.
    confidence: float = Field(ge=0, le=2)


class KeyEstimate(BaseModel):
    """The key a recording's pitch classes best fit, with its runner-up.

    **An estimate of what was sung, not a statement that it was right.** There is
    no reference melody in this product, so this cannot say whether the key was
    the intended one — only which key the notes that were sung best fit.
    """

    model_config = ConfigDict(frozen=True)

    tonic: str = Field(pattern=_PITCH_CLASS_PATTERN)
    mode: KeyMode
    confidence: float = Field(ge=0, le=2)
    #: The candidate that came second, so a close call is visible rather than
    #: hidden behind a single confident-looking label.
    alternative: KeyCandidate | None = None


class KeyAnalysis(BaseModel):
    """A key estimate, or a stated reason there is none, plus its evidence.

    The measurements travel with the verdict in **both** cases. A reader who is
    told "not measured" can see the pitch classes that led to it, which is the
    difference between a refusal and a shrug.
    """

    model_config = ConfigDict(frozen=True)

    key: KeyEstimate | None = None
    unmeasured_reason: KeyUnmeasuredReason | None = None
    #: Always twelve entries, in pitch-class order, including the unused ones at
    #: zero. A key is decided as much by which classes are *absent* as by which
    #: are present, so omitting them would hide half the evidence.
    pitch_classes: tuple[PitchClassShare, ...] = ()
    #: Pitch classes carrying more than a negligible share. The count that the
    #: evidence gate is applied to.
    distinct_pitch_classes: int = Field(default=0, ge=0, le=12)
    voiced_seconds: float = Field(default=0.0, ge=0)
    #: Which published profile set produced this. The numbers are only
    #: interpretable against it, exactly as the analyzer publishes its settings.
    method: str = ""

    @model_validator(mode="after")
    def _check_verdict(self) -> Self:
        if (self.key is None) == (self.unmeasured_reason is None):
            raise ValueError("exactly one of key and unmeasured_reason must be set")
        if self.pitch_classes and len(self.pitch_classes) != 12:
            raise ValueError("a pitch-class profile has twelve entries or none")
        return self


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


class AudioFeedbackRequest(BaseModel):
    """Exactly what an AI provider is given about a recording's audio.

    A typed object rather than a loose dictionary, and a **selection** rather
    than a dump: it carries the measurements that have an interpretation worth
    reading and leaves out the ones that do not. Building it is the only way
    audio measurements reach a model, so what is absent here cannot be
    mentioned there.

    Two things are deliberately *not* in it: the recording, in any form, and any
    identifier. A provider given this object has no audio, no path, no filename
    and no recording id — the same restriction the speech feedback protocol
    imposes, for the same reason.

    Optional fields stay ``None`` when the signal did not support them.
    Serialising them as absent is the payload builder's job, not this model's.
    """

    model_config = ConfigDict(frozen=True)

    duration_seconds: float = Field(gt=0)
    #: The threshold ``in_tune_ratio`` was measured against. Sent so a provider
    #: can state the definition rather than guess at one.
    in_tune_threshold_cents: float = Field(gt=0)

    pitch: VocalRange | None = None
    stability: PitchStability
    loudness: Loudness
    spectral: SpectralFeatures | None = None
    #: Longest first. Capped by the builder; a provider does not need forty rows
    #: to say where the time went.
    notes: tuple[NoteSummary, ...] = ()


class AudioFeedback(BaseModel):
    """A provider's interpretation of measured audio.

    Sections are separate fields rather than one blob of prose so the UI can
    omit what is empty and never has to parse Markdown to find a heading.

    Note what has no field here: a score, a grade, a rating, a timbre label, or
    anything about vocal health. There is nowhere to put one, which is the
    cheapest way to guarantee none is shown.
    """

    model_config = ConfigDict(frozen=True)

    summary: str = Field(min_length=1)
    strengths: tuple[str, ...] = ()
    areas_to_improve: tuple[str, ...] = ()
    pitch_observations: tuple[str, ...] = ()
    range_observations: tuple[str, ...] = ()
    note_observations: tuple[str, ...] = ()
    audio_observations: tuple[str, ...] = ()
    exercises: tuple[str, ...] = ()
    practice_plan: tuple[str, ...] = ()
    provenance: Provenance


class AudioFeedbackStatus(StrEnum):
    """Where a recording's audio feedback has got to.

    Stored on the analysis record rather than in a second machine of its own.
    ``UNAVAILABLE`` is deliberately absent: a recording with no completed
    analysis has no record to store a status on, so "unavailable" is derived by
    the API from the analysis it could not find.
    """

    NOT_REQUESTED = "not_requested"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


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


class AudioAnalysisSummary(BaseModel):
    """One deterministic audio analysis of one recording, **without its timeline**.

    Everything a record says about itself — what state it is in, why it failed,
    what was measured — except the frame-by-frame pitch points. The timeline is
    the whole cost of a stored analysis: about 1.6 MB of JSON for a five-minute
    recording against 1.2 kB for everything else, so a reader that does not
    return it should not pay for it. :class:`AudioAnalysis` is this plus the
    points, and the split is a *type*, not a convention — see below.

    The validator encodes what makes a stored record trustworthy: a failure
    always says why, a success never carries a stale error code, and a completed
    record always has its metrics attached. Those rules are stated once, here,
    and inherited, so a summary read cannot be validated more loosely than the
    record it summarises.

    ``pitch_point_count`` is how many points the stored timeline holds, and it is
    the reason a summary is honest rather than lossy: a reader can still tell
    "measured 12 931 frames" from "measured nothing" without loading either.
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
    #: Frames in the stored timeline. On :class:`AudioAnalysis` it is derived
    #: from the points themselves and cannot be set independently of them.
    pitch_point_count: int = Field(default=0, ge=0)

    #: Generated prose about the measurements. Optional in every sense: an
    #: analysis is complete without it, and a provider outage costs the prose
    #: and nothing else.
    feedback: AudioFeedback | None = None
    feedback_status: AudioFeedbackStatus = AudioFeedbackStatus.NOT_REQUESTED
    #: Why feedback generation failed, when it did.
    feedback_error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.status is AudioAnalysisStatus.FAILED:
            if self.error_code is None:
                raise ValueError("a failed audio analysis must record an error_code")
        elif self.error_code is not None:
            raise ValueError("error_code is only valid on a failed audio analysis")

        if self.status is AudioAnalysisStatus.COMPLETED and self.metrics is None:
            raise ValueError("a completed audio analysis must have metrics")

        if self.pitch_point_count and self.metrics is None:
            raise ValueError("pitch points cannot exist without the metrics describing them")

        # The same rule the speech analysis keeps: prose is never stored
        # without the numbers it describes.
        if self.feedback is not None and self.metrics is None:
            raise ValueError("feedback cannot be attached without the metrics it describes")
        if (self.feedback is not None) != (self.feedback_status is AudioFeedbackStatus.COMPLETED):
            raise ValueError("completed feedback and stored feedback must agree")
        if (self.feedback_error_code is not None) != (
            self.feedback_status is AudioFeedbackStatus.FAILED
        ):
            raise ValueError("a failed feedback run must record exactly one error code")

        if self.completed_at is not None and self.status not in TERMINAL_STATUSES:
            raise ValueError("completed_at is only valid once the analysis has finished")

        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class AudioAnalysis(AudioAnalysisSummary):
    """A summary **with** its pitch timeline attached.

    ``pitch_points`` can be long — tens of thousands of entries for a long
    recording — which is why the API exposes it on its own path rather than
    inside the summary response, and why most reads use the base class instead.

    A subclass rather than a sibling, and that direction is deliberate. Anything
    that only needs the state, the measurements or the feedback accepts a
    :class:`AudioAnalysisSummary` and is handed either kind; anything that reads
    the timeline — the note breakdown, the key, and **every write**, which
    re-serialises the whole document — asks for this type and cannot be given a
    record whose points were left in the database. That is a type error at the
    call site rather than a silently empty timeline, which is the failure this
    split exists to prevent.

    ``pitch_point_count`` is set here from the points themselves, so it can
    never disagree with them and no caller has to maintain it.
    """

    pitch_points: tuple[PitchPoint, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _count_the_points(cls, data: Any) -> Any:
        """Derive the count from the timeline, whatever a caller supplied.

        A stored document written before this field existed has no count, and
        one written after has a count that is already correct; recomputing
        covers both and makes a stale or forged value unrepresentable.
        """
        if isinstance(data, dict):
            points = data.get("pitch_points") or ()
            return {**data, "pitch_point_count": len(points)}
        return data


class DecimatedTimeline(BaseModel):
    """Every ``decimation``-th point of a stored timeline, and the record it came from.

    The third form of an audio-analysis read, and the reason it is a separate
    type rather than an :class:`AudioAnalysis` with fewer points: these points
    are a *sample* of the stored timeline, so a record carrying them must never
    be written back. :class:`AudioAnalysisSummary` cannot be handed to
    ``update`` — that is the rule 10.11 established — and a summary is what this
    carries, so the same rule holds here without a second argument.

    ``analysis.pitch_point_count`` is the length of the **stored** timeline, not
    of ``points``. A reader can therefore always tell "12 931 frames were
    measured, you are looking at 995 of them" from "995 frames were measured",
    which is the distinction the count exists to keep.
    """

    model_config = ConfigDict(frozen=True)

    analysis: AudioAnalysisSummary
    #: Points at stored indices ``0, decimation, 2·decimation, …``, in order.
    points: tuple[PitchPoint, ...] = ()
    #: 1 when nothing was dropped; ``n`` when every ``n``-th point was taken.
    decimation: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check_the_sample_matches_the_stride(self) -> Self:
        """The number of points is a consequence of the count and the stride.

        Taking every ``n``-th of ``c`` points yields ``ceil(c / n)`` of them, so
        a query that dropped or repeated one is arithmetic that no longer adds
        up rather than a response that merely looks plausible. It is checked
        here, once, instead of in each of the two repositories that build one.
        """
        expected = -(-self.analysis.pitch_point_count // self.decimation)
        if len(self.points) != expected:
            raise ValueError(
                f"a stride of {self.decimation} over {self.analysis.pitch_point_count} points "
                f"is {expected} points, not {len(self.points)}"
            )
        return self
