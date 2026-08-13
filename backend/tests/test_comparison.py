"""The comparison domain: deltas, units, availability and caveats.

Pure functions, so these run with no database, no filesystem and no fixtures
beyond a couple of constructors. What they pin down is what a comparison is
*allowed to say*:

* a delta always has a unit and a direction, and `percentage_points` is never
  `percent`;
* a measurement one recording has and the other does not produces **no delta**,
  ever, rather than a delta against zero;
* a wider detected range is not an improvement, and nothing in the output claims
  it is;
* reversing the two recordings reverses every sign and changes nothing else.
"""

from typing import Any

import pytest

from app.services.audio_analysis.models import (
    AnalysisSettings,
    AudioMetrics,
    Loudness,
    NoteSummary,
    PitchStability,
    SpectralFeatures,
    VocalRange,
)
from app.services.audio_analysis.vocabulary import MetricDirection, MetricUnit
from app.services.comparison.compare import (
    compare_metrics,
    compare_notes,
    detect_caveats,
    voiced_seconds_of,
)
from app.services.comparison.models import (
    ComparedMetric,
    ComparisonCaveat,
    MetricAvailability,
    NotePresence,
    compared_metric,
)

SAMPLE_RATE = 22050
HOP = 512


def settings(**overrides: Any) -> AnalysisSettings:
    base: dict[str, Any] = {
        "sample_rate_hz": SAMPLE_RATE,
        "frame_length_samples": 2048,
        "hop_length_samples": HOP,
        "min_frequency_hz": 65.0,
        "max_frequency_hz": 1100.0,
        "clarity_threshold": 0.8,
        "silence_rms": 0.005,
    }
    base.update(overrides)
    return AnalysisSettings(**base)


def stability(**overrides: Any) -> PitchStability:
    base: dict[str, Any] = {
        "voiced_ratio": 0.8,
        "total_frames": 500,
        "voiced_frames": 400,
        "mean_cents_deviation": -2.0,
        "mean_abs_cents_deviation": 12.0,
        "cents_std": 18.0,
        "semitone_variance": 0.4,
        "in_tune_ratio": 0.75,
    }
    base.update(overrides)
    return PitchStability(**base)


def metrics(**overrides: Any) -> AudioMetrics:
    base: dict[str, Any] = {
        "settings": settings(),
        "duration_seconds": 12.0,
        "pitch": VocalRange(
            lowest_frequency_hz=98.0,
            highest_frequency_hz=523.25,
            lowest_note="G2",
            highest_note="C5",
            semitone_span=29,
        ),
        "stability": stability(),
        "loudness": Loudness(rms=0.18, peak=0.92, dynamic_range_db=15.0, clipped_sample_ratio=0.0),
        "spectral": SpectralFeatures(
            centroid_hz=2180.0,
            bandwidth_hz=1900.0,
            rolloff_hz=4200.0,
            zero_crossing_rate=0.08,
            flatness=0.12,
        ),
    }
    base.update(overrides)
    return AudioMetrics(**base)


def note(midi: int, name: str, *, share: float = 25.0, mean_abs: float = 10.0) -> NoteSummary:
    return NoteSummary(
        midi_note=midi,
        note_name=name,
        duration_seconds=share / 10,
        percentage_of_voiced_time=share,
        frame_count=max(1, int(share)),
        average_cents=-mean_abs,
        mean_abs_cents=mean_abs,
        in_tune_ratio=0.5,
    )


def by_key(compared: tuple[ComparedMetric, ...]) -> dict[str, ComparedMetric]:
    return {metric.key: metric for metric in compared}


# --- Units and directions --------------------------------------------------


def test_every_metric_states_a_unit_and_a_direction() -> None:
    """A number without either is not a comparison."""
    for metric in compare_metrics(metrics(), metrics()):
        assert isinstance(metric.unit, MetricUnit)
        assert isinstance(metric.direction, MetricDirection)
        assert metric.label


