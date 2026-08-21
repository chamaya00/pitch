"""API representation of song references and of a compatibility result.

Built explicitly from the domain, like every other schema here, so a field added
to the domain cannot reach a response by accident. What that filter removes: no
owner id, no analysis id, no storage path, no settings block.

Three properties of the domain survive into the wire format, because they are
the feature:

* **``source`` is on both ranges, always.** ``measured`` is a number this system
  took from audio; ``asserted`` is a number somebody typed. A client that
  renders the two alike is rendering a claim this product does not make, and the
  field is what makes that checkable rather than a matter of remembering.
* **A count and a distance are different numbers**, and each field's name says
  which it is. ``overlap_note_count`` counts semitone positions inclusively;
  ``semitones_above_top_note`` is a distance.
* **There is no compatibility score, and no field that could hold one.**

A refusal is a ``200`` carrying ``comparable: false`` and the reason.
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.services.compatibility.models import (
    MAX_ARTIST_LENGTH,
    MAX_TITLE_LENGTH,
    NOTE_PATTERN,
    PITCH_CLASS_PATTERN,
    NoteRange,
    RangeFit,
    ReferenceKey,
    SongCompatibility,
    SongReference,
    Transposition,
    validated_range,
)


class ReferenceKeyModel(BaseModel):
    """A key, as asserted or as transposed. Sharps only, like every pitch class here."""

    tonic: str = Field(pattern=PITCH_CLASS_PATTERN, description="Pitch class, e.g. `C#`.")
    mode: str = Field(description="`major` or `minor`. No other modes are estimated here.")

    @classmethod
    def from_domain(cls, key: ReferenceKey) -> "ReferenceKeyModel":
        return cls(tonic=key.tonic, mode=key.mode.value)


class SongReferenceRequest(BaseModel):
    """A song the caller wants to be compared against, as they describe it.

    **Every number in here is asserted, not measured.** Nothing is decoded,
    nothing is analysed, and no audio is involved at any point — see
    `docs/phase-9-specification.md` §3A for why this input model was chosen and
    what it costs.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    artist: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_ARTIST_LENGTH,
        description="Optional: a warm-up or a scale has no artist.",
    )
    lowest_note: str = Field(
        pattern=NOTE_PATTERN,
        description=(
            "Scientific pitch notation with sharps only, e.g. `F#3`. Flats are "
            "not accepted: this project spells every pitch class with sharps, "
            "and echoing a value back under a different name would be worse "
            "than refusing it."
        ),
    )
    highest_note: str = Field(pattern=NOTE_PATTERN, description="As above, and not below it.")
    key: ReferenceKeyModel | None = Field(
        default=None,
        description=(
            "Optional. The transposition arithmetic never needs a key; it buys "
            "one thing, which is naming the result *in B major* rather than "
            "only *down three semitones*."
        ),
    )

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        """The domain's own rule, applied where a bad request becomes a `422`.

        Calling the shared check rather than restating it: a body that would not
        make a valid ``SongReference`` is refused here, with the reason, instead
        of raising inside the route and becoming a `500`.
        """
        validated_range(self.lowest_note, self.highest_note)
        return self


class SongReferenceResponse(BaseModel):
    """A stored reference, as the caller described it."""

    reference_id: str
    title: str
    artist: str | None = None
    lowest_note: str
    highest_note: str
    key: ReferenceKeyModel | None = None
    created_at: datetime
    source: str = Field(
        description=(
            "Always `asserted`. These numbers were typed, not measured, and "
            "every figure derived from them is an arithmetic consequence of an "
            "unverified input."
        )
    )

    @classmethod
    def from_domain(cls, reference: SongReference) -> "SongReferenceResponse":
        return cls(
            reference_id=reference.reference_id,
            title=reference.title,
            artist=reference.artist,
            lowest_note=reference.lowest_note,
            highest_note=reference.highest_note,
            key=None if reference.key is None else ReferenceKeyModel.from_domain(reference.key),
            created_at=reference.created_at,
            source=reference.source.value,
        )


class SongReferenceListResponse(BaseModel):
    """The caller's own references, newest first."""

    count: int = Field(description="How many are in this response, not how many exist.")
    references: list[SongReferenceResponse]

    @classmethod
    def from_domain(cls, references: list[SongReference]) -> "SongReferenceListResponse":
        items = [SongReferenceResponse.from_domain(one) for one in references]
        return cls(count=len(items), references=items)


