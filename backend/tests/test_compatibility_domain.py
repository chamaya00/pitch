"""The compatibility arithmetic: overlap, gaps, transposition, caveats.

Pure functions over two closed intervals of semitones, so these run with no
database, no filesystem and no audio at all — which is the property Option D was
chosen for (``docs/phase-9-specification.md`` §3A).

What they pin down is the set of decisions that are easy to get wrong by one and
impossible to notice afterwards:

* the boundary is **inclusive** at both ends, so a song that exactly reaches the
  singer's top note fits and a shared single note is an overlap of one;
* a *count* of notes and a *distance* in semitones are different numbers, and
  the fields that hold them are never confused;
* a song wider than the range produces a shortfall and **no** suggestion;
* when several shifts work, the recommended one is the smallest, and zero wins
  whenever zero fits;
* the three standing caveats are on every result, unconditionally.
"""

import pytest

from app.services.audio_analysis.models import KeyMode, VocalRange
from app.services.audio_analysis.pitch import midi_for_note_name, midi_to_frequency
from app.services.compatibility.fit import (
    compare,
    compute_fit,
    compute_transposition,
    detect_caveats,
    recording_range,
    reference_range,
    standing_caveats,
    transpose_key,
)
from app.services.compatibility.models import (
    CompatibilityCaveat,
    NoteRange,
    RangeSource,
    RecordingSideStatus,
    ReferenceKey,
    SongReference,
)

#: Enough voiced signal that the conditional caveat never fires unless a test
#: asks it to.
AMPLE_VOICED_RATIO = 0.8


def note_range(low: str, high: str, source: RangeSource = RangeSource.MEASURED) -> NoteRange:
    low_midi = midi_for_note_name(low)
    high_midi = midi_for_note_name(high)
    assert low_midi is not None and high_midi is not None
    return NoteRange(
        lowest_note=low,
        highest_note=high,
        lowest_midi=low_midi,
        highest_midi=high_midi,
        semitone_span=high_midi - low_midi,
        source=source,
    )


def reference(
    low: str = "C4",
    high: str = "C5",
    key: ReferenceKey | None = None,
    reference_id: str = "a" * 32,
) -> SongReference:
    return SongReference(
        reference_id=reference_id,
        title="A song",
        artist="Somebody",
        lowest_note=low,
        highest_note=high,
        key=key,
    )


# --- Reference validation ---------------------------------------------------


def test_a_reference_cannot_be_upside_down() -> None:
    with pytest.raises(ValueError, match="highest note is below the lowest"):
        reference(low="C5", high="C4")


def test_a_reference_of_one_note_is_allowed() -> None:
    """A drone or a single-note exercise. Legal, and the arithmetic must survive it."""
    single = reference(low="A4", high="A4")
    assert single.lowest_midi == single.highest_midi == 69


def test_a_note_outside_midi_is_refused_even_though_it_matches_the_pattern() -> None:
    """``B9`` is well-formed scientific notation and is not a MIDI note.

    The field pattern admits it; the validator is what actually decides, so a
    row read back from storage is held to the same rule as one arriving over
    HTTP.
    """
    with pytest.raises(ValueError, match="not a note this project can name"):
        reference(low="C4", high="B9")


def test_a_reference_is_asserted_and_says_so_without_being_asked() -> None:
    assert reference().source is RangeSource.ASSERTED


# --- Overlap ----------------------------------------------------------------


def test_a_song_inside_the_range_overlaps_completely() -> None:
    fit = compute_fit(note_range("C3", "C6"), note_range("C4", "C5"))
    assert fit.overlap_note_count == 13
    assert fit.reference_note_count == 13
    assert fit.percent_of_reference_range == pytest.approx(100.0)
    assert fit.semitones_above_top_note == 0
    assert fit.semitones_below_bottom_note == 0


def test_identical_ranges_overlap_completely_with_no_off_by_one_at_either_end() -> None:
    fit = compute_fit(note_range("C4", "C5"), note_range("C4", "C5"))
    assert fit.overlap_note_count == fit.reference_note_count == 13
    assert fit.percent_of_reference_range == pytest.approx(100.0)
    assert (fit.semitones_above_top_note, fit.semitones_below_bottom_note) == (0, 0)


def test_a_song_entirely_above_the_range_overlaps_by_nothing() -> None:
    fit = compute_fit(note_range("C3", "C4"), note_range("C5", "C6"))
    assert fit.overlap_note_count == 0
    assert fit.percent_of_reference_range == pytest.approx(0.0)
    assert fit.semitones_above_top_note == 24
    assert fit.semitones_below_bottom_note == 0


