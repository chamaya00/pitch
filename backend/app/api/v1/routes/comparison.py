"""Recording-comparison route.

A thin translation layer, like every other route module here: two query
parameters in, a service call, a domain object out as a response schema. No
arithmetic, no eligibility rules, no SQL.

**Path.** ``/recordings/compare`` rather than ``/recordings/{id}/compare-with/{id}``:
a comparison is not a sub-resource of either recording, and making one of them
the parent would imply an asymmetry the result does not have. It is registered
before ``/recordings/{recording_id}`` so the literal segment wins over the
parameter — a test pins that, because the failure mode is a confusing 422 rather
than an obvious break.

**Refusals are 200s.** An unknown recording, a missing analysis, a failed one,
or one with no reliable pitch all come back as a successful response whose
``comparable`` is ``false`` and whose sides say why. They are answers, not
errors: a client renders each of them differently, and an HTTP error code would
collapse them into one. The single exception is asking to compare a recording
with itself, which is a malformed request rather than an outcome.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ComparisonServiceDep, OwnerIdDep
from app.api.responses import error_responses
from app.core.errors import ErrorCode
from app.schemas.comparison import RecordingComparisonResponse

router = APIRouter(prefix="/recordings", tags=["comparison"])

#: The same constraint the other recording routes apply, for the same reason: a
#: malformed id is refused at the edge and never reaches a query.
RecordingIdQuery = Annotated[
    str,
    Query(
        pattern=r"^[0-9a-f]{32}$",
        description="Server-generated recording identifier.",
    ),
]


@router.get(
    "/compare",
    response_model=RecordingComparisonResponse,
    summary="Compare two of your recordings",
    description=(
        "Place the measurements of two recordings side by side and report the "
        "differences between them.\n\n"
        "**This is a comparison of measurements, not a verdict.** There is no "
        "overall score, no ranking and no statement about which recording is "
        "better — the response has no field that could hold one. Three of the "
        "seven metrics are marked `neutral` because their difference has no "
        "better end, including the detected range: a wider range is bounded by "
        "what was performed and by the microphone, not by ability.\n\n"
        "**Both recordings must be yours.** Ownership is enforced in the "
        "database query, so a recording belonging to somebody else is never "
        "selected — it reports `not_found`, identically to an id that was never "
        "real.\n\n"
        "**A refusal is a `200`.** A missing recording, a missing analysis, a "
        "failed analysis or one with no reliable pitch all return successfully "
        "with `comparable: false` and a per-side `status` saying which. Only "
        "comparing a recording with itself is a request error.\n\n"
        "**`null` is never zero.** A measurement one recording supports and the "
        "other does not produces no delta at all; `availability` says which side "
        "was missing.\n\n"
        "**Two recordings are only meaningfully comparable when captured under "
        "reasonably similar conditions.** `caveats` names the differences this "
        "system can actually measure. Microphone, room, effort and physical "
        "condition are not among them and are not claimed."
    ),
    responses=error_responses(
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def compare_recordings(
    service: ComparisonServiceDep,
    owner_id: OwnerIdDep,
    left_id: RecordingIdQuery,
    right_id: RecordingIdQuery,
) -> RecordingComparisonResponse:
    """Compare two recordings belonging to the caller."""
    comparison = await service.compare(left_id, right_id, owner_id)
    return RecordingComparisonResponse.from_domain(comparison)