class NoteRangeResponse(BaseModel):
    """One side's range, and where its two numbers came from."""

    lowest_note: str
    highest_note: str
    #: Whole semitones between the extremes — a width, so a range of one note is
    #: 0. Distinct from the note *counts* in the fit, which include both ends.
    semitone_span: int
    source: str = Field(description="`measured` for the recording, `asserted` for the song.")

    @classmethod
    def from_domain(cls, value: NoteRange) -> "NoteRangeResponse":
        return cls(
            lowest_note=value.lowest_note,
            highest_note=value.highest_note,
            semitone_span=value.semitone_span,
            source=value.source.value,
        )


class RangeFitResponse(BaseModel):
    """How much of the song sits inside the detected range, and by how much it misses."""

    overlap_note_count: int = Field(
        description="Semitone positions the two ranges share, counting both ends."
    )
    reference_note_count: int = Field(description="Semitone positions the song spans. At least 1.")
    percent_of_reference_range: float = Field(
        description="The overlap as a share of the **song's** range, not of the singer's."
    )
    semitones_above_top_note: int = Field(
        description="How far the song's top note sits above yours. 0 when it is inside."
    )
    semitones_below_bottom_note: int = Field(
        description="How far the song's bottom note sits below yours. 0 when it is inside."
    )

    @classmethod
    def from_domain(cls, value: RangeFit) -> "RangeFitResponse":
        return cls(**value.model_dump())


class TranspositionResponse(BaseModel):
    """The shift that brings the song inside the range, if one exists.

    **Arithmetic, not musical advice.** It says a shift exists and how big it is.
    Whether the result is singable depends on register transitions, breath and
    technique, none of which this system measures.
    """

    possible: bool
    semitones: int | None = Field(
        default=None,
        description=(
            "The recommended shift; negative is down. The workable shift "
            "closest to the original, which is the least change to the song. "
            "`null` exactly when no shift fits — never `0` standing in for it."
        ),
    )
    lowest_workable_semitones: int | None = None
    highest_workable_semitones: int | None = None
    shortfall_semitones: int | None = Field(
        default=None,
        description=(
            "How many semitones wider the song is than the detected range. Set "
            "exactly when no shift fits, and offered instead of a best-effort "
            "suggestion, because there is no shift that would work."
        ),
    )
    resulting_lowest_note: str | None = None
    resulting_highest_note: str | None = None
    resulting_key: ReferenceKeyModel | None = Field(
        default=None, description="Only when the reference carried a key."
    )

    @classmethod
    def from_domain(cls, value: Transposition) -> "TranspositionResponse":
        return cls(
            possible=value.possible,
            semitones=value.semitones,
            lowest_workable_semitones=value.lowest_workable_semitones,
            highest_workable_semitones=value.highest_workable_semitones,
            shortfall_semitones=value.shortfall_semitones,
            resulting_lowest_note=value.resulting_lowest_note,
            resulting_highest_note=value.resulting_highest_note,
            resulting_key=(
                None
                if value.resulting_key is None
                else ReferenceKeyModel.from_domain(value.resulting_key)
            ),
        )


class SongCompatibilityResponse(BaseModel):
    """A recording's detected range placed against a song's asserted one."""

    comparable: bool
    recording_status: str = Field(
        description=(
            "`ready`, or why not: `analysis_missing`, `analysis_in_progress`, "
            "`analysis_failed`, `insufficient_pitch_signal`. There is no "
            "`not_found` — an unknown recording is a `404`, as on every other "
            "recording route."
        )
    )
    recording_range: NoteRangeResponse | None = None
    reference_range: NoteRangeResponse | None = None
    reference: SongReferenceResponse | None = None
    fit: RangeFitResponse | None = None
    transposition: TranspositionResponse | None = None
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Always contains at least `reference_range_asserted`, "
            "`detected_range_is_this_recording` and "
            "`not_a_statement_of_ability`. Those three describe the method "
            "rather than the inputs, so they are true of every result; the rest "
            "are conditional on what was measured."
        ),
    )

    @classmethod
    def from_domain(cls, value: SongCompatibility) -> "SongCompatibilityResponse":
        return cls(
            comparable=value.comparable,
            recording_status=value.recording_status.value,
            recording_range=(
                None
                if value.recording_range is None
                else NoteRangeResponse.from_domain(value.recording_range)
            ),
            reference_range=(
                None
                if value.reference_range is None
                else NoteRangeResponse.from_domain(value.reference_range)
            ),
            reference=(
                None
                if value.reference is None
                else SongReferenceResponse.from_domain(value.reference)
            ),
            fit=None if value.fit is None else RangeFitResponse.from_domain(value.fit),
            transposition=(
                None
                if value.transposition is None
                else TranspositionResponse.from_domain(value.transposition)
            ),
            caveats=[caveat.value for caveat in value.caveats],
        )