def test_a_song_entirely_below_the_range_reports_the_gap_at_the_bottom() -> None:
    fit = compute_fit(note_range("C5", "C6"), note_range("C3", "C4"))
    assert fit.overlap_note_count == 0
    assert fit.semitones_above_top_note == 0
    assert fit.semitones_below_bottom_note == 24


def test_ranges_sharing_exactly_one_note_overlap_by_one_note() -> None:
    """The inclusive boundary, at its smallest.

    Counted as a width this would be zero, which would read as "no overlap"
    about two ranges that share a note. §3A's convention says otherwise and this
    is where it bites.
    """
    fit = compute_fit(note_range("C3", "C4"), note_range("C4", "C5"))
    assert fit.overlap_note_count == 1
    assert fit.semitones_above_top_note == 12


def test_a_song_one_semitone_above_at_the_top_reports_one_not_zero_and_not_two() -> None:
    fit = compute_fit(note_range("C4", "C5"), note_range("C4", "C#5"))
    assert fit.semitones_above_top_note == 1
    assert fit.overlap_note_count == 13
    assert fit.reference_note_count == 14


def test_a_song_of_one_note_never_divides_by_zero() -> None:
    """A width of zero is a real input; a share over widths could not survive it."""
    inside = compute_fit(note_range("C4", "C5"), note_range("A4", "A4"))
    outside = compute_fit(note_range("C4", "C5"), note_range("A6", "A6"))
    assert inside.percent_of_reference_range == pytest.approx(100.0)
    assert outside.percent_of_reference_range == pytest.approx(0.0)


def test_the_share_is_of_the_song_not_of_the_singer() -> None:
    """Half of a two-octave song inside a wide range is still half of the song."""
    fit = compute_fit(note_range("C2", "C5"), note_range("C4", "C6"))
    assert fit.reference_note_count == 25
    assert fit.overlap_note_count == 13
    assert fit.percent_of_reference_range == pytest.approx(52.0)


# --- Transposition ----------------------------------------------------------


def test_a_song_that_already_fits_is_not_moved() -> None:
    shift = compute_transposition(note_range("C3", "C6"), note_range("C4", "C5"), None)
    assert shift.possible is True
    assert shift.semitones == 0
    assert (shift.resulting_lowest_note, shift.resulting_highest_note) == ("C4", "C5")


def test_a_song_above_the_range_comes_down_by_exactly_what_it_is_out_by() -> None:
    """The song's top is an octave above the singer's, so it comes down an octave.

    Not further: the window runs from -24 to -12 and the rule takes the end
    nearest zero, which is the smallest move that puts the top note in reach.
    """
    shift = compute_transposition(note_range("C3", "C5"), note_range("C5", "C6"), None)
    assert shift.possible is True
    assert (shift.lowest_workable_semitones, shift.highest_workable_semitones) == (-24, -12)
    assert shift.semitones == -12
    assert (shift.resulting_lowest_note, shift.resulting_highest_note) == ("C4", "C5")


def test_a_song_below_the_range_goes_up_by_what_its_bottom_was_out_by() -> None:
    shift = compute_transposition(note_range("C4", "C6"), note_range("C3", "C4"), None)
    assert (shift.lowest_workable_semitones, shift.highest_workable_semitones) == (12, 24)
    assert shift.semitones == 12
    assert (shift.resulting_lowest_note, shift.resulting_highest_note) == ("C4", "C5")


def test_identical_ranges_leave_exactly_one_workable_shift_and_it_is_zero() -> None:
    shift = compute_transposition(note_range("C4", "C5"), note_range("C4", "C5"), None)
    assert (shift.lowest_workable_semitones, shift.highest_workable_semitones) == (0, 0)
    assert shift.semitones == 0


def test_a_song_reaching_exactly_the_top_note_fits() -> None:
    """§3A question 6, at the top. Inclusive: the note was sung, so it is in the range."""
    shift = compute_transposition(note_range("C4", "C5"), note_range("G4", "C5"), None)
    assert shift.possible is True
    assert shift.semitones == 0
    assert shift.highest_workable_semitones == 0


def test_a_song_reaching_exactly_the_bottom_note_fits() -> None:
    shift = compute_transposition(note_range("C4", "C5"), note_range("C4", "G4"), None)
    assert shift.possible is True
    assert shift.semitones == 0
    assert shift.lowest_workable_semitones == 0


