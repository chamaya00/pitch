"""The compatibility arithmetic. Two closed intervals of semitones, and nothing else.

Pure functions over the domain types. No repository, no settings, no clock, no
provider — and the absence of a provider anywhere in this module's imports is
what makes it impossible for a model to produce one of these numbers, in the way
``comparison/compare.py`` and ``progress/series.py`` already are.

**Everything here happens on notes, not on frequencies**, and that is a choice
with a visible consequence. A reference's range is two note *names* somebody
typed; there is no frequency behind them and never will be. So the recording's
range is rounded to its two nearest notes before either is used, and every
figure below is an integer count of semitones.

The consequence: :attr:`NoteRange.semitone_span` computed here can differ by one
from the ``semitone_span`` on the same recording's ``VocalRange``. They are
different measurements and both are right. ``VocalRange`` rounds the interval
between two measured *frequencies* — 100.0 Hz to 200.9 Hz is 12.1 semitones,
which rounds to 12. This module rounds each end to a note first — G#2 and G3 —
and the distance between those two names is 12 as well, but a pair a few cents
either side of a boundary lands on 13. Comparing against typed notes can only be
done on notes; the recording's own span stays what it is, because between
frequencies is the better description of what the audio contained.
"""

from typing import Final

from app.services.audio_analysis.models import VocalRange
from app.services.audio_analysis.pitch import NOTE_NAMES, midi_for_note_name, note_name_for_midi

# One threshold, one definition. A recording that carried very little pitch is
# the same warning here as in a comparison, and defining it twice would let the
# two drift into disagreeing about the same recording.
from app.services.comparison.models import LOW_VOICED_RATIO
from app.services.compatibility.models import (
    NARROW_RANGE_SEMITONES,
    CompatibilityCaveat,
    NoteRange,
    RangeFit,
    RangeSource,
    RecordingSideStatus,
    ReferenceKey,
    SongCompatibility,
    SongReference,
    Transposition,
)

_TO_PERCENT: Final = 100.0
_SEMITONES_PER_OCTAVE: Final = 12


def recording_range(detected: VocalRange) -> NoteRange | None:
    """The recording's detected range, as two notes and their MIDI numbers.

    ``None`` when either extreme cannot be named as a MIDI note. That is
    unreachable from a measured range — the analyzer's search band sits well
    inside 0–127 — but it is returned rather than asserted away, because the
    caller has a state for "this side cannot take part" and does not need a new
    kind of failure.
    """
    low = midi_for_note_name(detected.lowest_note)
    high = midi_for_note_name(detected.highest_note)
    if low is None or high is None or high < low:
        return None
    return NoteRange(
        lowest_note=detected.lowest_note,
        highest_note=detected.highest_note,
        lowest_midi=low,
        highest_midi=high,
        semitone_span=high - low,
        source=RangeSource.MEASURED,
    )


def reference_range(reference: SongReference) -> NoteRange:
    """The song's asserted range. Total, because the reference validated it."""
    return NoteRange(
        lowest_note=reference.lowest_note,
        highest_note=reference.highest_note,
        lowest_midi=reference.lowest_midi,
        highest_midi=reference.highest_midi,
        semitone_span=reference.highest_midi - reference.lowest_midi,
        source=reference.source,
    )


def compute_fit(singer: NoteRange, reference: NoteRange) -> RangeFit:
    """How much of the song sits inside the singer's range, and how far it misses.

    The intersection of two closed intervals, counted inclusively: ranges that
    share exactly one note overlap by one note, not by zero. That inclusiveness
    is the same convention §3A settled for the transposition boundary, applied
    to the same two intervals, and it would be incoherent to have one without
    the other.
    """
    lowest_shared = max(singer.lowest_midi, reference.lowest_midi)
    highest_shared = min(singer.highest_midi, reference.highest_midi)
    overlap = max(0, highest_shared - lowest_shared + 1)

    return RangeFit(
        overlap_note_count=overlap,
        reference_note_count=reference.note_count,
        percent_of_reference_range=overlap / reference.note_count * _TO_PERCENT,
        semitones_above_top_note=max(0, reference.highest_midi - singer.highest_midi),
        semitones_below_bottom_note=max(0, singer.lowest_midi - reference.lowest_midi),
    )


