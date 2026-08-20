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
import re
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

#: The MIDI range, which is the range of things :func:`note_name_for_midi` can
#: be asked about. Not a musical statement — a note below 0 or above 127 is
#: simply not a MIDI note.
_MIN_MIDI: Final = 0
_MAX_MIDI: Final = 127

#: Scientific pitch notation, as this project writes it. The same shape as
#: ``_NOTE_PATTERN`` in ``models.py``, which validates the strings this parses;
#: the groups are what makes it a parser rather than a second copy of the check.
_NOTE_NAME_PATTERN: Final = re.compile(r"\A(?P<pitch_class>[A-G]#?)(?P<octave>-?[0-9])\Z")


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


def midi_for_note_name(name: str) -> int | None:
    """MIDI number for scientific pitch notation, e.g. ``A4`` → 69.

    The exact inverse of :func:`note_name_for_midi`, and deliberately no wider
    than that. It accepts what this project *writes* — a letter, an optional
    ``#``, and an octave from -1 to 9 — and returns ``None`` for everything
    else, including well-formed music that this project never writes:

    **Flats are not accepted.** ``Db4`` is a real note and ``NOTE_NAMES`` cannot
    produce it, for the reason stated there: enharmonic spelling needs a key,
    and this project spells with sharps everywhere. Accepting a flat would mean
    echoing a value back to the caller under a different name than they sent,
    which is worse than refusing it — and every place a note is chosen in the
    UI is a picker over the names above, so the flat never arrives.

    Out-of-range octaves are refused rather than clamped: ``B9`` parses to 131,
    which is not a MIDI note, and returning 127 for it would be inventing a
    value the caller did not ask for.
    """
    if not isinstance(name, str):
        return None
    match = _NOTE_NAME_PATTERN.match(name)
    if match is None:
        return None
    pitch_class = NOTE_NAMES.index(match.group("pitch_class"))
    octave = int(match.group("octave"))
    midi = (octave + 1) * _SEMITONES_PER_OCTAVE + pitch_class
    return midi if _MIN_MIDI <= midi <= _MAX_MIDI else None


def semitones_between_notes(low: str, high: str) -> int | None:
    """Whole semitones from one written note to another, or ``None``.

    Signed: ``semitones_between_notes("C4", "C5")`` is 12 and the reverse is
    -12. Named apart from :func:`semitones_between`, which takes frequencies and
    returns a fraction, because confusing the two would silently change a
    measurement into an interval between two spellings.
    """
    low_midi = midi_for_note_name(low)
    high_midi = midi_for_note_name(high)
    if low_midi is None or high_midi is None:
        return None
    return high_midi - low_midi
