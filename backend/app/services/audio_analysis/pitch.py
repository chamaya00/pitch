"""Musical conversions: frequency, MIDI, note name, cents.

**This is one half of a contract with the browser.** ``frontend/lib/pitch.ts``
implements the same four formulas in TypeScript, because the live display needs
them at frame rate in the page and shipping Python to a browser to avoid a
duplicated logarithm would be the wrong trade. The implementations are separate
on purpose; the *mathematics* is not, and it is written down once here:

.. code-block:: text

    midi   = 69 + 12 · log2(f / 440)          # fractional, deliberately
    note   = NOTE_NAMES[round(midi) mod 12]
    octave = floor(round(midi) / 12) − 1      # MIDI 60 → C4
    cents  = 100 · (midi − round(midi))       # always within ±50

Reference: A4 = 440 Hz = MIDI 69, twelve-tone equal temperament.

Two properties both implementations must keep:

**MIDI stays fractional until the last moment.** The cents deviation *is* the
distance between the measured pitch and the nearest semitone; rounding before
measuring it would throw the measurement away.

**Anything that cannot be a pitch returns ``None``.** Zero, a negative, a NaN,
an infinity, or a frequency outside the audible pitch range. A frequency that
cannot exist must never become a note name — silence rendering as a confident
C-something is the exact failure this guards against.

Pure arithmetic. No numpy, no audio, no I/O.
"""

import math
from typing import Final

A4_FREQUENCY_HZ: Final = 440.0
A4_MIDI: Final = 69
_SEMITONES_PER_OCTAVE: Final = 12
_CENTS_PER_SEMITONE: Final = 100.0

#: Sharps only. Enharmonic spelling needs a key, which a lone frequency cannot
#: supply — a sung D-flat 4 is reported as ``C#4``.
NOTE_NAMES: Final[tuple[str, ...]] = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

#: Outside this, a value is not a pitch anyone sang or spoke. Wider than the
#: detector's own search range, which is a separate and narrower decision.
MIN_FREQUENCY_HZ: Final = 20.0
MAX_FREQUENCY_HZ: Final = 5000.0


def is_usable_frequency(frequency: float | None) -> bool:
    """Whether ``frequency`` can describe a pitch at all."""
    if frequency is None or isinstance(frequency, bool):
        return False
    if not isinstance(frequency, int | float):
        return False
    if not math.isfinite(frequency):
        return False
    return MIN_FREQUENCY_HZ <= frequency <= MAX_FREQUENCY_HZ


def frequency_to_midi(frequency: float | None) -> float | None:
    """Exact (fractional) MIDI number for a frequency, or ``None``."""
    if not is_usable_frequency(frequency):
        return None
    assert frequency is not None  # narrowed by is_usable_frequency
    return A4_MIDI + _SEMITONES_PER_OCTAVE * math.log2(frequency / A4_FREQUENCY_HZ)


def midi_to_frequency(midi: float | None) -> float | None:
    """Inverse of :func:`frequency_to_midi`. Accepts fractional MIDI."""
    if midi is None or isinstance(midi, bool) or not isinstance(midi, int | float):
        return None
    if not math.isfinite(midi):
        return None
    return float(A4_FREQUENCY_HZ * 2.0 ** ((midi - A4_MIDI) / _SEMITONES_PER_OCTAVE))


def nearest_midi(frequency: float | None) -> int | None:
    """The nearest equal-tempered semitone to a frequency, as a MIDI number."""
    midi = frequency_to_midi(frequency)
    if midi is None:
        return None
    return round(midi)


def note_name_for_midi(midi: int) -> str:
    """Scientific pitch notation for a whole semitone, e.g. 69 → ``A4``.

    **A note's name is a fact about its number**, and this is where that fact
    is stated. Nothing is measured here and nothing can fail: a semitone always
    has a name, so unlike every other function in this module it returns a
    ``str`` rather than ``str | None``.

    That totality is why it exists separately from :func:`midi_to_note_name`,
    which takes a possibly-fractional, possibly-absent pitch and must be able to
    say "that is not a note". Aggregations read the stored ``midi_note`` of a
    frame — an integer the model bounds to 0–127 — and would otherwise have to
    handle a ``None`` that cannot arrive. One arithmetic, two entry points; the
    naming rule is not restated anywhere else in this codebase.
    """
    return f"{NOTE_NAMES[midi % _SEMITONES_PER_OCTAVE]}{midi // _SEMITONES_PER_OCTAVE - 1}"


def midi_to_note_name(midi: float | None) -> str | None:
    """Scientific pitch notation, e.g. 69 → ``A4``. Rounds to the nearest note."""
    if midi is None or isinstance(midi, bool) or not isinstance(midi, int | float):
        return None
    if not math.isfinite(midi):
        return None
    return note_name_for_midi(round(midi))


def cents_from_nearest_note(frequency: float | None) -> float | None:
    """Signed distance to the nearest semitone, in cents. Always within ±50.

    Beyond ±50 the *next* semitone is nearer, so a larger deviation would be
    describing the wrong note.
    """
    midi = frequency_to_midi(frequency)
    if midi is None:
        return None
    return (midi - round(midi)) * _CENTS_PER_SEMITONE


def note_name_for_frequency(frequency: float | None) -> str | None:
    """Note name for a frequency, or ``None`` if it cannot be a pitch."""
    return midi_to_note_name(frequency_to_midi(frequency))


def semitones_between(low_hz: float, high_hz: float) -> float:
    """Interval between two frequencies, in semitones. Fractional."""
    return _SEMITONES_PER_OCTAVE * math.log2(high_hz / low_hz)
