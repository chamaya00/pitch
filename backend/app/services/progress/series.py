"""Turning loaded rows into progress series.

A pure function. It opens no connections, reads no files, calls no provider and
re-measures nothing: it is handed rows that were selected and ordered by SQL and
it arranges them. That is why the whole of it tests in microseconds, and why the
*meaning* of a progress series cannot change because a query changed.

Three rules govern everything below.

**A null never becomes a zero.** Each metric is read as ``float | None``, and a
``None`` produces a point with no value and a status saying which kind of absence
it was. There is no default, no ``or 0`` and no fill-forward anywhere in this
module.

**Nothing is smoothed, fitted or forecast.** The strongest statement made here is
that the latest measured value was higher, lower or unchanged against the
previous measured one. A slope over recordings whose conditions nobody controlled
would be a statistical claim the data cannot support.

**Four of the seven series have no better direction.** They are marked
``NEUTRAL`` and the wording that reaches a reader is built from that, not from a
guess about what "up" means.
"""

from collections.abc import Callable
from typing import Final

from app.services.audio_analysis.vocabulary import MetricDirection, MetricUnit
from app.services.progress.models import (
    LatestObservation,
    ObservationDirection,
    PointStatus,
    ProgressHistory,
    ProgressMetric,
    ProgressPoint,
    ProgressRecording,
    ProgressSeries,
    depth_of,
)
from app.services.progress.sources import ProgressRow

__all__ = ["METRIC_DEFINITIONS", "build_history", "observe"]

#: Ratios are stored 0–1 and shown as percentages. Converted in exactly one
#: place, so a value can never reach a reader labelled with the wrong unit.
_TO_PERCENT: Final = 100.0


def _percent(ratio: float | None) -> float | None:
    return None if ratio is None else ratio * _TO_PERCENT


def _voiced_seconds(row: ProgressRow) -> float | None:
    """Pitched time, from the frame count and the hop.

    The same arithmetic the note breakdown uses to make its durations sum to the
    voiced time. Derived rather than stored, so there is no second copy to keep
    consistent — and ``None`` the moment any input is missing rather than
    quietly treating an absent frame count as no pitched time.
    """
    if row.voiced_frames is None or row.hop_length_samples is None:
        return None
    if row.sample_rate_hz is None or row.sample_rate_hz <= 0:
        return None
    return row.voiced_frames * row.hop_length_samples / row.sample_rate_hz


class MetricDefinition:
    """How one metric is read, labelled and interpreted."""

    __slots__ = ("direction", "label", "metric", "read", "unit")

    def __init__(
        self,
        metric: ProgressMetric,
        label: str,
        unit: MetricUnit,
        direction: MetricDirection,
        read: Callable[[ProgressRow], float | None],
    ) -> None:
        self.metric = metric
        self.label = label
        self.unit = unit
        self.direction = direction
        self.read = read


#: Every plottable metric, in the order a reader wants them offered: how in tune,
#: then how far from a note, then how steady, then the neutral observations about
#: what was recorded.
#:
#: The absences matter as much as the entries. No RMS, no peak, no spectral
#: feature, no clipping, no note count — see ``models.ProgressMetric``.
METRIC_DEFINITIONS: Final[tuple[MetricDefinition, ...]] = (
    MetricDefinition(
        ProgressMetric.IN_TUNE_RATIO,
        "Share of pitched time within 25 cents of a note",
        MetricUnit.PERCENTAGE_POINTS,
        MetricDirection.HIGHER_IS_NEARER_THE_NOTE,
        lambda row: _percent(row.in_tune_ratio),
    ),
    MetricDefinition(
        ProgressMetric.MEAN_ABS_CENTS_DEVIATION,
        "Typical distance from the nearest note",
        MetricUnit.CENTS,
        MetricDirection.LOWER_IS_NEARER_THE_NOTE,
        lambda row: row.mean_abs_cents_deviation,
    ),
    MetricDefinition(
        ProgressMetric.CENTS_STD,
        "Spread of pitch placement",
        MetricUnit.CENTS,
        MetricDirection.LOWER_IS_STEADIER,
        lambda row: row.cents_std,
    ),
    MetricDefinition(
        ProgressMetric.SEMITONE_SPAN,
        "Detected range",
        MetricUnit.SEMITONES,
        # Wider is not better. The range is bounded by what was performed, by
        # the microphone and by the detector's own search limits.
        MetricDirection.NEUTRAL,
        lambda row: None if row.semitone_span is None else float(row.semitone_span),
    ),
    MetricDefinition(
        ProgressMetric.VOICED_RATIO,
        "Share of the recording that carried a pitch",
        MetricUnit.PERCENTAGE_POINTS,
        MetricDirection.NEUTRAL,
        lambda row: _percent(row.voiced_ratio),
    ),
    MetricDefinition(
        ProgressMetric.VOICED_SECONDS,
        "Pitched time",
        MetricUnit.SECONDS,
        MetricDirection.NEUTRAL,
        _voiced_seconds,
    ),
    MetricDefinition(
        ProgressMetric.DURATION_SECONDS,
        "Recording length",
        MetricUnit.SECONDS,
        MetricDirection.NEUTRAL,
        lambda row: row.duration_seconds,
    ),
)


