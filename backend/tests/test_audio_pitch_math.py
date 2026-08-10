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
    midi_to_frequency,
    midi_to_note_name,
    nearest_midi,
    note_name_for_frequency,
    semitones_between,
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


def test_note_names_use_sharps_only() -> None:
    """Enharmonic spelling needs a key, which a lone frequency cannot supply."""
    names = {midi_to_note_name(midi) for midi in range(60, 72)}
    assert names == {"C4", "C#4", "D4", "D#4", "E4", "F4", "F#4", "G4", "G#4", "A4", "A#4", "B4"}
    assert not any("b" in (name or "") for name in names)
