"""The progress domain: measurements over time, and nothing more.

**"Your recorded measurements over time"** is the whole product claim. The types
here are shaped so a larger one cannot be expressed:

* There is no field that could hold a level, a grade, a rank or an overall
  figure — the same technique ``AudioFeedback`` and ``RecordingComparison``
  already use, and the reason none of them has ever had to be policed.
* Four of the seven series are ``NEUTRAL``: a wider detected range, more pitched
  time, a higher voiced share and a longer recording are facts about what was
  recorded, not achievements.
* The three directed series are defined against **equal temperament**, not
  against singing. None of them is skill.
* There is no slope, no regression, no moving average and no forecast. The most
  this domain will say is how the latest measured point compares with the one
  before it, and it says so as an observation rather than a verdict.

A point has three possible states and they are never collapsed. ``MEASURED``
carries a number; ``NOT_MEASURED`` means the analysis finished and this
particular metric was unavailable; ``NOT_ELIGIBLE`` means the recording has no
completed analysis to read. The last two produce **gaps**, never zeroes, and
still appear in the context list so a recording is never silently absent from
its own history.
"""

from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.audio_analysis.vocabulary import MetricDirection, MetricUnit


class ProgressMetric(StrEnum):
    """The measurements that may be plotted over time.

    Deliberately the same seven the recording comparison uses. One vocabulary
    means a reader who learned what "typical distance from a note" means while
    comparing two takes does not relearn it while looking at twenty.

    What is **absent** is the load-bearing part: no RMS, no peak, no spectral
    feature, no clipping, no note count and no note diversity. RMS and peak
    depend on input gain, the spectral features have no validated interpretation
    in this project, and singing more distinct notes is not an achievement.
    Plotting any of them over time would invent an interpretation this system
    has consistently refused to make.
    """

    IN_TUNE_RATIO = "in_tune_ratio"
    MEAN_ABS_CENTS_DEVIATION = "mean_abs_cents_deviation"
    CENTS_STD = "cents_std"
    SEMITONE_SPAN = "semitone_span"
    VOICED_RATIO = "voiced_ratio"
    VOICED_SECONDS = "voiced_seconds"
    DURATION_SECONDS = "duration_seconds"


class PointStatus(StrEnum):
    """Whether a recording contributed a number to a series, and if not, why."""

    #: A real measurement. The only state that carries a value.
    MEASURED = "measured"
    #: The analysis completed, but this metric was not available from it. A
    #: completed analysis can have no detected range — frames were voiced, none
    #: was held long enough — and no tuning statistics when nothing was voiced.
    NOT_MEASURED = "not_measured"
    #: No completed analysis: never run, still running, failed, or the audio
    #: carried no reliable pitch. The recording cannot contribute a point.
    NOT_ELIGIBLE = "not_eligible"


class ProgressPoint(BaseModel):
    """One recording's value for one metric.

    Kept deliberately thin — an id, a timestamp, a state and a number. The
    recording's name, duration and detected range live once in
    :class:`ProgressHistory.recordings` rather than being copied into every
    series, so seven series over fifty recordings is seven small arrays and one
    context list rather than three hundred duplicated records.
    """

    model_config = ConfigDict(frozen=True)

    recording_id: str
    #: The recording's server-assigned creation time. The client clock is never
    #: consulted; see ``sources.py`` for the ordering this establishes.
    recorded_at: datetime
    status: PointStatus
    #: Present only when ``status`` is ``MEASURED``. Never a zero standing in
    #: for a missing measurement.
    value: float | None = None

    @model_validator(mode="after")
    def _check_value(self) -> Self:
        if self.status is PointStatus.MEASURED:
            if self.value is None:
                raise ValueError("a measured point must carry its value")
        elif self.value is not None:
            raise ValueError("only a measured point may carry a value")
        return self


class ObservationDirection(StrEnum):
    """How the latest measured point compares with the one before it.

    An **observation**, not a verdict. ``HIGHER`` and ``LOWER`` describe the
    number; whether that is welcome depends on the metric's direction, and for
    four of the seven it is neither.
    """

    HIGHER = "higher"
    LOWER = "lower"
    UNCHANGED = "unchanged"
    #: Fewer than two measured points. Not "no change" — no basis for saying.
    INSUFFICIENT_DATA = "insufficient_data"


