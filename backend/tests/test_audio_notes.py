"""The note breakdown: aggregating a pitch timeline by musical note.

Driven with hand-built timelines rather than audio, because the thing under test
is arithmetic over points that the detector has already produced. The detector's
own behaviour is tested in ``test_audio_detector.py`` and
``test_audio_analyzer.py``, and nothing here touches it.

The properties asserted hardest are the ones that would quietly mislead:
percentages that are shares of the wrong denominator, a note split into one
entry per cents value, an order that changes between runs, and an empty result
that reads like a successful measurement of zero.
"""

import pytest

from app.services.audio_analysis.models import IN_TUNE_CENTS, PitchFields, PitchPoint
from app.services.audio_analysis.notes import (
    frame_duration_seconds,
    summarise_notes,
    voiced_seconds,
)

#: 512 samples at 22050 Hz — the hop the analyzer actually uses at that rate.
HOP_SAMPLES = 512
SAMPLE_RATE = 22050
FRAME_SECONDS = HOP_SAMPLES / SAMPLE_RATE

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def point(midi: int, *, cents: float = 0.0, index: int = 0) -> PitchPoint:
    """One voiced frame on a given note, with a given deviation from it."""
    return PitchPoint(
        timestamp_seconds=index * FRAME_SECONDS,
        # The frequency is not what the aggregation groups on; the note is.
        frequency_hz=440.0 * 2 ** ((midi + cents / 100 - 69) / 12),
        midi_note=midi,
        note_name=note_name(midi),
        cents=cents,
        confidence=0.95,
    )


def timeline(*runs: tuple[int, int]) -> tuple[PitchPoint, ...]:
    """Build a timeline from ``(midi, frame count)`` runs, in order."""
    points: list[PitchPoint] = []
    for midi, count in runs:
        for _ in range(count):
            points.append(point(midi, index=len(points)))
    return tuple(points)


def fields(points: tuple[PitchPoint, ...]) -> PitchFields:
    """The fixture as the aggregation reads it: two fields of every frame.

    Fixtures are still written as whole frames, validated by the same model the
    analyzer writes, so nothing here can express a point the pipeline could not
    produce. The conversion is the one production uses — the store projects the
    same two fields straight out of the stored document, and the contract suite
    asserts the two routes reach the same arrays.
    """
    return PitchFields.of_points(points)


def summarise(points: tuple[PitchPoint, ...]):  # type: ignore[no-untyped-def]
    return summarise_notes(fields(points), frame_seconds=FRAME_SECONDS)


# --- Basic aggregation -----------------------------------------------------


def test_three_c4_frames_and_two_d4_frames_rank_c4_first() -> None:
    notes = summarise(timeline((60, 3), (62, 2)))

    assert [note.note_name for note in notes] == ["C4", "D4"]
    assert notes[0].frame_count == 3
    assert notes[1].frame_count == 2
    assert notes[0].duration_seconds > notes[1].duration_seconds


def test_durations_are_frame_count_times_the_hop() -> None:
    notes = summarise(timeline((60, 10)))
    assert notes[0].duration_seconds == pytest.approx(10 * FRAME_SECONDS)


def test_relative_durations_match_relative_frame_counts() -> None:
    notes = summarise(timeline((60, 60), (62, 30), (64, 10)))
    assert notes[0].duration_seconds == pytest.approx(2 * notes[1].duration_seconds)
    assert notes[1].duration_seconds == pytest.approx(3 * notes[2].duration_seconds)


def test_the_durations_sum_to_the_voiced_time() -> None:
    """The definition the whole feature rests on."""
    points = timeline((60, 40), (62, 25), (64, 12))
    notes = summarise(points)

    assert sum(note.duration_seconds for note in notes) == pytest.approx(
        voiced_seconds(fields(points), FRAME_SECONDS)
    )