def compute_transposition(
    singer: NoteRange, reference: NoteRange, key: ReferenceKey | None
) -> Transposition:
    """The shift, in semitones, that brings the song inside the singer's range.

    A shift ``k`` fits when the shifted song sits inside the range at **both**
    ends — ``reference.lowest + k >= singer.lowest`` and
    ``reference.highest + k <= singer.highest``. Rearranged, that is a single
    closed interval of workable shifts::

        lowest_workable  = singer.lowest_midi  - reference.lowest_midi
        highest_workable = singer.highest_midi - reference.highest_midi

    which is non-empty exactly when the song's span is no wider than the
    singer's. Both comparisons are inclusive, which is §3A question 6: a song
    that exactly reaches the top note fits, because that note is in the range
    for the reason that it was sung.

    When the interval is empty there is no shift and no suggestion of one. The
    shortfall is how many semitones wider the song is, which is the same number
    as how far the interval's ends have crossed.
    """
    lowest_workable = singer.lowest_midi - reference.lowest_midi
    highest_workable = singer.highest_midi - reference.highest_midi

    if lowest_workable > highest_workable:
        return Transposition(
            possible=False,
            shortfall_semitones=lowest_workable - highest_workable,
        )

    # The workable shifts are contiguous integers, so the one nearest zero is
    # whichever end zero falls outside of — and zero itself when it is inside.
    # Unique by construction, so §3A's rule needs no tie-break.
    recommended = min(max(0, lowest_workable), highest_workable)

    return Transposition(
        possible=True,
        semitones=recommended,
        lowest_workable_semitones=lowest_workable,
        highest_workable_semitones=highest_workable,
        resulting_lowest_note=note_name_for_midi(reference.lowest_midi + recommended),
        resulting_highest_note=note_name_for_midi(reference.highest_midi + recommended),
        resulting_key=transpose_key(key, recommended),
    )


def transpose_key(key: ReferenceKey | None, semitones: int) -> ReferenceKey | None:
    """The same key, moved. ``None`` in, ``None`` out.

    The mode is unchanged: shifting every note of a major scale by a constant
    gives a major scale. Spelled with sharps, because that is how this project
    spells pitch classes everywhere — a shift landing on D-flat is named ``C#``,
    the same convention ``NOTE_NAMES`` states and for the same reason.
    """
    if key is None:
        return None
    tonic = NOTE_NAMES[(NOTE_NAMES.index(key.tonic) + semitones) % _SEMITONES_PER_OCTAVE]
    return ReferenceKey(tonic=tonic, mode=key.mode)


def standing_caveats() -> tuple[CompatibilityCaveat, ...]:
    """The three that are true of every result this feature can produce.

    Not conditional, and not derived from anything: they describe the method.
    Returned from a function rather than written as a constant tuple so that the
    conditional ones below can be appended to them in one place, and so a test
    can assert that no result ever ships without them.
    """
    return (
        CompatibilityCaveat.REFERENCE_RANGE_ASSERTED,
        CompatibilityCaveat.DETECTED_RANGE_IS_THIS_RECORDING,
        CompatibilityCaveat.NOT_A_STATEMENT_OF_ABILITY,
    )


def detect_caveats(singer: NoteRange, *, voiced_ratio: float) -> tuple[CompatibilityCaveat, ...]:
    """The standing three, plus anything the recording itself warns about.

    Both conditional checks read something already measured. Neither claims
    anything about microphone, room, effort or condition, because nothing here
    measures those.
    """
    caveats = list(standing_caveats())

    if voiced_ratio < LOW_VOICED_RATIO:
        caveats.append(CompatibilityCaveat.LITTLE_PITCHED_SIGNAL)

    if singer.semitone_span < NARROW_RANGE_SEMITONES:
        caveats.append(CompatibilityCaveat.NARROW_DETECTED_RANGE)

    return tuple(caveats)


def compare(
    singer: NoteRange,
    reference: SongReference,
    *,
    voiced_ratio: float,
) -> SongCompatibility:
    """Everything above, assembled. The only place a comparable result is built.

    Kept in this module rather than in the service so the whole arithmetic is
    testable without a repository, a database or an owner — which is the one
    property Option D was chosen for.
    """
    song = reference_range(reference)
    return SongCompatibility(
        comparable=True,
        recording_status=RecordingSideStatus.READY,
        recording_range=singer,
        reference_range=song,
        reference=reference,
        fit=compute_fit(singer, song),
        transposition=compute_transposition(singer, song, reference.key),
        caveats=detect_caveats(singer, voiced_ratio=voiced_ratio),
    )