class LatestObservation(BaseModel):
    """The latest measured value against the previous measured one.

    Deliberately the simplest defensible statement about change: two points, one
    subtraction, no smoothing. A slope or a percentage-improvement figure over
    recordings whose conditions nobody controlled would be a statistical claim
    the data cannot support, so none is computed.

    Non-measured recordings are skipped when finding "the previous one" — a gap
    is not a value, and comparing across it is still comparing two real
    measurements.
    """

    model_config = ConfigDict(frozen=True)

    direction: ObservationDirection
    latest: float | None = None
    previous: float | None = None
    #: ``latest - previous``, in the series' unit. ``None`` without two points.
    delta: float | None = None
    latest_recording_id: str | None = None
    previous_recording_id: str | None = None

    @model_validator(mode="after")
    def _check_delta(self) -> Self:
        known = self.direction is not ObservationDirection.INSUFFICIENT_DATA
        if known != (self.delta is not None):
            raise ValueError("a delta exists exactly when two measured points do")
        return self


class ProgressSeries(BaseModel):
    """One metric over time, with its unit and what its direction means."""

    model_config = ConfigDict(frozen=True)

    metric: ProgressMetric
    label: str = Field(min_length=1)
    unit: MetricUnit
    direction: MetricDirection
    #: Oldest first. Every recording in the window appears, including the ones
    #: that contributed no number, so the series and the context list line up.
    points: tuple[ProgressPoint, ...] = ()
    observation: LatestObservation

    @property
    def measured_count(self) -> int:
        return sum(1 for point in self.points if point.status is PointStatus.MEASURED)


class ProgressRecording(BaseModel):
    """One recording's context: what it was, when, and how far it got.

    Carries no owner id, no analysis id, no path and no provider. The detected
    range is here as note names because ``G2–C5`` is a label rather than a
    quantity; the plottable part of it is the span, which is a series.
    """

    model_config = ConfigDict(frozen=True)

    recording_id: str
    recorded_at: datetime
    original_filename: str
    duration_seconds: float
    audio_format: str
    #: ``True`` when a completed audio analysis exists to read metrics from.
    analysed: bool
    lowest_note: str | None = None
    highest_note: str | None = None


class HistoryDepth(StrEnum):
    """How much history exists, and therefore what may honestly be shown.

    Named rather than left to the client to infer from a count, because the
    honest thing to say differs at each step and two points are emphatically not
    a trend.
    """

    EMPTY = "empty"
    #: One measured recording: a value, but nothing to compare it against.
    SINGLE = "single"
    #: Two measured recordings: a comparison is possible, a trend is not.
    PAIR = "pair"
    #: Three or more: enough to draw a line worth looking at.
    SERIES = "series"


#: Measured points needed before a line is worth drawing rather than a pair of
#: dots. Below this the UI says so instead of implying a trend.
TREND_MINIMUM: Final = 3


def depth_of(measured_points: int) -> HistoryDepth:
    """Classify how much measured history exists."""
    if measured_points <= 0:
        return HistoryDepth.EMPTY
    if measured_points == 1:
        return HistoryDepth.SINGLE
    if measured_points < TREND_MINIMUM:
        return HistoryDepth.PAIR
    return HistoryDepth.SERIES


class ProgressHistory(BaseModel):
    """Every series, plus the recordings behind them.

    Note what has no field here: an overall figure, a level, a rank, or any
    statement that the singing changed. This model can say that a number was
    higher in the most recent recording. It cannot say what that means about the
    person, and it is not asked to.
    """

    model_config = ConfigDict(frozen=True)

    series: tuple[ProgressSeries, ...] = ()
    #: Oldest first, matching the points. Includes recordings that contributed
    #: no measurement, so nothing disappears from its own history.
    recordings: tuple[ProgressRecording, ...] = ()
    #: How much *measured* history exists, across the series as a whole.
    depth: HistoryDepth = HistoryDepth.EMPTY
    #: The largest number of recordings this request could return.
    limit: int = 0