def test_a_ratio_delta_is_expressed_in_percentage_points_not_percent() -> None:
    """0.50 to 0.56 is six percentage points. Calling it six percent is wrong."""
    compared = by_key(
        compare_metrics(
            metrics(stability=stability(in_tune_ratio=0.50)),
            metrics(stability=stability(in_tune_ratio=0.56)),
        )
    )

    in_tune = compared["in_tune_ratio"]
    assert in_tune.unit is MetricUnit.PERCENTAGE_POINTS
    assert in_tune.left == pytest.approx(50.0)
    assert in_tune.right == pytest.approx(56.0)
    assert in_tune.delta == pytest.approx(6.0)


def test_a_cents_delta_is_in_cents() -> None:
    compared = by_key(
        compare_metrics(
            metrics(stability=stability(mean_abs_cents_deviation=20.0)),
            metrics(stability=stability(mean_abs_cents_deviation=12.0)),
        )
    )

    deviation = compared["mean_abs_cents_deviation"]
    assert deviation.unit is MetricUnit.CENTS
    assert deviation.delta == pytest.approx(-8.0)
    assert deviation.direction is MetricDirection.LOWER_IS_NEARER_THE_NOTE


def test_a_range_delta_is_in_semitones_and_has_no_better_direction() -> None:
    """A wider detected range is a difference, not an achievement."""
    wide = VocalRange(
        lowest_frequency_hz=98.0,
        highest_frequency_hz=880.0,
        lowest_note="G2",
        highest_note="A5",
        semitone_span=38,
    )
    compared = by_key(compare_metrics(metrics(), metrics(pitch=wide)))

    span = compared["semitone_span"]
    assert span.unit is MetricUnit.SEMITONES
    assert span.delta == pytest.approx(9.0)
    assert span.direction is MetricDirection.NEUTRAL


def test_duration_and_pitched_time_are_neutral_seconds() -> None:
    compared = by_key(compare_metrics(metrics(), metrics(duration_seconds=30.0)))

    for key in ("duration_seconds", "voiced_seconds"):
        assert compared[key].unit is MetricUnit.SECONDS
        assert compared[key].direction is MetricDirection.NEUTRAL


def test_only_tuning_metrics_claim_a_desirable_direction() -> None:
    """Three of the seven have a direction; the rest must not imply one."""
    directed = {
        metric.key
        for metric in compare_metrics(metrics(), metrics())
        if metric.direction is not MetricDirection.NEUTRAL
    }

    assert directed == {"in_tune_ratio", "mean_abs_cents_deviation", "cents_std"}


def test_pitched_time_is_derived_from_frames_and_the_hop() -> None:
    assert voiced_seconds_of(metrics()) == pytest.approx(400 * HOP / SAMPLE_RATE)


# --- Missing measurements --------------------------------------------------


def test_a_metric_missing_on_one_side_produces_no_delta() -> None:
    """The rule that stops a null becoming a zero."""
    compared = by_key(compare_metrics(metrics(), metrics(stability=stability(in_tune_ratio=None))))

    in_tune = compared["in_tune_ratio"]
    assert in_tune.availability is MetricAvailability.LEFT_ONLY
    assert in_tune.right is None
    assert in_tune.delta is None


def test_a_metric_missing_on_both_sides_produces_no_delta() -> None:
    absent = stability(mean_abs_cents_deviation=None, cents_std=None, in_tune_ratio=None)
    compared = by_key(compare_metrics(metrics(stability=absent), metrics(stability=absent)))

    for key in ("in_tune_ratio", "mean_abs_cents_deviation", "cents_std"):
        assert compared[key].availability is MetricAvailability.NEITHER
        assert compared[key].delta is None


def test_a_recording_with_no_detected_range_reports_it_missing_not_zero() -> None:
    """A completed analysis can have no range: voiced, but nothing held long enough."""
    compared = by_key(compare_metrics(metrics(), metrics(pitch=None)))

    span = compared["semitone_span"]
    assert span.right is None
    assert span.right != 0
    assert span.delta is None
    assert span.availability is MetricAvailability.LEFT_ONLY


def test_a_zero_measurement_is_not_treated_as_missing() -> None:
    """Zero is a measurement. It must produce a delta like any other number."""
    compared = by_key(
        compare_metrics(
            metrics(stability=stability(in_tune_ratio=0.0)),
            metrics(stability=stability(in_tune_ratio=0.4)),
        )
    )

    in_tune = compared["in_tune_ratio"]
    assert in_tune.left == 0.0
    assert in_tune.availability is MetricAvailability.BOTH
    assert in_tune.delta == pytest.approx(40.0)


