"""Song references, and the compatibility of a recording with one.

A thin translation layer, like every other route module here: a body or a
parameter in, a service call, a domain object out as a response schema. No
arithmetic, no eligibility rules, no SQL.

**Two paths, two prefixes, one router.** References are their own collection
under ``/references``; a compatibility result is a sub-resource of a recording,
because it is derived from that recording's stored measurement and because
ownership is then checked on a path segment exactly as it is on
``/recordings/{id}/audio-analysis``.

**Refusals are 200s, missing things are 404s.** A recording nobody has measured,
one still being measured, one whose analysis failed, and one with no reliable
pitch are all successful responses whose ``comparable`` is ``false`` and whose
``recording_status`` says which — a client renders each of them differently, and
an HTTP error would collapse them into one. An id that is not the caller's is a
``404``, on either the recording or the reference, and it is the same ``404`` an
id that never existed gets.

**Creating a reference is not rate-limited**, unlike uploading. The costly-request
allowance exists for requests that consume disk, CPU or provider budget; this one
writes about two hundred bytes and decodes nothing. Reading is never limited
here, consistent with every other read in this API.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import CompatibilityServiceDep, OwnerIdDep, SongReferenceServiceDep
from app.api.responses import error_responses
from app.core.errors import ErrorCode
from app.schemas.compatibility import (
    SongCompatibilityResponse,
    SongReferenceListResponse,
    SongReferenceRequest,
    SongReferenceResponse,
)
from app.services.compatibility.models import (
    ReferenceKey,
    SongReference,
    new_reference_id,
)

router = APIRouter(tags=["compatibility"])

#: A list screen, not a bulk export — the same reasoning the history limits
#: carry, and the same shape.
DEFAULT_REFERENCE_LIMIT = 50
MAX_REFERENCE_LIMIT = 200

#: Server-generated ids, constrained at the edge so a malformed one never
#: reaches a query.
_ID_PATTERN = r"^[0-9a-f]{32}$"

RecordingIdPath = Annotated[
    str, Path(pattern=_ID_PATTERN, description="Server-generated recording identifier.")
]
ReferenceIdPath = Annotated[
    str, Path(pattern=_ID_PATTERN, description="Server-generated reference identifier.")
]
ReferenceIdQuery = Annotated[
    str, Query(pattern=_ID_PATTERN, description="One of your own song references.")
]


@router.post(
    "/references",
    response_model=SongReferenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Describe a song to compare against",
    description=(
        "Record a song's range so a recording can be placed against it.\n\n"
        "**Nothing here is measured.** There is no audio, no upload and no "
        "decoding: the two notes and the optional key are what you say they "
        "are, and every figure derived from them is an arithmetic consequence "
        "of an unverified input. The response says so in `source`, and it says "
        "so on every compatibility result that uses it.\n\n"
        "**Notes are written with sharps**, in scientific pitch notation — "
        "`F#3`, `C4`, `A#5`. Flats are refused rather than silently rewritten, "
        "because this project spells every pitch class one way and handing back "
        "a different name than you sent would be worse than saying no.\n\n"
        "**The highest note may not be below the lowest**, and both must be "
        "real MIDI notes. A range of a single note is allowed: a drone and a "
        "one-note exercise are songs too.\n\n"
        "**Duplicates are allowed.** Two entries for the same song are two "
        "references, the same way uploading the same audio twice is two "
        "recordings."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def create_reference(
    service: SongReferenceServiceDep,
    owner_id: OwnerIdDep,
    body: SongReferenceRequest,
) -> SongReferenceResponse:
    """Store a song reference owned by the caller."""
    reference = SongReference(
        reference_id=new_reference_id(),
        title=body.title,
        artist=body.artist,
        lowest_note=body.lowest_note,
        highest_note=body.highest_note,
        key=(
            None
            if body.key is None
            # Validated into the domain's own enum here rather than carried as a
            # string: the mode is one of two things, and the request schema's
            # looser type stops at this boundary.
            else ReferenceKey.model_validate(body.key.model_dump())
        ),
    )
    stored = await service.create(reference, owner_id)
    return SongReferenceResponse.from_domain(stored)


@router.get(
    "/references",
    response_model=SongReferenceListResponse,
    summary="List your song references",
    description=(
        "The references belonging to the caller, newest first.\n\n"
        "**Ownership is enforced in the database.** There is no parameter that "
        "would let a caller name another owner, and none may be added.\n\n"
        "`count` is how many are in this response, not how many exist."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def list_references(
    service: SongReferenceServiceDep,
    owner_id: OwnerIdDep,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_REFERENCE_LIMIT, description="Largest number to return."),
    ] = DEFAULT_REFERENCE_LIMIT,
) -> SongReferenceListResponse:
    """Return this owner's song references."""
    return SongReferenceListResponse.from_domain(await service.list_for_owner(owner_id, limit))


