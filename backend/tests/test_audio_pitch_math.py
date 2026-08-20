"""The musical conversions, against known reference values.

These four formulas are a contract shared with ``frontend/lib/pitch.ts``, which
implements the same mathematics in TypeScript for the live display. The two
implementations are separate; the numbers they produce must not be. The same
reference cases are asserted in ``frontend/tests/pitch.test.ts``.

Tolerances throughout — never floating-point equality on a measurement.
"""

import math

import pytest

from app.services.audio_analysis.pitch import (
    A4_FREQUENCY_HZ,
    A4_MIDI,
    MAX_FREQUENCY_HZ,
    MIN_FREQUENCY_HZ,
    cents_from_nearest_note,
    frequency_to_midi,
    is_usable_frequency,
    midi_for_note_name,
    midi_to_frequency,
    midi_to_note_name,
    nearest_midi,
    note_name_for_frequency,
    note_name_for_midi,
    semitones_between,
    semitones_between_notes,
)

#: Cents. Tight enough to catch a wrong formula, loose enough for binary floats.
TOLERANCE_CENTS = 0.01


def test_440_hz_is_a4() -> None:
    assert frequency_to_midi(A4_FREQUENCY_HZ) == pytest.approx(A4_MIDI, abs=1e-9)
    assert nearest_midi(A4_FREQUENCY_HZ) == 69
    assert note_name_for_frequency(A4_FREQUENCY_HZ) == "A4"
    assert cents_from_nearest_note(A4_FREQUENCY_HZ) == pytest.approx(0.0, abs=TOLERANCE_CENTS)


def test_261_626_hz_is_c4() -> None:
    """Middle C. The other reference every implementation is checked against."""
    assert nearest_midi(261.626) == 60
    assert note_name_for_frequency(261.626) == "C4"
    assert cents_from_nearest_note(261.626) == pytest.approx(0.0, abs=0.1)


def test_the_truncated_middle_c_is_a_hair_flat_not_another_note() -> None:
    """261.625 is not 261.6256. It should read as C4, very slightly flat."""
    cents = cents_from_nearest_note(261.625)
    assert cents is not None
    assert -1.0 < cents < 0.0
    assert note_name_for_frequency(261.625) == "C4"


@pytest.mark.parametrize(
    ("frequency", "note"),
    [
        (27.5, "A0"),
        (55.0, "A1"),
        (110.0, "A2"),
        (220.0, "A3"),
        (880.0, "A5"),
        (1760.0, "A6"),
        (32.703, "C1"),
        (523.251, "C5"),
        (2093.0, "C7"),
        (369.994, "F#4"),
    ],
)
def test_known_note_frequencies(frequency: float, note: str) -> None:
    assert note_name_for_frequency(frequency) == note
    cents = cents_from_nearest_note(frequency)
    assert cents is not None
    assert abs(cents) < 1.0


def test_midi_and_frequency_are_inverses() -> None:
    for midi in range(24, 108):
        frequency = midi_to_frequency(midi)
        assert frequency is not None
        assert frequency_to_midi(frequency) == pytest.approx(midi, abs=1e-9)


def test_midi_stays_fractional() -> None:
    """The cents deviation only exists because MIDI is not rounded here."""
    quarter_sharp = A4_FREQUENCY_HZ * 2 ** (0.5 / 12)
    midi = frequency_to_midi(quarter_sharp)
    assert midi is not None
    assert midi == pytest.approx(69.5, abs=1e-9)
    assert midi != round(midi)


def test_cents_never_exceed_half_a_semitone() -> None:
    """Beyond ±50 the next semitone is nearer, so a larger figure is wrong."""
    for offset in [x / 100 for x in range(-49, 50)]:
        frequency = A4_FREQUENCY_HZ * 2 ** (offset / 12)
        cents = cents_from_nearest_note(frequency)
        assert cents is not None
        assert -50.0 <= cents <= 50.0
        assert cents == pytest.approx(offset * 100, abs=TOLERANCE_CENTS)


def test_cents_are_signed_the_way_a_tuner_reads() -> None:
    flat = cents_from_nearest_note(A4_FREQUENCY_HZ * 2 ** (-0.2 / 12))
    sharp = cents_from_nearest_note(A4_FREQUENCY_HZ * 2 ** (0.2 / 12))
    assert flat is not None and flat < 0
    assert sharp is not None and sharp > 0


def test_an_octave_is_twelve_semitones() -> None:
    assert semitones_between(220.0, 440.0) == pytest.approx(12.0, abs=1e-9)
    assert semitones_between(110.0, 880.0) == pytest.approx(36.0, abs=1e-9)
    assert semitones_between(440.0, 440.0) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, -440.0, math.nan, math.inf, -math.inf, None],
)
def test_values_that_cannot_be_a_pitch_produce_nothing(value: float | None) -> None:
    """Silence must never render as a confident note. This is that guarantee."""
    assert is_usable_frequency(value) is False
    assert frequency_to_midi(value) is None
    assert nearest_midi(value) is None
    assert cents_from_nearest_note(value) is None
    assert note_name_for_frequency(value) is None


