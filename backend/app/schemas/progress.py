"""API representation of an owner's measurements over time.

Built explicitly from the domain, so a field added upstream cannot reach a
response by accident. What that filter removes here: **no owner id, no analysis
id, no storage path, no provider, no settings block, no pitch timeline.**

Three distinctions survive into the wire format because the product depends on
them:

* ``status`` distinguishes ``measured`` from ``not_measured`` from
  ``not_eligible``. Only the first carries a ``value``, and the other two carry
  ``null`` — never a zero. A client draws them as gaps.
* ``direction`` says whether a change means anything. Four of the seven series
  are ``neutral``: a wider detected range, more pitched time, a higher voiced
  share and a longer recording are facts about what was recorded, not
  achievements.
* ``depth`` says how much *measured* history exists, so a client can be honest
  about two points not being a trend rather than inferring it from a count.

There is no overall figure in this response, and no field that could hold one.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.progress.models import (
    LatestObservation,
    ProgressHistory,
    ProgressPoint,
    ProgressRecording,
    ProgressSeries,
)


class ProgressPointResponse(BaseModel):
    """One recording's value for one metric."""

    recording_id: str
    recorded_at: datetime = Field(
        description="The recording's server-assigned creation time, in UTC."
    )
    status: str = Field(
        description=(
            "`measured` — a real value. `not_measured` — the analysis completed "
            "but this metric was unavailable. `not_eligible` — the recording has "
            "no completed analysis. Only `measured` carries a `value`."
        )
    )
    value: float | None = Field(
        description="The measurement, in the series' unit. `null` is never a zero."
    )

    @classmethod
    def from_domain(cls, point: ProgressPoint) -> "ProgressPointResponse":
        return cls(
            recording_id=point.recording_id,
            recorded_at=point.recorded_at,
            status=point.status.value,
            value=point.value,
        )


class LatestObservationResponse(BaseModel):
    """The latest measured value against the previous measured one.

    An observation, not a verdict: `higher` and `lower` describe the number.
    Whether that is welcome depends on the series' `direction`, and for four of
    the seven it is neither.
    """

    direction: str = Field(
        description=(
            "`higher`, `lower`, `unchanged`, or `insufficient_data` when fewer "
            "than two recordings carried this measurement. "
            "`insufficient_data` is not `unchanged`."
        )
    )
    latest: float | None = None
    previous: float | None = None
    delta: float | None = Field(
        default=None, description="`latest - previous`, in the series' unit."
    )
    latest_recording_id: str | None = None
    previous_recording_id: str | None = None

    @classmethod
    def from_domain(cls, observation: LatestObservation) -> "LatestObservationResponse":
        return cls(
            direction=observation.direction.value,
            latest=observation.latest,
            previous=observation.previous,
            delta=observation.delta,
            latest_recording_id=observation.latest_recording_id,
            previous_recording_id=observation.previous_recording_id,
        )


class ProgressSeriesResponse(BaseModel):
    """One metric over time."""

    metric: str
    label: str
    unit: str = Field(
        description="`percentage_points`, `cents`, `semitones` or `seconds`. Never `percent`."
    )
    direction: str = Field(
        description=(
            "`higher_is_nearer_the_note`, `lower_is_nearer_the_note`, "
            "`lower_is_steadier`, or `neutral`. The three directed metrics are "
            "defined against equal temperament, not against singing; none of "
            "them is a measure of skill."
        )
    )
    points: list[ProgressPointResponse] = Field(
        description="Oldest first. Every recording in the window appears, measured or not."
    )
    observation: LatestObservationResponse
    measured_count: int = Field(description="How many points in this series carry a value.")

    @classmethod
    def from_domain(cls, series: ProgressSeries) -> "ProgressSeriesResponse":
        return cls(
            metric=series.metric.value,
            label=series.label,
            unit=series.unit.value,
            direction=series.direction.value,
            points=[ProgressPointResponse.from_domain(p) for p in series.points],
            observation=LatestObservationResponse.from_domain(series.observation),
            measured_count=series.measured_count,
        )


class ProgressRecordingResponse(BaseModel):
    """One recording's context, so every point is traceable to a take."""

    recording_id: str
    recorded_at: datetime
    original_filename: str
    duration_seconds: float
    audio_format: str
    analysed: bool = Field(description="Whether a completed audio analysis exists.")
    lowest_note: str | None = Field(
        default=None,
        description=(
            "Lowest note detected **in this recording** — never a physiological "
            "limit. `null` when no pitch was held long enough to establish a range."
        ),
    )
    highest_note: str | None = None

    @classmethod
    def from_domain(cls, recording: ProgressRecording) -> "ProgressRecordingResponse":
        return cls(
            recording_id=recording.recording_id,
            recorded_at=recording.recorded_at,
            original_filename=recording.original_filename,
            duration_seconds=recording.duration_seconds,
            audio_format=recording.audio_format,
            analysed=recording.analysed,
            lowest_note=recording.lowest_note,
            highest_note=recording.highest_note,
        )


class ProgressHistoryResponse(BaseModel):
    """An owner's recorded measurements over time.

    **Measurements, not a verdict.** There is no score, no level, no rank and no
    claim that the singing changed — the response has nowhere to put one.
    """

    series: list[ProgressSeriesResponse]
    recordings: list[ProgressRecordingResponse] = Field(
        description="Oldest first, matching the points. Includes unmeasured recordings."
    )
    depth: str = Field(
        description=(
            "How much measured history exists: `empty`, `single` (one "
            "measurement — nothing to compare it with), `pair` (two — a "
            "comparison, not a trend), or `series` (three or more)."
        )
    )
    limit: int = Field(description="The largest number of recordings this request could return.")

    @classmethod
    def from_domain(cls, history: ProgressHistory) -> "ProgressHistoryResponse":
        return cls(
            series=[ProgressSeriesResponse.from_domain(s) for s in history.series],
            recordings=[ProgressRecordingResponse.from_domain(r) for r in history.recordings],
            depth=history.depth.value,
            limit=history.limit,
        )
