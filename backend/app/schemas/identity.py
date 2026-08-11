"""API representation of the caller's own identity.

The narrowest response in this API, and deliberately so. It answers two
questions — *what have I accumulated?* and *is this identity anonymous?* — and
carries nothing else.

**No owner id.** The client already proves who it is with its key; echoing the
internal identifier would put a second, permanent handle on the same person into
logs, screenshots and bug reports for no benefit.

**No token.** The server stores only a SHA-256 hash of it, so it *could not*
return one even if that were wise. A lost key cannot be recovered from here —
which is exactly why the browser is the one that shows it, and why this response
tells the reader what is at stake if they lose it.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import Owner


class IdentityResponse(BaseModel):
    """Who the caller is, in the only terms this system has."""

    created_at: datetime = Field(description="When this identity was first issued (UTC).")
    anonymous: bool = Field(
        description=(
            "`true` while the identity is only a bearer key — no password, no "
            "revocation, no server-side recovery. It becomes `false` if "
            "credentials are ever attached to the same identity."
        )
    )
    recordings: int = Field(description="How many recordings this identity owns.")
    analysed_recordings: int = Field(
        description="How many of them have a completed audio analysis."
    )
    ai_feedback: int = Field(
        description=(
            "How many analyses carry generated feedback. Counted separately "
            "because producing it costs a provider call, so it is the part of a "
            "lost identity that cannot simply be recomputed."
        )
    )

    @classmethod
    def from_domain(cls, owner: Owner, summary: OwnerDataSummary) -> "IdentityResponse":
        return cls(
            created_at=owner.created_at,
            # One resolver exists today and it is anonymous. When a credential
            # resolver lands, this is where it stops being true — the field
            # exists now so a client is not rewritten to learn about it later.
            anonymous=True,
            recordings=summary.recordings,
            analysed_recordings=summary.analysed_recordings,
            ai_feedback=summary.ai_feedback,
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