def test_frequencies_outside_the_pitch_range_produce_nothing() -> None:
    assert note_name_for_frequency(MIN_FREQUENCY_HZ - 0.1) is None
    assert note_name_for_frequency(MAX_FREQUENCY_HZ + 1) is None
    assert note_name_for_frequency(MIN_FREQUENCY_HZ) is not None
    assert note_name_for_frequency(MAX_FREQUENCY_HZ) is not None


def test_booleans_are_not_frequencies() -> None:
    """``True`` is an int in Python. It is not 1 Hz, and it is not a pitch."""
    assert is_usable_frequency(True) is False  # noqa: FBT003
    assert frequency_to_midi(True) is None  # noqa: FBT003


def test_midi_to_note_name_rejects_what_cannot_be_a_note() -> None:
    assert midi_to_note_name(None) is None
    assert midi_to_note_name(math.nan) is None
    assert midi_to_note_name(math.inf) is None
    assert midi_to_frequency(math.nan) is None
    assert midi_to_frequency(None) is None


def test_a_semitone_is_named_the_same_whichever_entry_point_asks() -> None:
    """One naming rule, two entry points, and no room for a third.

    :func:`note_name_for_midi` is what the two aggregations call: they group on
    the stored ``midi_note`` and derive the name rather than reading the one in
    the document, which is why the two must agree for **every** note a stored
    point can hold. If they could differ, a note breakdown would disagree with
    the timeline it was folded from — and the analyzer names every point it
    writes through the other entry point.
    """
    for midi in range(128):
        assert note_name_for_midi(midi) == midi_to_note_name(midi)


def test_naming_a_semitone_cannot_fail() -> None:
    """A semitone always has a name, which is why this one returns a ``str``.

    The distinction from :func:`midi_to_note_name`, which takes a possibly
    fractional and possibly absent pitch and must be able to answer "that is not
    a note". A caller folding validated notes would otherwise have to handle a
    ``None`` that cannot arrive.
    """
    assert note_name_for_midi(0) == "C-1"
    assert note_name_for_midi(60) == "C4"
    assert note_name_for_midi(127) == "G9"


def test_note_names_use_sharps_only() -> None:
    """Enharmonic spelling needs a key, which a lone frequency cannot supply."""
    names = {midi_to_note_name(midi) for midi in range(60, 72)}
    assert names == {"C4", "C#4", "D4", "D#4", "E4", "F4", "F#4", "G4", "G#4", "A4", "A#4", "B4"}
    assert not any("b" in (name or "") for name in names)


# --- Reading a written note back --------------------------------------------


def test_every_midi_note_survives_a_round_trip_through_its_name() -> None:
    """The property that makes :func:`midi_for_note_name` an inverse rather than a parser.

    Phase 9 compares a measured range against two note names somebody typed, so
    a name has to become a number again exactly. Asserted over the whole MIDI
    range rather than on examples: an octave-boundary error would show up on
    twelve notes out of 128 and on none of the obvious ones.
    """
    for midi in range(128):
        assert midi_for_note_name(note_name_for_midi(midi)) == midi


def test_reading_a_note_accepts_what_this_project_writes_and_nothing_more() -> None:
    assert midi_for_note_name("A4") == 69
    assert midi_for_note_name("C-1") == 0
    assert midi_for_note_name("G9") == 127
    # Real notes this project never writes, and things that are not notes.
    assert midi_for_note_name("Db4") is None
    assert midi_for_note_name("H4") is None
    assert midi_for_note_name("C") is None
    assert midi_for_note_name("C10") is None
    assert midi_for_note_name(" A4") is None
    assert midi_for_note_name("") is None


def test_a_well_formed_note_above_the_midi_range_is_refused_not_clamped() -> None:
    """``B9`` is spelled correctly and is not a MIDI note.

    Returning 127 for it would be inventing a value the caller did not ask for —
    and 127 is ``G9``, four semitones away from what they wrote.
    """
    assert midi_for_note_name("B9") is None


def test_the_interval_between_two_written_notes_is_signed() -> None:
    assert semitones_between_notes("C4", "C5") == 12
    assert semitones_between_notes("C5", "C4") == -12
    assert semitones_between_notes("A4", "A4") == 0


def test_an_interval_from_a_note_that_cannot_be_read_is_absent_not_zero() -> None:
    assert semitones_between_notes("C4", "Db5") is None
    assert semitones_between_notes("Bb3", "C5") is None
