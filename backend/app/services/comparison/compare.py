"""The comparison itself: two measured recordings in, one deterministic result out.

A pure function. It reads no files, opens no connections, calls no provider and
re-measures nothing — it is handed two ``AudioMetrics`` objects and two note
breakdowns that were computed and stored elsewhere, and it subtracts. That is
why the whole of it can be tested in microseconds with no fixtures, and why a
change to the analyzer cannot silently change what a comparison *means*.

Three rules govern every number below.

**A delta is ``right - left``, always.** One direction, stated once, so "higher"
in the UI always means "the right-hand recording is higher". Reversing the
selection reverses every sign, which is correct and is tested.

**Ratios become percentage points.** ``in_tune_ratio`` and ``voiced_ratio`` are
0–1 in the domain and are multiplied by 100 exactly here, so the unit attached to
the delta is `percentage_points` and never `percent`.

**A missing measurement produces no delta.** Not zero, not an assumed value, not
a skipped row — a metric with its `availability` set and its `delta` absent.
"""

from typing import Final

from app.services.audio_analysis.models import (
    AudioMetrics,
    NoteSummary,
)
from app.services.comparison.models import (
    CLIPPING_RATIO,
    DURATION_RATIO,
    LOW_VOICED_RATIO,
    VOICED_SECONDS_RATIO,
    ComparedMetric,
    ComparedNote,
    ComparisonCaveat,
    MetricDirection,
    MetricUnit,
    NotePresence,
    compared_metric,
)

__all__ = ["compare_metrics", "compare_notes", "detect_caveats", "voiced_seconds_of"]

#: Ratios are stored 0–1 and shown as percentages. Converted in exactly one
#: place so a delta can never be labelled "percent" by accident.
_TO_PERCENT: Final = 100.0


def _percent(ratio: float | None) -> float | None:
    """A 0–1 ratio as a percentage, preserving ``None``."""
    return None if ratio is None else ratio * _TO_PERCENT


def voiced_seconds_of(metrics: AudioMetrics) -> float:
    """How much of a recording carried a reliable pitch, in seconds.

    Derived rather than stored: ``voiced_frames`` times the hop is the same
    arithmetic the note breakdown uses to make its durations sum to the voiced
    time, and duplicating the value on the record would be a second copy to keep
    consistent.
    """
    settings = metrics.settings
    return metrics.stability.voiced_frames * settings.hop_length_samples / settings.sample_rate_hz


def compare_metrics(left: AudioMetrics, right: AudioMetrics) -> tuple[ComparedMetric, ...]:
    """The side-by-side measurements, in the order a reader wants them.

    Conditions first (how much audio, how much of it was pitched), then what was
    sung (the detected range), then how it sat against equal temperament. A
    reader who starts at the top has the context before the tuning figures, which
    is the order in which those figures are worth anything.

    Loudness and spectral measurements are deliberately **absent**. RMS and peak
    depend on input gain, so a delta says as much about the microphone setup as
    about the singer; the spectral features have no validated interpretation in
    this project and a side-by-side delta would invite exactly the timbre reading
    the rest of the system refuses to make. Clipping — the one loudness fact with
    a defensible reading — is reported as a caveat instead, because it describes
    the recording rather than the voice.
    """
    return (
        compared_metric(
            key="duration_seconds",
            label="Recording length",
            unit=MetricUnit.SECONDS,
            direction=MetricDirection.NEUTRAL,
            left=left.duration_seconds,
            right=right.duration_seconds,
        ),
        compared_metric(
            key="voiced_seconds",
            label="Pitched time",
            unit=MetricUnit.SECONDS,
            direction=MetricDirection.NEUTRAL,
            left=voiced_seconds_of(left),
            right=voiced_seconds_of(right),
        ),
        compared_metric(
            key="voiced_ratio",
            label="Share of the recording that carried a pitch",
            unit=MetricUnit.PERCENTAGE_POINTS,
            direction=MetricDirection.NEUTRAL,
            left=_percent(left.stability.voiced_ratio),
            right=_percent(right.stability.voiced_ratio),
        ),
        compared_metric(
            key="semitone_span",
            label="Detected range",
            unit=MetricUnit.SEMITONES,
            # Wider is not better. The range is bounded by what was performed,
            # by the microphone and by the detector's own search limits, so a
            # wider one is a difference in the recording, not an achievement.
            direction=MetricDirection.NEUTRAL,
            left=None if left.pitch is None else float(left.pitch.semitone_span),
            right=None if right.pitch is None else float(right.pitch.semitone_span),
        ),
        compared_metric(
            key="in_tune_ratio",
            label="Share of pitched time within 25 cents of a note",
            unit=MetricUnit.PERCENTAGE_POINTS,
            direction=MetricDirection.HIGHER_IS_NEARER_THE_NOTE,
            left=_percent(left.stability.in_tune_ratio),
            right=_percent(right.stability.in_tune_ratio),
        ),
        compared_metric(
            key="mean_abs_cents_deviation",
            label="Typical distance from the nearest note",
            unit=MetricUnit.CENTS,
            direction=MetricDirection.LOWER_IS_NEARER_THE_NOTE,
            left=left.stability.mean_abs_cents_deviation,
            right=right.stability.mean_abs_cents_deviation,
        ),
        compared_metric(
            key="cents_std",
            label="Spread of pitch placement",
            unit=MetricUnit.CENTS,
            direction=MetricDirection.LOWER_IS_STEADIER,
            left=left.stability.cents_std,
            right=right.stability.cents_std,
        ),
    )