@router.get(
    "/references/{reference_id}",
    response_model=SongReferenceResponse,
    summary="Read one of your song references",
    responses=error_responses(
        ErrorCode.REFERENCE_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def read_reference(
    service: SongReferenceServiceDep,
    owner_id: OwnerIdDep,
    reference_id: ReferenceIdPath,
) -> SongReferenceResponse:
    """Return one reference, if it is the caller's."""
    return SongReferenceResponse.from_domain(await service.get(reference_id, owner_id))


@router.delete(
    "/references/{reference_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one of your song references",
    description=(
        "Delete a reference. Nothing else is affected: a reference holds no "
        "audio and no analysis, and the recordings it was compared against are "
        "untouched.\n\n"
        "**Never rate-limited**, for the reason deletion is never rate-limited "
        "here: being told to slow down while removing your own data is the one "
        "moment a limit is indefensible."
    ),
    responses=error_responses(
        ErrorCode.REFERENCE_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def delete_reference(
    service: SongReferenceServiceDep,
    owner_id: OwnerIdDep,
    reference_id: ReferenceIdPath,
) -> None:
    """Remove one reference belonging to the caller."""
    await service.delete(reference_id, owner_id)


@router.get(
    "/recordings/{recording_id}/compatibility",
    response_model=SongCompatibilityResponse,
    summary="Place a recording's range against a song's",
    description=(
        "Compare the range detected in a recording with the range you said a "
        "song has, and report what follows arithmetically.\n\n"
        "**One side was measured and the other was typed.** `source` is "
        "`measured` on the recording's range and `asserted` on the song's, and "
        "the difference is the whole reason the field exists: everything in "
        "`fit` and `transposition` is an arithmetic consequence of a number "
        "this system never verified.\n\n"
        "**There is no compatibility score, and no field that could hold one.** "
        "A single figure would have to weight how far the top is out against "
        "how far the bottom is out against how much of the middle overlaps, and "
        "no measurement sets those weights. The components are reported "
        "instead, each with its unit in its name.\n\n"
        "**Counts and distances are different numbers.** "
        "`overlap_note_count` counts semitone positions and includes both ends; "
        "`semitones_above_top_note` is a distance. A song running C4–C5 spans "
        "12 semitones and contains 13 notes.\n\n"
        "**A refusal is a `200`.** A recording nobody has measured, one still "
        "being measured, one whose analysis failed and one with no reliable "
        "pitch all return successfully with `comparable: false` and a "
        "`recording_status` saying which. A recording or a reference that is "
        "not yours is a `404`, identical to one that never existed.\n\n"
        "**Range overlap is not a statement about whether you can sing a "
        "song**, and the detected range is what one recording contained rather "
        "than a physiological maximum. Both are in `caveats` on every response, "
        "unconditionally, because they describe the method rather than the "
        "inputs."
    ),
    responses=error_responses(
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.REFERENCE_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def read_compatibility(
    service: CompatibilityServiceDep,
    owner_id: OwnerIdDep,
    recording_id: RecordingIdPath,
    reference_id: ReferenceIdQuery,
) -> SongCompatibilityResponse:
    """Compare one of the caller's recordings with one of their references."""
    result = await service.compatibility(recording_id, reference_id, owner_id)
    return SongCompatibilityResponse.from_domain(result)
