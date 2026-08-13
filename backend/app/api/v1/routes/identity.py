"""Routes for the caller's own identity.

*What do I have?*, *remove all of it*, and — since Step 10.2 — *what are the
ways in, add one, take one away*. Every route here is scoped to the resolved
owner. None of them takes an owner identifier: there is no parameter through
which a caller could name somebody else, which is a stronger guarantee than
checking one.

There is deliberately **no route that returns an existing key**. The server
stores only a hash, so it cannot; and a "give me my key" endpoint reachable with
the key is not recovery, it is an echo. A *new* key is returned once, at the
moment it is created, and never again.

**Adding a credential does not create an owner.** It attaches another way in to
the identity that already owns the caller's recordings — the whole point of the
step. Nothing is copied, nothing is reassigned, and the `owner_id` on every
existing row is untouched.
"""

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    CostlyRequestDep,
    CredentialRepositoryDep,
    OwnerDataRepositoryDep,
    OwnerDeletionServiceDep,
    OwnerDep,
    PresentedKeyDep,
)
from app.api.responses import error_responses
from app.core.errors import ApiError, ErrorCode
from app.schemas.identity import (
    CreateCredentialRequest,
    CreatedCredentialResponse,
    CredentialResponse,
    DeletionResponse,
    IdentityResponse,
)
from app.services.owners.credentials import LastCredentialError, new_credential

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get(
    "",
    response_model=IdentityResponse,
    summary="Who you are, and what you have",
    description=(
        "A summary of the caller's own identity.\n\n"
        "**This is not an account.** `anonymous: true` means every way in is a "
        "bearer key and nothing else: no password, no second factor, and no way "
        "for the server to recover one. Only a hash of each key is stored, so "
        "this endpoint cannot return one — the browser holding it is the only "
        "place it exists in the clear. Keys *can* be revoked individually since "
        "Step 10.2; see `DELETE /identity/credentials/{credential_id}`.\n\n"
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
    credentials: CredentialRepositoryDep,
    presented_key: PresentedKeyDep,
) -> IdentityResponse:
    """Return the caller's identity, what it owns, and the ways in to it."""
    summary = await owners.data_summary(owner.owner_id)
    keys = await credentials.list_for_owner(owner.owner_id)
    # Which of them the reader is holding. Looked up by hash like any other
    # resolution; the key itself is not stored, compared in Python, or returned.
    current = None if presented_key is None else await credentials.credential_for_key(presented_key)
    return IdentityResponse.from_domain(owner, summary, keys, current)


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


@router.post(
    "/credentials",
    response_model=CreatedCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add another way in to this identity",
    description=(
        "Create a second key for the identity the caller already has — for "
        "another device, or to keep one somewhere safe.\n\n"
        "**This does not create a new identity.** The new key resolves to the "
        "same owner, so it sees the same recordings, the same analyses and the "
        "same history. Nothing is copied and nothing changes hands.\n\n"
        "**The key is returned once, in this response, and never again.** Only "
        "a hash of it is stored, so no later request can show it. Anyone holding "
        "it is this identity: it is a bearer key, not a password.\n\n"
        "The optional `label` is for the holder's benefit — 'Phone', 'Laptop' — "
        "and carries no security weight."
    ),
    responses=error_responses(
        ErrorCode.VALIDATION_ERROR, ErrorCode.RATE_LIMITED, ErrorCode.INTERNAL_ERROR
    ),
)
async def create_credential(
    owner: OwnerDep,
    credentials: CredentialRepositoryDep,
    _limit: CostlyRequestDep,
    body: CreateCredentialRequest | None = None,
) -> CreatedCredentialResponse:
    """Mint a credential for the **already resolved** owner.

    The owner id comes from the resolver, never from the request: there is no
    field a caller could use to attach a key to somebody else's identity.
    """
    credential, key = new_credential(owner.owner_id, None if body is None else body.label)
    stored = await credentials.create(credential, key)
    return CreatedCredentialResponse(
        credential_id=stored.credential_id,
        label=stored.label,
        created_at=stored.created_at,
        key=key,
    )


@router.delete(
    "/credentials/{credential_id}",
    response_model=list[CredentialResponse],
    status_code=status.HTTP_200_OK,
    summary="Revoke one way in",
    description=(
        "Remove a single key from this identity. Everything else is untouched: "
        "the recordings, the analyses and the other keys all remain, and the "
        "owner is unchanged.\n\n"
        "**The last key cannot be revoked.** Removing it would strand the "
        "identity — the recordings would still exist, still owned, and nobody "
        "could ever reach them again. Deleting the identity is the honest way to "
        "get rid of everything, and it is a different endpoint.\n\n"
        "**Revoking the key you are holding is allowed** when others remain; the "
        "current request finishes and that key stops working afterwards.\n\n"
        "A credential belonging to somebody else is reported as not found rather "
        "than refused, so this cannot be used to discover that an id is real.\n\n"
        "Returns the credentials that remain."
    ),
    responses=error_responses(
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.CREDENTIAL_NOT_FOUND,
        ErrorCode.LAST_CREDENTIAL,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def revoke_credential(
    credential_id: uuid.UUID,
    owner: OwnerDep,
    credentials: CredentialRepositoryDep,
    presented_key: PresentedKeyDep,
) -> list[CredentialResponse]:
    """Revoke one of the caller's own credentials.

    Scoped by owner in SQL rather than fetched and filtered, so a credential
    belonging to somebody else is never read in the first place.
    """
    try:
        removed = await credentials.revoke(credential_id, owner.owner_id)
    except LastCredentialError as exc:
        raise ApiError(
            ErrorCode.LAST_CREDENTIAL,
            "This is the only way in to your recordings, so it cannot be removed. "
            "Add another key first, or delete the identity if you want everything gone.",
        ) from exc
    if not removed:
        raise ApiError(ErrorCode.CREDENTIAL_NOT_FOUND, "No such key.")

    remaining = await credentials.list_for_owner(owner.owner_id)
    current = None if presented_key is None else await credentials.credential_for_key(presented_key)
    return [
        CredentialResponse.from_domain(credential, current=credential.credential_id == current)
        for credential in remaining
    ]
