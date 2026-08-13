"""Progress series: what may be plotted, and what may be said about it.

Pure functions over rows the query already ordered, so these run with no
database and no fixtures beyond a row constructor. What they pin down is what a
progress history is *allowed to claim*:

* a null never becomes a zero, and the three absence states stay distinct;
* two points are a comparison and not a trend, and the product says so;
* the strongest statement available is latest-versus-previous — no slope, no
  percentage improvement, no forecast;
* four of the seven series have no better direction, and nothing marks them as
  improvement.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.audio_analysis.vocabulary import MetricDirection, MetricUnit
from app.services.progress.models import (
    TREND_MINIMUM,
    HistoryDepth,
    ObservationDirection,
    PointStatus,
    ProgressMetric,
    ProgressPoint,
    depth_of,
)
from app.services.progress.series import METRIC_DEFINITIONS, build_history, observe
from app.services.progress.sources import ProgressRow

BASE = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
SAMPLE_RATE = 22050
HOP = 512


def row(index: int = 0, *, analysed: bool = True, **overrides: Any) -> ProgressRow:
    """A recording with a completed analysis, unless told otherwise."""
    payload: dict[str, Any] = {
        "recording_id": f"{index:032x}",
        "recorded_at": BASE + timedelta(days=index),
        "original_filename": f"take-{index}.wav",
        "recording_duration_seconds": 12.0,
        "audio_format": "wav",
        "analysed": analysed,
    }
    if analysed:
        payload.update(
            duration_seconds=12.0,
            in_tune_ratio=0.75,
            mean_abs_cents_deviation=12.0,
            cents_std=18.0,
            voiced_ratio=0.8,
            voiced_frames=400,
            hop_length_samples=HOP,
            sample_rate_hz=SAMPLE_RATE,
            semitone_span=29,
            lowest_note="G2",
            highest_note="C5",
        )
    payload.update(overrides)
    return ProgressRow(**payload)


def series_of(history: Any, metric: ProgressMetric) -> Any:
    return next(item for item in history.series if item.metric is metric)


def values(history: Any, metric: ProgressMetric) -> list[float | None]:
    return [point.value for point in series_of(history, metric).points]


# --- What is offered -------------------------------------------------------


def test_every_metric_has_a_label_a_unit_and_a_direction() -> None:
    for definition in METRIC_DEFINITIONS:
        assert definition.label
        assert isinstance(definition.unit, MetricUnit)
        assert isinstance(definition.direction, MetricDirection)


def test_exactly_three_series_claim_a_direction() -> None:
    """The other four are observations about what was recorded, not achievements."""
    directed = {
        definition.metric
        for definition in METRIC_DEFINITIONS
        if definition.direction is not MetricDirection.NEUTRAL
    }

    assert directed == {
        ProgressMetric.IN_TUNE_RATIO,
        ProgressMetric.MEAN_ABS_CENTS_DEVIATION,
        ProgressMetric.CENTS_STD,
    }


def test_the_detected_range_is_neutral() -> None:
    """A wider range is bounded by the performance and the microphone."""
    definition = next(
        item for item in METRIC_DEFINITIONS if item.metric is ProgressMetric.SEMITONE_SPAN
    )

    assert definition.direction is MetricDirection.NEUTRAL
    assert definition.unit is MetricUnit.SEMITONES


def test_no_unsupported_measurement_is_offered_as_progress() -> None:
    """RMS depends on input gain; spectral features have no validated reading."""
    offered = {definition.metric.value for definition in METRIC_DEFINITIONS}

    for forbidden in (
        "rms",
        "peak",
        "crest_factor_db",
        "dynamic_range_db",
        "centroid_hz",
        "bandwidth_hz",
        "rolloff_hz",
        "zero_crossing_rate",
        "flatness",
        "clipped_sample_ratio",
        "note_count",
        "note_diversity",
    ):
        assert forbidden not in offered


def test_no_metric_is_named_like_a_score() -> None:
    for definition in METRIC_DEFINITIONS:
        text = f"{definition.metric.value} {definition.label}".lower()
        for word in ("score", "grade", "level", "rank", "skill", "ability", "improve"):
            assert word not in text, f"{definition.metric} reads as a verdict"


# --- Units -----------------------------------------------------------------


def test_ratios_are_converted_to_percentages_exactly_once() -> None:
    history = build_history([row(0, in_tune_ratio=0.62, voiced_ratio=0.5)], limit=10)

    assert values(history, ProgressMetric.IN_TUNE_RATIO) == [pytest.approx(62.0)]
    assert values(history, ProgressMetric.VOICED_RATIO) == [pytest.approx(50.0)]
    assert series_of(history, ProgressMetric.IN_TUNE_RATIO).unit is MetricUnit.PERCENTAGE_POINTS


def test_pitched_time_is_derived_from_frames_and_the_hop() -> None:
    history = build_history([row(0, voiced_frames=400)], limit=10)

    assert values(history, ProgressMetric.VOICED_SECONDS) == [
        pytest.approx(400 * HOP / SAMPLE_RATE)
    ]


def test_pitched_time_is_missing_rather_than_zero_when_an_input_is_absent() -> None:
    history = build_history([row(0, hop_length_samples=None)], limit=10)

    point = series_of(history, ProgressMetric.VOICED_SECONDS).points[0]
    assert point.value is None
    assert point.status is PointStatus.NOT_MEASURED


# --- Null is never zero ----------------------------------------------------


def test_a_null_metric_on_a_completed_analysis_is_not_measured() -> None:
    history = build_history([row(0, in_tune_ratio=None)], limit=10)

    point = series_of(history, ProgressMetric.IN_TUNE_RATIO).points[0]
    assert point.status is PointStatus.NOT_MEASURED
    assert point.value is None
    assert point.value != 0


def test_a_recording_with_no_completed_analysis_is_not_eligible() -> None:
    history = build_history([row(0, analysed=False)], limit=10)

    for series in history.series:
        assert series.points[0].status is PointStatus.NOT_ELIGIBLE
        assert series.points[0].value is None


def test_the_two_absence_states_are_never_collapsed() -> None:
    """ "Nobody measured it" and "it measured nothing" are different facts."""
    history = build_history([row(0, analysed=False), row(1, in_tune_ratio=None)], limit=10)

    statuses = [point.status for point in series_of(history, ProgressMetric.IN_TUNE_RATIO).points]
    assert statuses == [PointStatus.NOT_ELIGIBLE, PointStatus.NOT_MEASURED]


def test_a_completed_analysis_with_no_detected_range_is_not_measured_for_span() -> None:
    """Voiced frames existed; none was held long enough to establish a range."""
    history = build_history(
        [row(0, semitone_span=None, lowest_note=None, highest_note=None)], limit=10
    )

    span = series_of(history, ProgressMetric.SEMITONE_SPAN).points[0]
    assert span.status is PointStatus.NOT_MEASURED
    # The other series are unaffected: one absent metric is not an absent point.
    assert series_of(history, ProgressMetric.IN_TUNE_RATIO).points[0].status is PointStatus.MEASURED


def test_a_zero_measurement_stays_a_measurement() -> None:
    history = build_history([row(0, in_tune_ratio=0.0, cents_std=0.0)], limit=10)

    for metric in (ProgressMetric.IN_TUNE_RATIO, ProgressMetric.CENTS_STD):
        point = series_of(history, metric).points[0]
        assert point.status is PointStatus.MEASURED
        assert point.value == 0.0


def test_a_point_cannot_carry_a_value_it_did_not_measure() -> None:
    """The model refuses the mistake rather than trusting callers not to make it."""
    with pytest.raises(ValueError, match="measured point"):
        ProgressPoint(
            recording_id="a" * 32,
            recorded_at=BASE,
            status=PointStatus.NOT_MEASURED,
            value=0.0,
        )
    with pytest.raises(ValueError, match="measured point"):
        ProgressPoint(recording_id="a" * 32, recorded_at=BASE, status=PointStatus.MEASURED)


# --- Ordering --------------------------------------------------------------


def test_points_keep_the_order_the_query_returned() -> None:
    """SQL orders; the domain does not re-derive a second definition of "in order"."""
    rows = [row(0), row(1), row(2)]

    history = build_history(rows, limit=10)

    stamps = [
        point.recorded_at for point in series_of(history, ProgressMetric.IN_TUNE_RATIO).points
    ]
    assert stamps == sorted(stamps)
    assert [r.recording_id for r in history.recordings] == [r.recording_id for r in rows]


def test_every_series_shares_one_ordering() -> None:
    rows = [row(index) for index in range(4)]
    history = build_history(rows, limit=10)

    orders = {tuple(point.recording_id for point in series.points) for series in history.series}
    assert len(orders) == 1


# --- Depth -----------------------------------------------------------------


def test_no_history_is_empty() -> None:
    history = build_history([], limit=10)

    assert history.depth is HistoryDepth.EMPTY
    assert history.recordings == ()
    assert all(series.points == () for series in history.series)


def test_one_measured_recording_is_a_single() -> None:
    assert build_history([row(0)], limit=10).depth is HistoryDepth.SINGLE


def test_two_measured_recordings_are_a_pair_not_a_trend() -> None:
    """Two points are a comparison. The product must not call them a trend."""
    assert build_history([row(0), row(1)], limit=10).depth is HistoryDepth.PAIR


def test_three_measured_recordings_are_a_series() -> None:
    assert build_history([row(i) for i in range(3)], limit=10).depth is HistoryDepth.SERIES
    assert TREND_MINIMUM == 3


def test_unmeasured_recordings_do_not_count_towards_depth() -> None:
    history = build_history([row(0), row(1, analysed=False), row(2, analysed=False)], limit=10)

    assert history.depth is HistoryDepth.SINGLE
    assert len(history.recordings) == 3, "they still appear in their own history"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, HistoryDepth.EMPTY),
        (1, HistoryDepth.SINGLE),
        (2, HistoryDepth.PAIR),
        (9, HistoryDepth.SERIES),
    ],
)
def test_depth_classification(count: int, expected: HistoryDepth) -> None:
    assert depth_of(count) is expected


# --- Observation -----------------------------------------------------------


def test_one_point_gives_no_observation() -> None:
    observation = series_of(
        build_history([row(0)], limit=10), ProgressMetric.IN_TUNE_RATIO
    ).observation

    assert observation.direction is ObservationDirection.INSUFFICIENT_DATA
    assert observation.delta is None


def test_insufficient_data_is_not_unchanged() -> None:
    """Two different statements, and only one of them is a claim about the voice."""
    assert ObservationDirection.INSUFFICIENT_DATA is not ObservationDirection.UNCHANGED


def test_a_higher_latest_value_is_reported_as_higher() -> None:
    history = build_history([row(0, in_tune_ratio=0.5), row(1, in_tune_ratio=0.62)], limit=10)
    observation = series_of(history, ProgressMetric.IN_TUNE_RATIO).observation

    assert observation.direction is ObservationDirection.HIGHER
    assert observation.delta == pytest.approx(12.0)
    assert observation.latest == pytest.approx(62.0)
    assert observation.previous == pytest.approx(50.0)


def test_a_lower_latest_value_is_reported_as_lower() -> None:
    history = build_history(
        [row(0, mean_abs_cents_deviation=20.0), row(1, mean_abs_cents_deviation=12.0)], limit=10
    )
    observation = series_of(history, ProgressMetric.MEAN_ABS_CENTS_DEVIATION).observation

    assert observation.direction is ObservationDirection.LOWER
    assert observation.delta == pytest.approx(-8.0)


def test_an_identical_pair_is_unchanged() -> None:
    history = build_history([row(0), row(1)], limit=10)

    assert (
        series_of(history, ProgressMetric.IN_TUNE_RATIO).observation.direction
        is ObservationDirection.UNCHANGED
    )


def test_the_observation_compares_the_two_most_recent_measurements() -> None:
    """Not the first and the last: "previous" means the one before the latest."""
    history = build_history(
        [row(0, in_tune_ratio=0.1), row(1, in_tune_ratio=0.9), row(2, in_tune_ratio=0.8)],
        limit=10,
    )
    observation = series_of(history, ProgressMetric.IN_TUNE_RATIO).observation

    assert observation.previous == pytest.approx(90.0)
    assert observation.latest == pytest.approx(80.0)
    assert observation.direction is ObservationDirection.LOWER


def test_gaps_are_skipped_when_finding_the_previous_measurement() -> None:
    """A recording that measured nothing is not a value to compare against."""
    history = build_history(
        [row(0, in_tune_ratio=0.4), row(1, analysed=False), row(2, in_tune_ratio=0.7)],
        limit=10,
    )
    observation = series_of(history, ProgressMetric.IN_TUNE_RATIO).observation

    assert observation.previous == pytest.approx(40.0)
    assert observation.latest == pytest.approx(70.0)
    assert observation.previous_recording_id == f"{0:032x}"


def test_the_observation_names_the_recordings_it_compared() -> None:
    history = build_history([row(0, in_tune_ratio=0.4), row(1, in_tune_ratio=0.7)], limit=10)
    observation = series_of(history, ProgressMetric.IN_TUNE_RATIO).observation

    assert observation.latest_recording_id == f"{1:032x}"
    assert observation.previous_recording_id == f"{0:032x}"


def test_an_observation_cannot_carry_a_delta_without_two_points() -> None:
    assert observe(()).delta is None
    assert (
        observe(
            (
                ProgressPoint(
                    recording_id="a" * 32, recorded_at=BASE, status=PointStatus.MEASURED, value=1.0
                ),
            )
        ).delta
        is None
    )


# --- What is deliberately absent -------------------------------------------


def test_nothing_computes_a_slope_or_a_forecast() -> None:
    """The domain exposes no field a trend line or a prediction could live in."""
    history = build_history([row(i, in_tune_ratio=0.4 + i * 0.1) for i in range(5)], limit=10)
    series = series_of(history, ProgressMetric.IN_TUNE_RATIO)

    fields = set(series.model_dump()) | set(series.observation.model_dump())
    for absent in ("slope", "trend", "regression", "forecast", "average", "score", "level"):
        assert absent not in fields


def test_the_history_has_no_overall_figure() -> None:
    history = build_history([row(i) for i in range(3)], limit=10)

    fields = set(history.model_dump())
    for absent in ("score", "level", "grade", "rank", "overall", "improvement"):
        assert absent not in fields


def test_building_a_history_is_deterministic() -> None:
    rows = [row(i, in_tune_ratio=0.4 + i * 0.05) for i in range(4)]

    assert build_history(rows, limit=10).model_dump() == build_history(rows, limit=10).model_dump()


def test_the_recording_context_carries_no_owner_or_analysis_identity() -> None:
    history = build_history([row(0)], limit=10)

    fields = set(history.recordings[0].model_dump())
    for absent in ("owner_id", "audio_analysis_id", "path", "provider", "document"):
        assert absent not in fields
