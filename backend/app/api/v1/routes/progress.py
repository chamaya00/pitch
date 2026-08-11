"""Progress-tracking route.

A thin translation layer: one query parameter in, a service call, a domain
object out as a response schema. No arithmetic, no eligibility rules, no SQL.

**Path.** ``/recordings/progress``, registered in the same module group as
``/recordings/compare`` and before ``/recordings/{recording_id}``, so the literal
segment wins over the parameter. A test pins that, because the failure mode is a
confusing 422 rather than an obvious break.

**No provider dependency.** Progress is arithmetic over stored measurements, and
a model producing one of these numbers would be producing a measurement. There
is no object in this route's dependency graph through which one could.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import OwnerIdDep, ProgressServiceDep
from app.api.responses import error_responses
from app.core.errors import ErrorCode
from app.schemas.progress import ProgressHistoryResponse
from app.services.progress.service import DEFAULT_WINDOW, MAX_WINDOW

router = APIRouter(prefix="/recordings", tags=["progress"])


@router.get(
    "/progress",
    response_model=ProgressHistoryResponse,
    summary="Your recorded measurements over time",
    description=(
        "How the measurements from your recordings have changed, oldest "
        "first.\n\n"
        "**This is a history of measurements, not a score.** There is no level, "
        "no grade, no ranking and no claim that your singing improved — the "
        "response has no field that could hold one. Four of the seven series "
        "are marked `neutral` because a change in them means nothing in "
        "particular: a wider detected range, more pitched time, a higher voiced "
        "share and a longer recording are facts about what was recorded.\n\n"
        "**The three directed series are defined against equal temperament**, "
        "not against singing. Slides, vibrato, bends and non-Western intonation "
        "move all three without anything being wrong, and none of them is a "
        "measure of skill.\n\n"
        "**Recordings were made under conditions this system cannot measure.** "
        "Microphone, room, distance, effort, warm-up and physical condition all "
        "move these numbers and none is measured or corrected for. A change "
        "between two points may be entirely about the room.\n\n"
        "**`null` is never zero.** A point is `measured`, `not_measured` (the "
        "analysis completed without this metric) or `not_eligible` (no "
        "completed analysis). Only the first carries a value; the others are "
        "gaps, and must not be plotted at zero.\n\n"
        "**Ordering is by the recording's server-assigned creation time**, "
        "oldest first, with the recording id as a tie-break so two recordings "
        "created in the same instant have one deterministic order. The client "
        "clock is never consulted.\n\n"
        "No trend line, slope, percentage-improvement figure or forecast is "
        "computed. The strongest statement made is how the latest measured "
        "value compares with the previous measured one."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def get_progress(
    service: ProgressServiceDep,
    owner_id: OwnerIdDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_WINDOW,
            description="How many of the most recent recordings to include.",
        ),
    ] = DEFAULT_WINDOW,
) -> ProgressHistoryResponse:
    """Return the caller's measurements over time."""
    history = await service.history(owner_id, limit)
    return ProgressHistoryResponse.from_domain(history)