def test_frames_scattered_through_the_recording_still_aggregate() -> None:
    """A note returned to later is the same note, not a second entry."""
    notes = summarise(timeline((60, 5), (62, 5), (60, 5)))

    assert len(notes) == 2
    assert notes[0].note_name == "C4"
    assert notes[0].frame_count == 10


# --- Segmentation ----------------------------------------------------------


def test_cents_variation_around_one_note_is_one_entry() -> None:
    """C4 at −5, +8, −12 and +4 cents is C4, not four notes."""
    points = tuple(
        point(60, cents=value, index=index) for index, value in enumerate([-5.0, 8.0, -12.0, 4.0])
    )
    notes = summarise(points)

    assert len(notes) == 1
    assert notes[0].note_name == "C4"
    assert notes[0].frame_count == 4


def test_a_rising_line_produces_one_entry_per_note() -> None:
    notes = summarise(timeline((60, 6), (62, 6), (64, 6)))
    assert {note.note_name for note in notes} == {"C4", "D4", "E4"}
    assert all(note.frame_count == 6 for note in notes)


def test_notes_an_octave_apart_are_different_notes() -> None:
    notes = summarise(timeline((60, 5), (72, 5)))
    assert [note.midi_note for note in notes] == [60, 72]
    assert {note.note_name for note in notes} == {"C4", "C5"}


# --- Percentages -----------------------------------------------------------


def test_percentages_sum_to_one_hundred() -> None:
    notes = summarise(timeline((60, 41), (62, 27), (64, 13), (65, 7)))
    assert sum(note.percentage_of_voiced_time for note in notes) == pytest.approx(100.0)


def test_percentages_are_shares_of_voiced_time_not_of_the_recording() -> None:
    """A silent half of a recording must not halve every note's share.

    Unvoiced frames never reach the timeline, so a recording that is half
    silence produces half as many points — and the same percentages.
    """
    dense = summarise(timeline((60, 30), (62, 10)))
    sparse = summarise(timeline((60, 15), (62, 5)))

    assert dense[0].percentage_of_voiced_time == pytest.approx(75.0)
    assert sparse[0].percentage_of_voiced_time == pytest.approx(75.0)


def test_a_single_note_takes_all_of_the_voiced_time() -> None:
    notes = summarise(timeline((60, 17)))
    assert notes[0].percentage_of_voiced_time == pytest.approx(100.0)


@pytest.mark.parametrize(
    "runs",
    [
        ((60, 1),),
        ((60, 3), (62, 3)),
        ((60, 100), (61, 1), (62, 50), (72, 7)),
        ((36, 4), (60, 4), (84, 4)),
    ],
)
def test_invariants_hold_for_any_timeline(runs: tuple[tuple[int, int], ...]) -> None:
    points = timeline(*runs)
    notes = summarise(points)

    assert sum(note.percentage_of_voiced_time for note in notes) == pytest.approx(100.0)
    for note in notes:
        assert 0 <= note.percentage_of_voiced_time <= 100
        assert note.duration_seconds >= 0
        assert note.frame_count >= 1
        assert 0 <= note.in_tune_ratio <= 1
        assert -50 <= note.average_cents <= 50
        assert 0 <= note.mean_abs_cents <= 50
    assert sum(note.frame_count for note in notes) == len(points)


# --- Cents -----------------------------------------------------------------


def test_average_cents_is_the_signed_mean() -> None:
    points = tuple(
        point(60, cents=value, index=index) for index, value in enumerate([-10.0, -20.0, -30.0])
    )
    assert summarise(points)[0].average_cents == pytest.approx(-20.0)


def test_average_cents_cancels_where_mean_abs_cents_does_not() -> None:
    """The two answer different questions and a flat-and-sharp note shows it."""
    points = tuple(point(60, cents=value, index=index) for index, value in enumerate([-20.0, 20.0]))
    note = summarise(points)[0]

    assert note.average_cents == pytest.approx(0.0)
    assert note.mean_abs_cents == pytest.approx(20.0)