def test_the_recommended_shift_is_the_smallest_one_that_works() -> None:
    """A window with several workable values, and the rule §3A chose.

    A singer covering C3-C6 against a song covering C5-C6: shifts from -24 up to
    0 all fit, and the recommendation is 0 — the least change to the song, not
    the one that centres it.
    """
    shift = compute_transposition(note_range("C3", "C6"), note_range("C5", "C6"), None)
    assert (shift.lowest_workable_semitones, shift.highest_workable_semitones) == (-24, 0)
    assert shift.semitones == 0


def test_when_zero_does_not_fit_the_nearest_end_of_the_window_is_chosen() -> None:
    shift = compute_transposition(note_range("C3", "G3"), note_range("C5", "E5"), None)
    assert (shift.lowest_workable_semitones, shift.highest_workable_semitones) == (-24, -21)
    assert shift.semitones == -21
    assert (shift.resulting_lowest_note, shift.resulting_highest_note) == ("D#3", "G3")


def test_a_song_wider_than_the_range_does_not_fit_and_is_not_guessed_at() -> None:
    shift = compute_transposition(note_range("C4", "C5"), note_range("C3", "C6"), None)
    assert shift.possible is False
    assert shift.shortfall_semitones == 24
    assert shift.semitones is None
    assert shift.lowest_workable_semitones is None
    assert shift.highest_workable_semitones is None
    assert shift.resulting_lowest_note is None
    assert shift.resulting_key is None


def test_a_song_one_semitone_too_wide_reports_a_shortfall_of_one() -> None:
    shift = compute_transposition(note_range("C4", "C5"), note_range("C4", "C#5"), None)
    assert shift.possible is False
    assert shift.shortfall_semitones == 1


# --- Keys -------------------------------------------------------------------


def test_a_transposed_key_keeps_its_mode_and_moves_its_tonic() -> None:
    moved = transpose_key(ReferenceKey(tonic="D", mode=KeyMode.MAJOR), -3)
    assert moved == ReferenceKey(tonic="B", mode=KeyMode.MAJOR)


def test_a_transposed_key_wraps_at_the_octave() -> None:
    assert transpose_key(ReferenceKey(tonic="A", mode=KeyMode.MINOR), 5) == ReferenceKey(
        tonic="D", mode=KeyMode.MINOR
    )
    assert transpose_key(ReferenceKey(tonic="C", mode=KeyMode.MAJOR), -1) == ReferenceKey(
        tonic="B", mode=KeyMode.MAJOR
    )


def test_a_transposed_key_is_spelled_with_sharps() -> None:
    """C down one is D-flat by one spelling and ``C#`` by this project's."""
    moved = transpose_key(ReferenceKey(tonic="D", mode=KeyMode.MAJOR), -1)
    assert moved is not None
    assert moved.tonic == "C#"


def test_no_key_in_no_key_out() -> None:
    assert transpose_key(None, 7) is None


def test_a_song_with_no_key_still_transposes() -> None:
    """The shift never needed a key. Only naming the result does."""
    shift = compute_transposition(note_range("C3", "C4"), note_range("C5", "C6"), None)
    assert shift.possible is True
    assert shift.resulting_key is None


# --- Caveats ----------------------------------------------------------------


def test_every_result_carries_the_three_standing_caveats() -> None:
    caveats = detect_caveats(note_range("C3", "C6"), voiced_ratio=AMPLE_VOICED_RATIO)
    assert set(standing_caveats()) <= set(caveats)


def test_a_recording_with_almost_no_pitch_says_so() -> None:
    caveats = detect_caveats(note_range("C3", "C6"), voiced_ratio=0.01)
    assert CompatibilityCaveat.LITTLE_PITCHED_SIGNAL in caveats


def test_a_range_narrower_than_an_octave_says_so() -> None:
    caveats = detect_caveats(note_range("C4", "G4"), voiced_ratio=AMPLE_VOICED_RATIO)
    assert CompatibilityCaveat.NARROW_DETECTED_RANGE in caveats


def test_an_octave_is_not_narrow() -> None:
    """The boundary of a presentational threshold, pinned so it cannot drift."""
    caveats = detect_caveats(note_range("C4", "C5"), voiced_ratio=AMPLE_VOICED_RATIO)
    assert CompatibilityCaveat.NARROW_DETECTED_RANGE not in caveats


# --- The recording's side ---------------------------------------------------


def detected(low: str, high: str) -> VocalRange:
    low_hz = midi_to_frequency(midi_for_note_name(low))
    high_hz = midi_to_frequency(midi_for_note_name(high))
    assert low_hz is not None and high_hz is not None
    low_midi = midi_for_note_name(low)
    high_midi = midi_for_note_name(high)
    assert low_midi is not None and high_midi is not None
    return VocalRange(
        lowest_frequency_hz=low_hz,
        highest_frequency_hz=high_hz,
        lowest_note=low,
        highest_note=high,
        semitone_span=high_midi - low_midi,
    )


