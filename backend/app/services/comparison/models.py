"""The comparison domain.

A comparison is **two sets of measurements placed side by side, with the
arithmetic difference between them**. That is the whole product definition, and
the types here are shaped to make anything more ambitious impossible to express:

* There is no field that could hold a composite score, a rating or a ranking.
  The absence is the guarantee — the same technique ``AudioFeedback`` uses.
* Every delta carries its **unit** and its **direction semantics**. A number
  without those is not a comparison, it is a coincidence.
* Every metric carries its **availability**. A measurement one recording has and
  the other does not produces no delta at all, rather than a delta against zero.

Nothing here is "better". Three of the seven compared metrics have no desirable
direction — a wider detected range, a longer recording and a higher voiced ratio
are all differences, not improvements — and the ones that do have a direction
say precisely what it means (nearer the nearest equal-tempered note, or steadier
in placement), never "better singing".
"""

from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import ErrorCode
from app.services.audio_analysis.models import NoteSummary


class MetricUnit(StrEnum):
    """What a delta is expressed in.

    ``PERCENTAGE_POINTS`` exists as its own unit because the alternative is the
    most common numerical lie in software: a ratio moving from 0.50 to 0.56 is
    **6 percentage points**, not 6 percent (it is 12 percent). Ratios are
    converted once, here, and every consumer reads the unit rather than guessing.
    """

    PERCENTAGE_POINTS = "percentage_points"
    CENTS = "cents"
    SEMITONES = "semitones"
    SECONDS = "seconds"


class MetricDirection(StrEnum):
    """Whether a direction of change means anything, and exactly what.

    Only two metrics in this system have a defensible desirable direction, and
    both are defined relative to equal-tempered pitch rather than to singing:
    being nearer the nearest semitone, and being steadier about it. Everything
    else is ``NEUTRAL`` — including the detected range, which is bounded by what
    was performed and by the microphone, so a wider one is not an achievement.
    """

    HIGHER_IS_NEARER_THE_NOTE = "higher_is_nearer_the_note"
    LOWER_IS_NEARER_THE_NOTE = "lower_is_nearer_the_note"
    LOWER_IS_STEADIER = "lower_is_steadier"
    NEUTRAL = "neutral"


class MetricAvailability(StrEnum):
    """Which recordings actually supported a measurement.

    Anything other than ``BOTH`` means ``delta`` is ``None``. This is the type-level
    statement of the rule that a missing measurement stays missing: there is no
    combination of fields that produces a number from a null.
    """

    BOTH = "both"
    LEFT_ONLY = "left_only"
    RIGHT_ONLY = "right_only"
    NEITHER = "neither"


