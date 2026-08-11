"""Routes for the caller's own identity.

Two operations the product was missing until Step 7P: *what do I have?* and
*remove all of it*. Both are scoped to the resolved owner and take no
identifier — there is no parameter through which a caller could name somebody
else, which is a stronger guarantee than checking one.

There is deliberately **no route that returns a key**. The server stores only a
hash, so it cannot; and a "give me my key" endpoint reachable with the key is
not recovery, it is an echo. Recovery lives in the browser, which is the only
place the key exists in the clear.
"""

from fastapi import APIRouter, status

from app.api.deps import OwnerDataRepositoryDep, OwnerDeletionServiceDep, OwnerDep
from app.api.responses import error_responses
from app.core.errors import ErrorCode
from app.schemas.identity import DeletionResponse, IdentityResponse

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get(
    "",
    response_model=IdentityResponse,
    summary="Who you are, and what you have",
    description=(
        "A summary of the caller's own identity.\n\n"
        "**This is not an account.** `anonymous: true` means the identity is a "
        "bearer key and nothing else: no password, no revocation, and no way "
        "for the server to recover it. Only a hash of the key is stored, so "
        "this endpoint cannot return one — the browser holding it is the only "
        "place it exists in the clear.\n\n"
        "The counts say what a lost key would cost. `ai_feedback` is listed "
        "separately because generating it costs a provider call: measurements "
        "can be recomputed from the audio, generated prose cannot.\n\n"
        "No owner id is returned. The caller already proves who they are, and "
        "echoing the internal identifier would put a second permanent handle on "
        "the same person into logs and screenshots for no benefit."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def get_identity(
    owner: OwnerDep,
    owners: OwnerDataRepositoryDep,
) -> IdentityResponse:
    """Return the caller's identity and what it owns."""
    summary = await owners.data_summary(owner.owner_id)
    return IdentityResponse.from_domain(owner, summary)


@router.delete(
    "",
    response_model=DeletionResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete everything you have",
    description=(
        "Remove the caller's identity and everything belonging to it: every "
        "recording, every analysis, every generated interpretation, and the "
        "stored audio itself.\n\n"
        "**This is not reversible and there is no soft delete.** Somebody asking "
        "for their recordings to be gone is asking for them to be gone, and a "
        "row that still exists is not gone.\n\n"
        "**The audio files are removed too.** Deleting the rows alone would "
        "report success while leaving every recording on the server. The files "
        "go first, then the rows — so a failure part-way through leaves the rows "
        "and a retry finishes the job, rather than leaving audio nobody can "
        "name.\n\n"
        "The response says what was actually removed, including any file that "
        "could not be. Repeating the request is safe: an identity that no "
        "longer exists deletes nothing, and the caller is issued a fresh empty "
        "one on their next request."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def delete_identity(
    owner: OwnerDep,
    deletion: OwnerDeletionServiceDep,
) -> DeletionResponse:
    """Delete the caller's identity and all of its data."""
    report = await deletion.delete_everything(owner.owner_id)
    return DeletionResponse(
        recordings=report.recordings,
        audio_files_deleted=report.audio_files_deleted,
        audio_files_failed=report.audio_files_failed,
    )