def test_a_detected_range_becomes_a_note_range_that_says_it_was_measured() -> None:
    measured = recording_range(detected("G2", "C5"))
    assert measured is not None
    assert measured.source is RangeSource.MEASURED
    assert (measured.lowest_midi, measured.highest_midi) == (43, 72)
    assert measured.semitone_span == 29
    assert measured.note_count == 30


def test_a_reference_range_says_it_was_asserted() -> None:
    assert reference_range(reference()).source is RangeSource.ASSERTED


def test_the_span_here_is_between_notes_not_between_frequencies() -> None:
    """The one place this module's arithmetic and ``VocalRange`` can disagree.

    ``VocalRange.semitone_span`` rounds the interval between two measured
    frequencies; this module rounds each end to a note first, because the other
    side of the comparison is two typed note names and there is nothing else to
    compare them on. A pair straddling a boundary lands a semitone apart, and
    both numbers are right about different questions — see ``fit.py``.
    """
    straddling = VocalRange(
        lowest_frequency_hz=98.6,  # a shade above G2, so G2 by name
        highest_frequency_hz=196.6,  # a shade above G3, and 12.0 semitones up
        lowest_note="G2",
        highest_note="G3",
        semitone_span=12,
    )
    measured = recording_range(straddling)
    assert measured is not None
    assert measured.semitone_span == 12

    rounding_apart = VocalRange(
        lowest_frequency_hz=98.0,  # G2 by name, a shade flat
        highest_frequency_hz=207.0,  # G#3 by name, a shade sharp
        lowest_note="G2",
        highest_note="G#3",
        semitone_span=12,  # round(12.9) would be 13; a real analysis computes it
    )
    apart = recording_range(rounding_apart)
    assert apart is not None
    assert apart.semitone_span == 13


# --- The whole thing --------------------------------------------------------


def test_a_full_comparison_carries_both_ranges_the_fit_and_the_shift() -> None:
    result = compare(
        note_range("C3", "C6"),
        reference(low="E4", high="A5", key=ReferenceKey(tonic="D", mode=KeyMode.MAJOR)),
        voiced_ratio=AMPLE_VOICED_RATIO,
    )
    assert result.comparable is True
    assert result.recording_status is RecordingSideStatus.READY
    assert result.recording_range is not None and result.reference_range is not None
    assert result.fit is not None and result.transposition is not None
    assert result.transposition.semitones == 0
    assert result.transposition.resulting_key == ReferenceKey(tonic="D", mode=KeyMode.MAJOR)
    assert set(standing_caveats()) <= set(result.caveats)


def test_the_two_sides_of_a_result_are_labelled_by_where_their_numbers_came_from() -> None:
    """The distinction §3A calls the cost of Option D, in the payload itself."""
    result = compare(note_range("C3", "C6"), reference(), voiced_ratio=AMPLE_VOICED_RATIO)
    assert result.recording_range is not None and result.reference_range is not None
    assert result.recording_range.source is RangeSource.MEASURED
    assert result.reference_range.source is RangeSource.ASSERTED


def test_no_field_anywhere_in_a_result_could_hold_a_compatibility_score() -> None:
    """§3A question 4, enforced by the shape rather than by a convention.

    Every numeric field in the result names a unit that makes it a component:
    a count of notes, a share *of the reference range*, a distance in semitones.
    A field called ``score``, ``rating``, ``percent`` or ``overall`` would be one
    somebody could fill with a weighted composite, and there is none.
    """
    result = compare(note_range("C3", "C6"), reference(), voiced_ratio=AMPLE_VOICED_RATIO)
    payload = result.model_dump()
    flattened: list[str] = []

    def walk(value: object, prefix: str = "") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                flattened.append(str(name))
                walk(child, f"{prefix}.{name}")

    walk(payload)
    for forbidden in ("score", "rating", "overall", "grade", "compatibility_percent"):
        assert forbidden not in flattened


def test_an_incomparable_result_may_not_carry_half_an_answer() -> None:
    from app.services.compatibility.models import SongCompatibility

    with pytest.raises(ValueError, match="carries no measurements"):
        SongCompatibility(
            comparable=False,
            recording_status=RecordingSideStatus.ANALYSIS_MISSING,
            fit=compute_fit(note_range("C3", "C6"), note_range("C4", "C5")),
        )
