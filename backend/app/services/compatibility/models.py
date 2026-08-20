"""The song-compatibility domain.

Phase 9, under the input model chosen in
``docs/phase-9-specification.md`` §3A: **a reference song is metadata somebody
typed**, never audio this system measured. Every type here is shaped by that one
fact and by two rules taken from it.

**A typed number and a measured number are different things, and the type says
which.** :class:`RangeSource` travels with the reference wherever it goes, so a
serialiser cannot forget it and a UI cannot render the two alike by accident.
Under this input model it has one value; it is a field rather than a hardcoded
string because the day a measured reference exists, the payload should gain a
*value*, not a key.

**There is no field that could hold a compatibility score**, and the absence is
the guarantee — the same technique ``AudioFeedback`` and ``RecordingComparison``
use. §3A settled that a composite would have to weight incommensurable
quantities that no measurement in this repository sets weights for. What is
reported instead is the components, each with its unit in its name.

Nothing here reads a provider, a file, a socket or a clock. It is arithmetic
over two closed intervals of semitones.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.audio_analysis.models import KeyMode
from app.services.audio_analysis.pitch import midi_for_note_name

#: Scientific pitch notation as this project writes it, and as
#: ``audio_analysis/models.py`` validates it. Repeated rather than imported
#: because it is a private name there, and because a reference's notes are
#: validated at the edge of the system where a typed value arrives.
NOTE_PATTERN: Final = r"\A[A-G]#?-?[0-9]\z"
PITCH_CLASS_PATTERN: Final = r"\A[A-G]#?\z"

#: Bounds on what somebody may type. Long enough for real titles, short enough
#: that a text column is not an upload channel.
MAX_TITLE_LENGTH: Final = 200
MAX_ARTIST_LENGTH: Final = 200


def utc_now() -> datetime:
    return datetime.now(UTC)


class RangeSource(StrEnum):
    """Where a range's two numbers came from.

    Not decoration. It is the difference between a number this system measured
    from audio and a number a person typed into a form, and it decides how the
    result may be presented: an asserted range makes every figure derived from
    it an arithmetic consequence of an unverified input.
    """

    #: Derived from a recording by ``services/audio_analysis/``.
    MEASURED = "measured"
    #: Supplied by whoever created the reference. Never checked against audio.
    ASSERTED = "asserted"


class ReferenceKey(BaseModel):
    """The key a song is published in, as asserted by whoever typed it.

    Optional throughout: the transposition arithmetic does not need a key and
    never has. A key buys one thing — naming the result "in B major" instead of
    only "down three semitones" — and nothing else depends on it.
    """

    model_config = ConfigDict(frozen=True)

    tonic: str = Field(pattern=PITCH_CLASS_PATTERN)
    mode: KeyMode


class SongReference(BaseModel):
    """A song the caller wants to be compared against, as they described it.

    **Nothing here was measured.** The two notes bound a range somebody typed,
    and the key is what they said it was. The product's central rule — a number
    shown is a number measured — is not satisfied by this record, which is why
    :attr:`source` exists and why it is never omitted downstream.
    """

    model_config = ConfigDict(frozen=True)

    reference_id: str = Field(pattern=r"\A[0-9a-f]{32}\z")
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    #: Optional because a warm-up exercise or a scale has no artist, and
    #: requiring one would make the field a lie for those.
    artist: str | None = Field(default=None, min_length=1, max_length=MAX_ARTIST_LENGTH)
    lowest_note: str = Field(pattern=NOTE_PATTERN)
    highest_note: str = Field(pattern=NOTE_PATTERN)
    key: ReferenceKey | None = None
    created_at: datetime = Field(default_factory=utc_now)
    #: Constant under the chosen input model, and carried anyway. See the module
    #: docstring for why this is a field rather than a literal in a serialiser.
    source: RangeSource = RangeSource.ASSERTED

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        """The two notes must name a range, and both must be real MIDI notes.

        The pattern above admits ``B9``, which is not a MIDI note; the parser is
        what actually decides. Checking here rather than in the route means a
        row read back from storage is held to the same rule as one arriving
        over HTTP.
        """
        low = midi_for_note_name(self.lowest_note)
        high = midi_for_note_name(self.highest_note)
        if low is None:
            raise ValueError("lowest note is not a note this project can name")
        if high is None:
            raise ValueError("highest note is not a note this project can name")
        if high < low:
            raise ValueError("highest note is below the lowest")
        return self

    @property
    def lowest_midi(self) -> int:
        """Guaranteed by the validator, so callers need no ``None`` branch."""
        midi = midi_for_note_name(self.lowest_note)
        assert midi is not None  # enforced by _check_order
        return midi

    @property
    def highest_midi(self) -> int:
        midi = midi_for_note_name(self.highest_note)
        assert midi is not None  # enforced by _check_order
        return midi


class NoteRange(BaseModel):
    """One side's range, in the two forms the arithmetic and the reader need.

    Both sides of a compatibility result are expressed with this type, so the
    only thing distinguishing the singer's range from the song's is
    :attr:`source` — which is exactly the distinction that must never be lost.
    """

    model_config = ConfigDict(frozen=True)

    lowest_note: str = Field(pattern=NOTE_PATTERN)
    highest_note: str = Field(pattern=NOTE_PATTERN)
    lowest_midi: int = Field(ge=0, le=127)
    highest_midi: int = Field(ge=0, le=127)
    #: Whole semitones between the extremes — a *width*, and 0 for a range of
    #: one note. The same convention ``VocalRange.semitone_span`` already uses.
    semitone_span: int = Field(ge=0)
    source: RangeSource

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.highest_midi < self.lowest_midi:
            raise ValueError("highest note is below the lowest")
        if self.semitone_span != self.highest_midi - self.lowest_midi:
            raise ValueError("span must be the distance between the two notes")
        return self

    @property
    def note_count(self) -> int:
        """Semitone positions in the range, counting both ends.

        A *count*, not a width: C4–C5 spans 12 semitones and contains 13 notes.
        The two live side by side in a result and are named apart for that
        reason — see :class:`RangeFit`.
        """
        return self.highest_midi - self.lowest_midi + 1


class RangeFit(BaseModel):
    """How much of the song's range falls inside the singer's, and by how much it misses.

    **Two units appear here and they are not interchangeable.** A *count* is a
    number of semitone positions, both ends included, and answers "how many of
    the song's notes can you reach". A figure in *semitones* is a distance, and
    answers "how far out". A song running C4–C5 has a count of 13 and a span of
    12; a top note one semitone above yours is a distance of 1. Every field
    below says in its name which it is.

    The share is taken over counts rather than widths for one reason: a song
    whose range is a single note has a width of zero, and a percentage over
    widths would have to divide by it. There is no such degenerate case over
    counts, so there is no special case in the code and none in the reading.
    """

    model_config = ConfigDict(frozen=True)

    #: Semitone positions the two ranges share. Zero when they are disjoint.
    overlap_note_count: int = Field(ge=0)
    #: Semitone positions the song spans. Always at least 1.
    reference_note_count: int = Field(ge=1)
    #: ``overlap_note_count / reference_note_count``, as a percentage.
    percent_of_reference_range: float = Field(ge=0, le=100)
    #: How far the song's top note sits **above** the singer's. 0 when inside.
    semitones_above_top_note: int = Field(ge=0)
    #: How far the song's bottom note sits **below** the singer's. 0 when inside.
    semitones_below_bottom_note: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.overlap_note_count > self.reference_note_count:
            raise ValueError("a song cannot overlap by more notes than it contains")
        return self


class Transposition(BaseModel):
    """The shift that brings the song inside the singer's range, if one exists.

    **Arithmetic, not musical advice.** It says a shift exists and how big it
    is. It does not say the result is singable, that it should be performed that
    way, or that the singer will find it comfortable — register transitions,
    breath demands, vowel placement and the arrangement's own constraints are
    outside everything this system measures.

    When more than one shift works they form a contiguous run of integers, and
    :attr:`semitones` is the one nearest zero — the least change to the song.
    ``docs/phase-9-specification.md`` §3A records why that rule rather than
    "centre it in the range": centring would claim that the middle of a detected
    range is the comfortable part of a voice, and nothing here measures comfort.

    When no shift works, there is **no best-effort suggestion**. The song is
    simply wider than the range, and :attr:`shortfall_semitones` says by how
    much.
    """

    model_config = ConfigDict(frozen=True)

    possible: bool
    #: The recommended shift, in semitones. Negative is down. ``None`` exactly
    #: when no shift fits — never 0 standing in for "cannot".
    semitones: int | None = None
    #: The workable shifts, inclusive, when there are any.
    lowest_workable_semitones: int | None = None
    highest_workable_semitones: int | None = None
    #: How many semitones wider the song is than the detected range. Set exactly
    #: when no shift fits.
    shortfall_semitones: int | None = Field(default=None, ge=1)
    #: Where the song would sit after the recommended shift.
    resulting_lowest_note: str | None = Field(default=None, pattern=NOTE_PATTERN)
    resulting_highest_note: str | None = Field(default=None, pattern=NOTE_PATTERN)
    #: The key it would then be in — only when the reference carried one, and
    #: named with sharps, because that is how this project spells pitch classes.
    resulting_key: ReferenceKey | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """A possible shift and an impossible one have disjoint fields.

        Enforced in the type so no branch of the service can produce a half
        answer — a shortfall next to a recommendation, or a recommendation with
        nowhere for the song to land.
        """
        if self.possible:
            if self.semitones is None:
                raise ValueError("a possible transposition must name its shift")
            if self.lowest_workable_semitones is None or self.highest_workable_semitones is None:
                raise ValueError("a possible transposition must name its window")
            if self.highest_workable_semitones < self.lowest_workable_semitones:
                raise ValueError("the window ends below where it starts")
            if not (
                self.lowest_workable_semitones <= self.semitones <= self.highest_workable_semitones
            ):
                raise ValueError("the recommended shift must be inside the window")
            if self.resulting_lowest_note is None or self.resulting_highest_note is None:
                raise ValueError("a possible transposition must say where the song lands")
            if self.shortfall_semitones is not None:
                raise ValueError("a shift that fits has no shortfall")
        else:
            if self.shortfall_semitones is None:
                raise ValueError("an impossible transposition must say by how much it misses")
            if self.semitones is not None:
                raise ValueError("no shift fits, so none may be recommended")
            if self.lowest_workable_semitones is not None or (
                self.highest_workable_semitones is not None
            ):
                raise ValueError("no shift fits, so there is no window")
            if self.resulting_lowest_note is not None or self.resulting_highest_note is not None:
                raise ValueError("no shift fits, so the song lands nowhere")
            if self.resulting_key is not None:
                raise ValueError("no shift fits, so there is no resulting key")
        return self


class CompatibilityCaveat(StrEnum):
    """What a reader must know to read the numbers correctly.

    **Unlike ``ComparisonCaveat``, the first three are not conditional.** They
    are properties of the method rather than of the inputs, they are true of
    every result this endpoint will ever return, and
    ``docs/phase-9-specification.md`` §10 requires them on screen rather than
    only in the documentation. Emitting them always is what makes "the UI shows
    them" checkable by a test instead of a matter of someone remembering.
    """

    #: The song's two notes were typed, not measured. Always true under the
    #: chosen input model.
    REFERENCE_RANGE_ASSERTED = "reference_range_asserted"
    #: The singer's range is what one recording contained — bounded by what was
    #: performed, by the microphone and by the room. Never a physiological
    #: maximum.
    DETECTED_RANGE_IS_THIS_RECORDING = "detected_range_is_this_recording"
    #: Range overlap is not a statement about whether somebody can sing a song.
    #: Tessitura, breath, register transitions and technique are not measured.
    NOT_A_STATEMENT_OF_ABILITY = "not_a_statement_of_ability"
    #: Conditional: very little of the recording carried a pitch, so the range
    #: it produced rests on few frames.
    LITTLE_PITCHED_SIGNAL = "little_pitched_signal"
    #: Conditional: the detected range is narrower than an octave, which is
    #: usually a sign of a short take rather than of a narrow voice.
    NARROW_DETECTED_RANGE = "narrow_detected_range"


#: Below this, a detected range is narrow enough that comparing a song to it
#: says more about the take than about the singer. An octave: the interval a
#: scale exercise covers, and the smallest range in which the word "range"
#: means much. A presentational threshold — it adds a sentence and changes no
#: number.
NARROW_RANGE_SEMITONES: Final = 12


class RecordingSideStatus(StrEnum):
    """Whether the recording side can take part, and if not, why.

    Deliberately the same values as ``comparison.models.SideStatus``, and
    deliberately a separate type: they answer the same question about the same
    thing, but a compatibility result has one recording rather than two, and
    coupling the two features so that a value added for one appears in the
    other's contract would be worse than the duplication.
    """

    READY = "ready"
    #: Unknown to this owner — the same answer for "never existed" and "belongs
    #: to somebody else", for the reason the comparison service gives.
    NOT_FOUND = "not_found"
    ANALYSIS_MISSING = "analysis_missing"
    ANALYSIS_IN_PROGRESS = "analysis_in_progress"
    ANALYSIS_FAILED = "analysis_failed"
    INSUFFICIENT_PITCH_SIGNAL = "insufficient_pitch_signal"


class ReferenceSideStatus(StrEnum):
    """Whether the reference side can take part.

    Two values, and the shortness is a consequence of the input model: a
    reference is a range or it does not exist, because the range is required to
    create one and is validated before the row is written. There is no
    ``NO_RANGE`` here — under an input model that measures a reference there
    would be, which is why this is an enum rather than a boolean.
    """

    READY = "ready"
    NOT_FOUND = "not_found"


class SongCompatibility(BaseModel):
    """A recording's detected range placed against a song's asserted one.

    **A refusal is one of these too.** No analysis, a failed one, no reliable
    pitch, an unknown reference — each is a successful answer with
    :attr:`comparable` false and a per-side status saying which side, because a
    client renders each differently and an HTTP error would collapse them into
    one. Only an id that is not the caller's is a 404.

    An incomparable result carries **no** fit and **no** transposition. There is
    no half answer for a client to render as if it were whole.
    """

    model_config = ConfigDict(frozen=True)

    comparable: bool
    recording_status: RecordingSideStatus
    reference_status: ReferenceSideStatus
    recording_range: NoteRange | None = None
    reference_range: NoteRange | None = None
    reference: SongReference | None = None
    fit: RangeFit | None = None
    transposition: Transposition | None = None
    caveats: tuple[CompatibilityCaveat, ...] = ()

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.comparable:
            if self.recording_status is not RecordingSideStatus.READY:
                raise ValueError("a comparable result needs a ready recording")
            if self.reference_status is not ReferenceSideStatus.READY:
                raise ValueError("a comparable result needs a ready reference")
            if self.recording_range is None or self.reference_range is None:
                raise ValueError("a comparable result needs both ranges")
            if self.fit is None or self.transposition is None:
                raise ValueError("a comparable result carries the fit and the transposition")
        elif self.fit is not None or self.transposition is not None:
            raise ValueError("an incomparable result carries no measurements")
        return self