def _point(row: ProgressRow, definition: MetricDefinition) -> ProgressPoint:
    """One recording's contribution to one series.

    Three outcomes, kept distinct because each means something different to a
    reader: no completed analysis to read at all; an analysis that completed
    without this measurement; or a number.
    """
    if not row.analysed:
        status = PointStatus.NOT_ELIGIBLE
        value = None
    else:
        value = definition.read(row)
        status = PointStatus.NOT_MEASURED if value is None else PointStatus.MEASURED

    return ProgressPoint(
        recording_id=row.recording_id,
        recorded_at=row.recorded_at,
        status=status,
        value=value,
    )


def observe(points: tuple[ProgressPoint, ...]) -> LatestObservation:
    """Compare the latest measured point with the previous measured one.

    Gaps are skipped rather than counted: a recording that contributed no number
    is not a value, so "the previous one" means the previous *measurement*. That
    keeps the observation about two things that were actually measured.

    Fewer than two measured points is ``INSUFFICIENT_DATA`` — which is not
    "no change", and must never be rendered as one.
    """
    measured = [point for point in points if point.status is PointStatus.MEASURED]
    if len(measured) < 2:
        return LatestObservation(direction=ObservationDirection.INSUFFICIENT_DATA)

    previous, latest = measured[-2], measured[-1]
    assert latest.value is not None and previous.value is not None  # noqa: S101 - filtered above
    delta = latest.value - previous.value

    if delta > 0:
        direction = ObservationDirection.HIGHER
    elif delta < 0:
        direction = ObservationDirection.LOWER
    else:
        direction = ObservationDirection.UNCHANGED

    return LatestObservation(
        direction=direction,
        latest=latest.value,
        previous=previous.value,
        delta=delta,
        latest_recording_id=latest.recording_id,
        previous_recording_id=previous.recording_id,
    )


def build_history(rows: list[ProgressRow], *, limit: int) -> ProgressHistory:
    """Assemble every series from rows the query already ordered.

    The ordering is not re-derived here. SQL returned the window oldest-first
    with a deterministic tie-break, and re-sorting would put two definitions of
    "in order" in the codebase for one concept.
    """
    series = tuple(
        _series(definition, tuple(_point(row, definition) for row in rows))
        for definition in METRIC_DEFINITIONS
    )

    recordings = tuple(
        ProgressRecording(
            recording_id=row.recording_id,
            recorded_at=row.recorded_at,
            original_filename=row.original_filename,
            duration_seconds=row.recording_duration_seconds,
            audio_format=row.audio_format,
            analysed=row.analysed,
            lowest_note=row.lowest_note,
            highest_note=row.highest_note,
        )
        for row in rows
    )

    # Depth is the richest any single series manages. A recording measured for
    # tuning but with no detected range still counts as history — saying "not
    # enough data" because one series is thin would be wrong about the others.
    deepest = max((item.measured_count for item in series), default=0)

    return ProgressHistory(
        series=series,
        recordings=recordings,
        depth=depth_of(deepest),
        limit=limit,
    )


def _series(definition: MetricDefinition, points: tuple[ProgressPoint, ...]) -> ProgressSeries:
    return ProgressSeries(
        metric=definition.metric,
        label=definition.label,
        unit=definition.unit,
        direction=definition.direction,
        points=points,
        observation=observe(points),
    )
