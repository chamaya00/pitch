"""API representation of the caller's own identity.

The narrowest response in this API, and deliberately so. It answers three
questions — *what have I accumulated?*, *is this identity anonymous?* and *what
are the ways in to it?* — and carries nothing else.

**No owner id.** The client already proves who it is with its key; echoing the
internal identifier would put a second, permanent handle on the same person into
logs, screenshots and bug reports for no benefit.

**No key and no hash.** The server stores only a SHA-256 of a key, so it *could
not* return one even if that were wise. A new key is shown once, in the response
that creates it, and never again — which is why the browser is the one that
displays it, and why these responses say what is at stake if it is lost.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.services.owners.credentials import MAX_LABEL_LENGTH, Credential
from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import Owner


class CredentialResponse(BaseModel):
    """One way in to this identity.

    Carries no hash and no key. The id is here because revoking needs one, and
    it is not credential material: knowing it grants nothing.
    """

    credential_id: uuid.UUID
    label: str = Field(description="What the holder called it. Display only.")
    created_at: datetime
    current: bool = Field(description="Whether this is the credential the request was made with.")

    @classmethod
    def from_domain(cls, credential: Credential, *, current: bool) -> "CredentialResponse":
        return cls(
            credential_id=credential.credential_id,
            label=credential.label,
            created_at=credential.created_at,
            current=current,
        )


class CreateCredentialRequest(BaseModel):
    """Ask for another way in to this identity."""

    label: str | None = Field(
        default=None,
        max_length=MAX_LABEL_LENGTH,
        description=(
            "What to call the new key — 'Phone', 'Laptop'. Display only, so it "
            "needs no uniqueness and carries no security weight. Omitted or "
            "blank gets a default."
        ),
    )


class CreatedCredentialResponse(BaseModel):
    """A new key, returned **once**.

    The only moment the key exists outside the holder's hands: only a hash of it
    is stored, so nothing can show it again. It is returned in the body rather
    than a header because the client has to display it for someone to write
    down, and because it is a deliberate response to a deliberate request.
    """

    credential_id: uuid.UUID
    label: str
    created_at: datetime
    key: str = Field(
        description=(
            "The new key. Shown once and never recoverable. Anyone holding it is this owner."
        )
    )


class IdentityResponse(BaseModel):
    """Who the caller is, in the only terms this system has."""

    created_at: datetime = Field(description="When this identity was first issued (UTC).")
    anonymous: bool = Field(
        description=(
            "`true` while every way in to this identity is a bearer key — no "
            "password, no second factor, no server-side recovery. Holding "
            "several named keys does not change that: they are still keys. It "
            "becomes `false` only when a credential that is not a bearer key is "
            "attached."
        )
    )
    recordings: int = Field(description="How many recordings this identity owns.")
    analysed_recordings: int = Field(
        description="How many of them have a completed audio analysis."
    )
    ai_feedback: int = Field(
        description=(
            "How many of them have generated feedback on at least one analysis. "
            "Counted separately because producing it costs a provider call, so "
            "it is the part of a lost identity that cannot simply be recomputed. "
            "A count of **recordings**, like the two above it, and never more "
            "than `recordings`: until Step 10.19 it counted feedback *runs*, so "
            "a recording analysed twice could make the deletion warning say that "
            "one recording included two of them."
        )
    )
    song_references: int = Field(
        default=0,
        description=(
            "How many song references this identity holds. **Not a count of "
            "recordings**, unlike the three fields above it: a reference is a "
            "song's range somebody typed, and an identity can hold references "
            "and no recordings at all. Counted here because deleting the "
            "identity deletes these too, and a confirmation that did not name "
            "them would be incomplete."
        ),
    )
    credentials: list[CredentialResponse] = Field(
        default_factory=list,
        description=(
            "Every way in to this identity, oldest first. All of them resolve "
            "to the same owner, so adding one to another device does not create "
            "a second identity. Hashes are never included."
        ),
    )

    @classmethod
    def from_domain(
        cls,
        owner: Owner,
        summary: OwnerDataSummary,
        credentials: list[Credential] | None = None,
        current_credential_id: uuid.UUID | None = None,
    ) -> "IdentityResponse":
        return cls(
            created_at=owner.created_at,
            # Every credential the system can issue today is a bearer key, so
            # this stays true however many of them an owner holds. When a
            # resolver that knows something rather than holds something lands,
            # this is where it stops being true.
            anonymous=True,
            recordings=summary.recordings,
            analysed_recordings=summary.analysed_recordings,
            ai_feedback=summary.ai_feedback,
            song_references=summary.song_references,
            credentials=[
                CredentialResponse.from_domain(
                    credential, current=credential.credential_id == current_credential_id
                )
                for credential in (credentials or [])
            ],
        )


class DeletionResponse(BaseModel):
    """What deleting an identity actually removed.

    Returned rather than a bare `204` because the audio lives on disk, outside
    the transaction that removes the rows. A caller is told how many files went
    and how many resisted, instead of being left to assume.
    """

    recordings: int = Field(description="Recordings removed from the database.")
    audio_files_deleted: int = Field(description="Stored audio files removed from disk.")
    audio_files_failed: int = Field(
        description=(
            "Files that could not be removed. The database rows were deleted "
            "anyway — leaving them would make the audio reachable again — so "
            "these are unreachable but still present on the server."
        )
    )