def compare_notes(
    left: tuple[NoteSummary, ...], right: tuple[NoteSummary, ...]
) -> tuple[ComparedNote, ...]:
    """Align two note breakdowns by musical note.

    **Ordered by pitch, ascending** — deliberately different from the
    single-recording breakdown, which is ordered longest-first. A comparison is
    read across two columns, and a pitch axis is the only ordering under which
    the same row means the same note in both. Longest-first would reorder rows
    depending on which recording happened to be on the left.

    A note present in one recording only produces a row with the other side
    ``None`` and no deltas. It is never filled in as a zero-duration entry: the
    note was not sung, which is not the same as having been sung for no time.
    """
    by_note_left = {note.midi_note: note for note in left}
    by_note_right = {note.midi_note: note for note in right}

    rows: list[ComparedNote] = []
    for midi_note in sorted(by_note_left.keys() | by_note_right.keys()):
        one = by_note_left.get(midi_note)
        other = by_note_right.get(midi_note)

        if one is not None and other is not None:
            rows.append(
                ComparedNote(
                    midi_note=midi_note,
                    note_name=one.note_name,
                    presence=NotePresence.BOTH,
                    left=one,
                    right=other,
                    share_delta_points=(
                        other.percentage_of_voiced_time - one.percentage_of_voiced_time
                    ),
                    duration_delta_seconds=other.duration_seconds - one.duration_seconds,
                    mean_abs_cents_delta=other.mean_abs_cents - one.mean_abs_cents,
                )
            )
            continue

        present = one if one is not None else other
        assert present is not None  # noqa: S101 - one of the two dicts had this key
        rows.append(
            ComparedNote(
                midi_note=midi_note,
                note_name=present.note_name,
                presence=NotePresence.LEFT_ONLY if one is not None else NotePresence.RIGHT_ONLY,
                left=one,
                right=other,
            )
        )
    return tuple(rows)


def detect_caveats(
    left: AudioMetrics,
    right: AudioMetrics,
    *,
    left_format: str,
    right_format: str,
) -> tuple[ComparisonCaveat, ...]:
    """Measurable reasons the two recordings may not be telling the same story.

    Every check below reads something the system already records. There is no
    check for microphone quality, room acoustics, effort or physical condition,
    because nothing here measures them — and inventing a proxy would be worse
    than the honest silence. The standing caveat that two recordings are only
    comparable under reasonably similar conditions is not conditional and is
    stated by the UI unconditionally.
    """
    caveats: list[ComparisonCaveat] = []

    if _differ_by(left.duration_seconds, right.duration_seconds, DURATION_RATIO):
        caveats.append(ComparisonCaveat.DIFFERENT_DURATION)

    if _differ_by(voiced_seconds_of(left), voiced_seconds_of(right), VOICED_SECONDS_RATIO):
        caveats.append(ComparisonCaveat.DIFFERENT_VOICED_TIME)

    if min(left.stability.voiced_ratio, right.stability.voiced_ratio) < LOW_VOICED_RATIO:
        caveats.append(ComparisonCaveat.LITTLE_PITCHED_SIGNAL)

    if left.settings.sample_rate_hz != right.settings.sample_rate_hz:
        caveats.append(ComparisonCaveat.DIFFERENT_SAMPLE_RATE)

    if left_format != right_format:
        # Lossy compression changes what the analyzer saw, so a WAV and an MP3
        # of the same performance are not quite the same measurement.
        caveats.append(ComparisonCaveat.DIFFERENT_AUDIO_FORMAT)

    if _settings_differ(left, right):
        caveats.append(ComparisonCaveat.DIFFERENT_ANALYSIS_SETTINGS)

    clipping = max(left.loudness.clipped_sample_ratio, right.loudness.clipped_sample_ratio)
    if clipping > CLIPPING_RATIO:
        caveats.append(ComparisonCaveat.CLIPPING)

    if left.pitch is None or right.pitch is None:
        caveats.append(ComparisonCaveat.NO_DETECTED_RANGE)

    return tuple(caveats)


def _differ_by(one: float, other: float, ratio: float) -> bool:
    """Whether one value is at least ``ratio`` times the other.

    Guards the zero case explicitly rather than dividing: a recording with no
    pitched time at all differs from one that has some, and ``0/0`` is not the
    answer to that.
    """
    low, high = sorted((one, other))
    if low <= 0:
        return high > 0
    return high / low >= ratio


def _settings_differ(left: AudioMetrics, right: AudioMetrics) -> bool:
    """Whether the two analyses were measured under different parameters.

    The sample rate is checked separately and reported on its own, because it is
    a property of the recording rather than of the analysis configuration.
    """
    return left.settings.model_dump(exclude={"sample_rate_hz"}) != right.settings.model_dump(
        exclude={"sample_rate_hz"}
    )