def test_a_metric_cannot_be_built_with_a_delta_it_did_not_earn() -> None:
    """The model refuses the mistake rather than trusting callers not to make it."""
    with pytest.raises(ValueError, match="delta"):
        ComparedMetric(
            key="x",
            label="X",
            unit=MetricUnit.CENTS,
            direction=MetricDirection.NEUTRAL,
            left=None,
            right=3.0,
            delta=3.0,
            availability=MetricAvailability.RIGHT_ONLY,
        )


def test_the_constructor_is_the_only_way_a_delta_appears() -> None:
    built = compared_metric(
        key="x",
        label="X",
        unit=MetricUnit.CENTS,
        direction=MetricDirection.NEUTRAL,
        left=None,
        right=3.0,
    )

    assert built.delta is None
    assert built.availability is MetricAvailability.RIGHT_ONLY


# --- Direction of the subtraction ------------------------------------------


def test_a_delta_is_right_minus_left() -> None:
    compared = by_key(
        compare_metrics(metrics(duration_seconds=10.0), metrics(duration_seconds=25.0))
    )

    assert compared["duration_seconds"].delta == pytest.approx(15.0)


def test_swapping_the_recordings_reverses_every_sign_and_nothing_else() -> None:
    left = metrics(duration_seconds=10.0, stability=stability(in_tune_ratio=0.4))
    right = metrics(duration_seconds=25.0, stability=stability(in_tune_ratio=0.9))

    forward = by_key(compare_metrics(left, right))
    backward = by_key(compare_metrics(right, left))

    assert forward.keys() == backward.keys()
    for key, metric in forward.items():
        mirrored = backward[key]
        assert mirrored.unit is metric.unit
        assert mirrored.direction is metric.direction
        if metric.delta is None:
            assert mirrored.delta is None
        else:
            assert mirrored.delta == pytest.approx(-metric.delta)


def test_the_same_measurements_produce_zero_deltas_not_missing_ones() -> None:
    for metric in compare_metrics(metrics(), metrics()):
        if metric.availability is MetricAvailability.BOTH:
            assert metric.delta == pytest.approx(0.0)


def test_comparison_is_deterministic() -> None:
    left, right = metrics(), metrics(duration_seconds=30.0)

    first = compare_metrics(left, right)
    second = compare_metrics(left, right)

    assert [m.model_dump() for m in first] == [m.model_dump() for m in second]


# --- Notes -----------------------------------------------------------------


def test_notes_are_aligned_by_pitch_ascending() -> None:
    """A row must mean the same note in both columns, whichever is on the left."""
    left = (note(67, "G4", share=60.0), note(60, "C4", share=40.0))
    right = (note(62, "D4", share=100.0),)

    compared = compare_notes(left, right)

    assert [row.midi_note for row in compared] == [60, 62, 67]


def test_a_note_in_both_recordings_carries_every_delta() -> None:
    compared = compare_notes(
        (note(60, "C4", share=40.0, mean_abs=20.0),),
        (note(60, "C4", share=55.0, mean_abs=12.0),),
    )

    row = compared[0]
    assert row.presence is NotePresence.BOTH
    assert row.share_delta_points == pytest.approx(15.0)
    assert row.mean_abs_cents_delta == pytest.approx(-8.0)
    assert row.duration_delta_seconds == pytest.approx(1.5)


def test_a_note_sung_in_only_one_recording_is_absent_not_zero() -> None:
    """ "Never sung" and "sung for no time" are different statements."""
    compared = compare_notes((note(60, "C4"),), (note(62, "D4"),))

    only_left = next(row for row in compared if row.midi_note == 60)
    assert only_left.presence is NotePresence.LEFT_ONLY
    assert only_left.right is None
    assert only_left.share_delta_points is None
    assert only_left.duration_delta_seconds is None


def test_an_empty_breakdown_on_one_side_leaves_every_row_one_sided() -> None:
    compared = compare_notes((note(60, "C4"), note(62, "D4")), ())

    assert len(compared) == 2
    assert all(row.presence is NotePresence.RIGHT_ONLY or row.right is None for row in compared)
    assert all(row.share_delta_points is None for row in compared)