def test_a_sharp_note_reports_a_positive_average() -> None:
    points = tuple(point(60, cents=value, index=index) for index, value in enumerate([12.0, 18.0]))
    assert summarise(points)[0].average_cents == pytest.approx(15.0)


# --- In-tune ratio ---------------------------------------------------------


def test_in_tune_ratio_uses_the_shared_threshold() -> None:
    points = (
        point(60, cents=0.0, index=0),
        point(60, cents=IN_TUNE_CENTS, index=1),  # the boundary counts
        point(60, cents=IN_TUNE_CENTS + 1, index=2),
        point(60, cents=-IN_TUNE_CENTS - 1, index=3),
    )
    assert summarise(points)[0].in_tune_ratio == pytest.approx(0.5)


def test_a_note_sung_dead_on_is_entirely_in_tune() -> None:
    assert summarise(timeline((60, 8)))[0].in_tune_ratio == pytest.approx(1.0)


def test_a_note_sung_consistently_off_reports_zero_which_is_measured() -> None:
    """A genuine zero. Distinct from a missing measurement, which is absent."""
    points = tuple(point(60, cents=40.0, index=index) for index in range(6))
    assert summarise(points)[0].in_tune_ratio == 0.0


# --- Ordering --------------------------------------------------------------


def test_notes_are_ordered_by_duration_descending() -> None:
    notes = summarise(timeline((64, 3), (60, 9), (62, 6)))
    assert [note.note_name for note in notes] == ["C4", "D4", "E4"]


def test_equal_durations_break_the_tie_on_the_lower_note() -> None:
    notes = summarise(timeline((67, 4), (60, 4), (64, 4)))
    assert [note.midi_note for note in notes] == [60, 64, 67]


def test_the_order_is_stable_across_runs() -> None:
    """Without a tie-break this would follow dictionary iteration order."""
    points = timeline((72, 5), (60, 5), (66, 5), (63, 5))
    first = [note.midi_note for note in summarise(points)]
    for _ in range(5):
        assert [note.midi_note for note in summarise(points)] == first
    assert first == [60, 63, 66, 72]


# --- Nothing to break down -------------------------------------------------


def test_an_empty_timeline_produces_no_notes() -> None:
    """Not a note of zero duration, and not a zero. Nothing."""
    assert summarise(()) == ()


def test_an_impossible_frame_duration_produces_no_notes_rather_than_infinity() -> None:
    assert summarise_notes(fields(timeline((60, 5))), frame_seconds=0.0) == ()


@pytest.mark.parametrize(("hop", "rate"), [(0, 22050), (512, 0), (-1, 22050)])
def test_an_impossible_frame_configuration_is_refused(hop: int, rate: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        frame_duration_seconds(hop, rate)


def test_the_frame_duration_is_the_hop_not_the_frame_length() -> None:
    """Charging each frame its full length would multiply every duration."""
    assert frame_duration_seconds(512, 22050) == pytest.approx(0.0232, abs=0.0005)
    assert frame_duration_seconds(1024, 44100) == pytest.approx(0.0232, abs=0.0005)


def test_voiced_seconds_of_an_empty_timeline_is_zero() -> None:
    assert voiced_seconds(PitchFields(), FRAME_SECONDS) == 0.0


# --- Short events ----------------------------------------------------------


def test_a_short_note_is_reported_rather_than_deleted() -> None:
    """No minimum-duration rule is applied here; see the module docstring.

    Upstream rules already removed detector artefacts. A two-frame entry is
    surfaced with its frame count so a reader can see how thin it is, rather
    than silently dropped by a threshold nobody chose deliberately.
    """
    notes = summarise(timeline((60, 40), (61, 2), (62, 20)))

    passing = next(note for note in notes if note.note_name == "C#4")
    assert passing.frame_count == 2
    assert passing.percentage_of_voiced_time < 5


def test_a_short_note_cannot_dominate_the_breakdown() -> None:
    notes = summarise(timeline((60, 200), (61, 1)))
    assert notes[0].note_name == "C4"
    assert notes[0].percentage_of_voiced_time > 99
