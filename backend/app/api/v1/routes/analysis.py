"""Analysis routes.

A thin translation layer, and deliberately nothing else. It turns a path
parameter into a service call and a domain object into a response schema. There
is no analysis logic here, no provider logic, no persistence and no filesystem
access — every decision about idempotency, failure and retry belongs to
``services/orchestration/analysis.py`` and stays there.

The slow work never happens inside a request. ``POST`` creates the record and
hands execution to a background task, so the response goes out in milliseconds
while transcription runs afterwards. That is also why a provider failure is
never a ``POST`` error: by the time it happens the request is long finished, and
the failure surfaces on ``GET`` as ``200`` with ``status: "failed"``.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Response, status

from app.api.deps import AnalysisServiceDep, CostlyRequestDep, OwnerIdDep
from app.api.responses import error_responses
from app.core.errors import ApiError, ErrorCode
from app.schemas.analysis import AnalysisResponse

router = APIRouter(prefix="/recordings", tags=["analysis"])

#: Recording ids are ``uuid4().hex``. Constraining the path parameter means a
#: malformed id is answered with a validation error at the edge and never
#: reaches a service — so nothing shaped like a path or a traversal sequence can
#: travel any further into the application, whatever a later layer might do
#: with it. The storage and repository layers still check the same shape
#: themselves; this is the outermost of several, not a replacement for them.
RecordingIdPath = Annotated[
    str,
    Path(
        description="Server-generated recording identifier: 32 lower-case hex characters.",
        pattern=r"^[0-9a-f]{32}$",
        examples=["8f14e45fceea167a5a36dedd4bea2543"],
    ),
]


@router.post(
    "/{recording_id}/analysis",
    response_model=AnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyse a recording",
    description=(
        "Start an analysis of a stored recording, or return the one that "
        "already exists.\n\n"
        "**This returns before the analysis runs.** Transcription and feedback "
        "happen in the background; poll `GET` on the same path for progress and "
        "results. A provider failure therefore never appears here — it is "
        "recorded on the analysis and surfaces on `GET` as `200` with "
        '`status: "failed"`.\n\n'
        "**Repeating the request is safe.** An analysis that is already queued, "
        "running or finished is returned as-is, and no second provider call is "
        "made. Only a recording whose last analysis *failed* starts a new one — "
        "the failed record stays readable.\n\n"
        "Returns `202` when work is queued or in flight, and `200` when an "
        "already-completed analysis is being handed back."
    ),
    responses={
        status.HTTP_200_OK: {
            "model": AnalysisResponse,
            "description": (
                "An analysis of this recording had already finished. Nothing was "
                "queued and no provider was called."
            ),
        },
        **error_responses(
            ErrorCode.RECORDING_NOT_FOUND,
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.INTERNAL_ERROR,
        ),
    },
)
async def start_analysis(
    recording_id: RecordingIdPath,
    service: AnalysisServiceDep,
    owner_id: OwnerIdDep,
    _limit: CostlyRequestDep,
    background_tasks: BackgroundTasks,
    response: Response,
) -> AnalysisResponse:
    """Create or return the analysis for a recording."""
    started = await service.start(recording_id, owner_id)

    if started.created:
        # Scheduled only for an analysis this request created. Scheduling one
        # for a record that was already in flight would run the providers a
        # second time over the same recording.
        background_tasks.add_task(service.run, started.analysis.analysis_id)
    elif started.analysis.is_terminal:
        response.status_code = status.HTTP_200_OK

    return AnalysisResponse.from_analysis(started.analysis)


@router.get(
    "/{recording_id}/analysis",
    response_model=AnalysisResponse,
    summary="Get a recording's analysis",
    description=(
        "Return the current analysis of a recording: its progress while it "
        "runs, its results once it finishes.\n\n"
        "**A failed analysis is a successful response.** It comes back as `200` "
        'with `status: "failed"` and an `error_code`, because the request '
        "worked — the analysis did not.\n\n"
        "Which fields are populated depends on `status`; see the response "
        "schema. A `null` metric always means the recording did not support "
        "measuring it, never zero."
    ),
    responses=error_responses(
        ErrorCode.ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_analysis(
    recording_id: RecordingIdPath,
    service: AnalysisServiceDep,
    owner_id: OwnerIdDep,
) -> AnalysisResponse:
    """Return the recording's most recent analysis."""
    analysis = await service.current(recording_id, owner_id)
    if analysis is None:
        raise ApiError(
            ErrorCode.ANALYSIS_NOT_FOUND,
            "That recording has not been analysed yet.",
        )
    return AnalysisResponse.from_analysis(analysis)