def test_two_empty_breakdowns_compare_to_nothing() -> None:
    assert compare_notes((), ()) == ()


def test_note_comparison_is_deterministic() -> None:
    left = (note(67, "G4"), note(60, "C4"))
    right = (note(62, "D4"), note(60, "C4"))

    assert [row.model_dump() for row in compare_notes(left, right)] == [
        row.model_dump() for row in compare_notes(left, right)
    ]


# --- Caveats ---------------------------------------------------------------


def caveats_of(left: AudioMetrics, right: AudioMetrics, **kwargs: str) -> set[ComparisonCaveat]:
    options = {"left_format": "wav", "right_format": "wav"}
    options.update(kwargs)
    return set(detect_caveats(left, right, **options))  # type: ignore[arg-type]


def test_similar_recordings_raise_no_caveats() -> None:
    assert caveats_of(metrics(), metrics()) == set()


def test_a_much_longer_recording_is_flagged() -> None:
    found = caveats_of(metrics(duration_seconds=10.0), metrics(duration_seconds=180.0))

    assert ComparisonCaveat.DIFFERENT_DURATION in found


def test_very_different_pitched_time_is_flagged() -> None:
    found = caveats_of(
        metrics(stability=stability(voiced_frames=400, total_frames=500)),
        metrics(stability=stability(voiced_frames=40, total_frames=500, voiced_ratio=0.08)),
    )

    assert ComparisonCaveat.DIFFERENT_VOICED_TIME in found


def test_a_recording_that_barely_carried_a_pitch_is_flagged() -> None:
    found = caveats_of(
        metrics(),
        metrics(stability=stability(voiced_ratio=0.05, voiced_frames=25, total_frames=500)),
    )

    assert ComparisonCaveat.LITTLE_PITCHED_SIGNAL in found


def test_different_sample_rates_are_flagged() -> None:
    found = caveats_of(metrics(), metrics(settings=settings(sample_rate_hz=44100)))

    assert ComparisonCaveat.DIFFERENT_SAMPLE_RATE in found


def test_different_audio_formats_are_flagged() -> None:
    """Lossy compression changes the signal the analyzer measured."""
    found = caveats_of(metrics(), metrics(), right_format="mp3")

    assert ComparisonCaveat.DIFFERENT_AUDIO_FORMAT in found


def test_different_analysis_settings_are_flagged() -> None:
    found = caveats_of(metrics(), metrics(settings=settings(clarity_threshold=0.9)))

    assert ComparisonCaveat.DIFFERENT_ANALYSIS_SETTINGS in found


def test_clipping_is_flagged_as_a_condition_not_compared_as_a_metric() -> None:
    clipped = Loudness(rms=0.4, peak=1.0, dynamic_range_db=8.0, clipped_sample_ratio=0.01)
    found = caveats_of(metrics(), metrics(loudness=clipped))

    assert ComparisonCaveat.CLIPPING in found
    assert "clipped_sample_ratio" not in {m.key for m in compare_metrics(metrics(), metrics())}


def test_a_missing_range_is_flagged_as_a_caveat() -> None:
    assert ComparisonCaveat.NO_DETECTED_RANGE in caveats_of(metrics(), metrics(pitch=None))


def test_caveats_are_deterministic_and_ordered() -> None:
    left = metrics(duration_seconds=10.0)
    right = metrics(duration_seconds=200.0, settings=settings(sample_rate_hz=44100))

    assert detect_caveats(left, right, left_format="wav", right_format="mp3") == detect_caveats(
        left, right, left_format="wav", right_format="mp3"
    )


# --- What must not exist ---------------------------------------------------


def test_no_loudness_or_spectral_measurement_is_compared() -> None:
    """RMS depends on input gain; spectral features have no validated reading."""
    keys = {metric.key for metric in compare_metrics(metrics(), metrics())}

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
    ):
        assert forbidden not in keys


def test_no_metric_is_named_like_a_score_or_a_verdict() -> None:
    for metric in compare_metrics(metrics(), metrics()):
        text = f"{metric.key} {metric.label}".lower()
        for word in ("score", "grade", "rating", "rank", "better", "worse", "improve", "ability"):
            assert word not in text, f"{metric.key} reads as a verdict"
