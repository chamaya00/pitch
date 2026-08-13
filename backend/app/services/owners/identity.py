"""The seam where a caller becomes an owner.

Everything below the API takes ``owner_id: uuid.UUID`` and nothing else. No
domain service knows what a token is, how a header is spelled, or how identity
was established — which means **replacing how somebody proves who they are does
not touch recording ownership at all.** This module states that boundary as a
protocol rather than leaving it as a property of the current code that a future
change could quietly lose.

Today there is one implementation: :class:`~app.services.owners.resolver.BearerKeyResolver`,
which resolves a bearer key. It is honest about what it is — no password, no
second factor, no server-side recovery — and Step 7P added the two things that
make it survivable: the holder can move it to another device, and can delete
everything it owns.

Step 10.2 made the seam real. Until then this protocol was declared here and
consumed nowhere: the API called the bearer-key function directly, so the
boundary existed in prose. The API now depends on this protocol, which is what
makes the next sentence a fact rather than a hope.

A password or OAuth resolver is a **second implementation of this one
protocol**, resolving to the *same* ``owner_id`` that already owns the
recordings. Nothing in ``services/recordings``, ``services/analysis``,
``services/audio_analysis``, ``services/comparison`` or ``services/progress``
changes, and no migration reassigns a recording to a new owner.

The schema constraint that used to stand in the way is gone. ``owners`` no
longer carries a key at all: credentials live in their own table and reference
an owner, so an owner who signs in some other way needs no column relaxed and no
placeholder key invented.
"""

import uuid
from typing import Protocol, runtime_checkable

from app.services.owners.models import Owner


@runtime_checkable
class IdentityResolver(Protocol):
    """Turns whatever a caller presented into the owner it identifies.

    The only place in the system that decides *who* somebody is. Implementations
    may mint a new identity (the anonymous resolver does) or refuse (a
    credential resolver would); either way what they return is an ``Owner``
    whose ``owner_id`` is the one already recorded against that person's
    recordings.
    """

    async def resolve(self) -> Owner:
        """Return the caller's owner, or raise a documented ``ApiError``."""


@runtime_checkable
class OwnerDataRepository(Protocol):
    """Reading and removing everything one owner has.

    Separate from the recording repository because these two operations are
    about the *identity*, not about a recording: a summary of what would be lost,
    and the removal of all of it.
    """

    async def data_summary(self, owner_id: uuid.UUID) -> "OwnerDataSummary":
        """What this owner has accumulated."""

    async def recording_ids(self, owner_id: uuid.UUID) -> list[str]:
        """Every recording id this owner has, so the stored audio can be removed."""

    async def delete_owner(self, owner_id: uuid.UUID) -> bool:
        """Remove the owner row. Analyses and recordings cascade with it.

        Returns whether a row was removed. The **audio files are not touched
        here** — they live on disk, outside any transaction, and removing them
        is the deletion service's job.
        """


class OwnerDataSummary:
    """How much an owner would lose, in the terms the UI states it.

    A plain object rather than a pydantic model: it is assembled from one SQL
    row and is not part of the domain the analysis services reason about.
    """

    __slots__ = ("ai_feedback", "analysed_recordings", "recordings")

    def __init__(self, *, recordings: int, analysed_recordings: int, ai_feedback: int) -> None:
        self.recordings = recordings
        self.analysed_recordings = analysed_recordings
        self.ai_feedback = ai_feedback

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OwnerDataSummary):
            return NotImplemented
        return (
            self.recordings == other.recordings
            and self.analysed_recordings == other.analysed_recordings
            and self.ai_feedback == other.ai_feedback
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return (
            f"OwnerDataSummary(recordings={self.recordings}, "
            f"analysed_recordings={self.analysed_recordings}, "
            f"ai_feedback={self.ai_feedback})"
        )


__all__ = ["IdentityResolver", "OwnerDataRepository", "OwnerDataSummary"]