class ComparedMetric(BaseModel):
    """One measurement from each recording, and the difference if there is one."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    unit: MetricUnit
    direction: MetricDirection

    #: The measured values, in ``unit``. ``None`` means the recording did not
    #: support the measurement — never zero, and never a placeholder.
    left: float | None = None
    right: float | None = None
    #: ``right - left``. Present only when both sides were measured.
    delta: float | None = None
    availability: MetricAvailability

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        expected = _availability_of(self.left, self.right)
        if self.availability is not expected:
            raise ValueError("availability must describe the values actually present")
        if self.availability is MetricAvailability.BOTH:
            if self.delta is None:
                raise ValueError("two measured values must produce a delta")
        elif self.delta is not None:
            raise ValueError("a delta cannot exist without two measured values")
        return self


def _availability_of(left: float | None, right: float | None) -> MetricAvailability:
    if left is not None and right is not None:
        return MetricAvailability.BOTH
    if left is not None:
        return MetricAvailability.LEFT_ONLY
    if right is not None:
        return MetricAvailability.RIGHT_ONLY
    return MetricAvailability.NEITHER


def compared_metric(
    *,
    key: str,
    label: str,
    unit: MetricUnit,
    direction: MetricDirection,
    left: float | None,
    right: float | None,
) -> ComparedMetric:
    """Build a metric, deriving the availability and the delta from the values.

    The single constructor for every metric in this module, so there is exactly
    one place where a delta can come into existence — and it cannot come into
    existence from a ``None``.
    """
    availability = _availability_of(left, right)
    delta = (
        right - left
        if availability is MetricAvailability.BOTH and left is not None and right is not None
        else None
    )
    return ComparedMetric(
        key=key,
        label=label,
        unit=unit,
        direction=direction,
        left=left,
        right=right,
        delta=delta,
        availability=availability,
    )


class NotePresence(StrEnum):
    """Which recordings contained a given note at all."""

    BOTH = "both"
    LEFT_ONLY = "left_only"
    RIGHT_ONLY = "right_only"


class ComparedNote(BaseModel):
    """One musical note, as it appeared in each recording.

    A note absent from a recording is ``None``, **not a zero-duration entry**.
    The distinction is the whole point: "this note was never sung" and "this note
    was sung for no time at all" are different statements, and only the first is
    ever true.

    Note presence is deliberately not scored. Singing more distinct notes is not
    better, and there is no field here that could say it was.
    """

    model_config = ConfigDict(frozen=True)

    midi_note: int = Field(ge=0, le=127)
    note_name: str
    presence: NotePresence

    left: NoteSummary | None = None
    right: NoteSummary | None = None

    #: Difference in share of *voiced* time, in percentage points.
    share_delta_points: float | None = None
    #: Difference in time spent on the note, in seconds.
    duration_delta_seconds: float | None = None
    #: Difference in mean distance from the note, in cents. Lower is nearer.
    mean_abs_cents_delta: float | None = None

    @model_validator(mode="after")
    def _check_presence(self) -> Self:
        present = NotePresence.BOTH
        if self.left is None and self.right is None:
            raise ValueError("a compared note must appear in at least one recording")
        if self.left is None:
            present = NotePresence.RIGHT_ONLY
        elif self.right is None:
            present = NotePresence.LEFT_ONLY
        if self.presence is not present:
            raise ValueError("presence must describe the entries actually present")

        deltas = (
            self.share_delta_points,
            self.duration_delta_seconds,
            self.mean_abs_cents_delta,
        )
        if present is NotePresence.BOTH:
            if any(delta is None for delta in deltas):
                raise ValueError("a note in both recordings must have every delta")
        elif any(delta is not None for delta in deltas):
            raise ValueError("a note in one recording cannot have a delta")
        return self


class SideStatus(StrEnum):
    """Whether one recording can take part, and if not, why.

    Each value maps to a distinct thing the reader can do about it, which is why
    they are not collapsed into a single "unavailable". "Nobody has measured this
    yet" and "measuring it failed" are different situations.
    """

    READY = "ready"
    #: Unknown to this owner. Deliberately the same answer for "does not exist"
    #: and "belongs to somebody else" — see the service.
    NOT_FOUND = "not_found"
    ANALYSIS_MISSING = "analysis_missing"
    ANALYSIS_IN_PROGRESS = "analysis_in_progress"
    ANALYSIS_FAILED = "analysis_failed"
    INSUFFICIENT_PITCH_SIGNAL = "insufficient_pitch_signal"


class ComparisonCaveat(StrEnum):
    """A measurable reason two recordings may not be telling the same story.

    Every one of these is derived from metadata the system **already** records.
    Nothing here claims to detect microphone quality, room acoustics, effort or
    physical condition, because nothing in this system measures those — see
    ``docs/limitations.md``.
    """

    DIFFERENT_DURATION = "different_duration"
    DIFFERENT_VOICED_TIME = "different_voiced_time"
    LITTLE_PITCHED_SIGNAL = "little_pitched_signal"
    DIFFERENT_SAMPLE_RATE = "different_sample_rate"
    DIFFERENT_AUDIO_FORMAT = "different_audio_format"
    DIFFERENT_ANALYSIS_SETTINGS = "different_analysis_settings"
    CLIPPING = "clipping"
    NO_DETECTED_RANGE = "no_detected_range"


class ComparisonSide(BaseModel):
    """One recording's identity and state, safe to return to its owner.

    Carries no path, no owner id, no provider name and no analysis internals —
    only what the comparison UI needs to label a column.
    """

    model_config = ConfigDict(frozen=True)

    recording_id: str
    status: SideStatus
    #: Present only once the recording is known to this owner.
    original_filename: str | None = None
    created_at: datetime | None = None
    duration_seconds: float | None = None
    audio_format: str | None = None
    #: Why the analysis failed, when it did. A documented code, never a message
    #: from a decoder.
    error_code: ErrorCode | None = None

    #: The detected range, as text. Compared as a span in semitones; the note
    #: names are shown side by side without a delta, because "C4 versus E4" is
    #: not a quantity.
    lowest_note: str | None = None
    highest_note: str | None = None


class RecordingComparison(BaseModel):
    """The whole comparison: two sides, the deltas, and what to be careful of.

    Note what is not here: an overall figure. Seven metrics that answer different
    questions cannot be averaged into one that answers any of them, and there is
    no field in this model that could hold the attempt.
    """

    model_config = ConfigDict(frozen=True)

    left: ComparisonSide
    right: ComparisonSide
    #: ``True`` only when both sides are ``READY``. Everything below is empty
    #: otherwise — a comparison of one recording is not a comparison.
    comparable: bool
    metrics: tuple[ComparedMetric, ...] = ()
    #: Ordered by pitch, ascending. See ``compare.py`` for why this differs from
    #: the single-recording breakdown's longest-first ordering.
    notes: tuple[ComparedNote, ...] = ()
    caveats: tuple[ComparisonCaveat, ...] = ()

    @model_validator(mode="after")
    def _check_eligibility(self) -> Self:
        ready = self.left.status is SideStatus.READY and self.right.status is SideStatus.READY
        if self.comparable is not ready:
            raise ValueError("comparable must mean both sides are ready")
        if not ready and (self.metrics or self.notes):
            raise ValueError("an ineligible comparison carries no measurements")
        return self


#: One recording at least this many times the other's duration is a difference
#: worth naming. A display threshold chosen to catch "a 20-second warm-up versus
#: a 3-minute take", not a statistical test.
DURATION_RATIO: Final = 2.0

#: The same idea applied to pitched time, which is what the tuning statistics
#: are actually computed over.
VOICED_SECONDS_RATIO: Final = 2.0

#: Below this share of voiced frames, the tuning statistics rest on very little
#: of the recording and the caveat says so.
LOW_VOICED_RATIO: Final = 0.10

#: Any measurable clipping is worth naming: it changes the signal the analyzer
#: saw, and it is a property of the recording rather than of the voice.
CLIPPING_RATIO: Final = 0.0
