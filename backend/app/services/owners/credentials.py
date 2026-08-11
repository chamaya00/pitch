"""Credentials: the ways in to an owner.

Until Step 10.2 an owner *was* a key. One value, no label, no way to add a
second and no way to revoke one without losing the identity it stood for. A
credential now **belongs to** an owner instead of being one, which is the whole
change: several ways in, each nameable, each revocable, all resolving to the
same ``owner_id`` that already owns the recordings.

**This is a credential layer, not authentication.** Every credential here is
still a bearer key: whoever holds it is the owner, there is no password to know
and no second factor. What it buys is the boundary — a future password or OAuth
resolver becomes another way of arriving at an ``owner_id``, rather than a
rewrite of what an owner is.

The security story is unchanged and deliberately so:

* the key is 128 bits from ``secrets``, so guessing one is not a strategy;
* only a SHA-256 of it is stored, so a leaked database yields no usable keys;
* it is never logged, never returned except once at creation, and never compared
  in Python — resolution is an indexed lookup of the hash, so there is no
  secret-dependent branch in application code whose timing could leak.

Passwords were considered and rejected for this slice. Adding them while
deferring reset, verification and rate limiting would make the system *less*
safe than it is: a human-chosen password is far more guessable than 128 random
bits, and none of the controls that make passwords workable would be present.
"""

import uuid
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.services.owners.models import hash_token, new_owner_token
from app.services.recordings.models import utc_now

#: Longest label a credential may carry. Long enough for "Laptop in the study",
#: short enough that the column cannot be used as storage for something else.
MAX_LABEL_LENGTH: Final = 60

#: Used when a caller supplies nothing. Better than an empty string, which would
#: render as a nameless row in the list of keys.
DEFAULT_LABEL: Final = "New key"


class CredentialExistsError(Exception):
    """That credential hash is already registered.

    Raised on the unique violation. With 128-bit keys this means a genuine
    duplicate insert rather than a collision.
    """


class LastCredentialError(Exception):
    """Refusing to revoke the only way in to an owner.

    Not a safety net around a mistake — a rule. Removing the last credential
    would strand the identity: the recordings would still exist, still owned,
    and nobody could ever reach them again. Somebody who wants their data gone
    deletes the identity, which is a different and honest operation.
    """


class Credential(BaseModel):
    """One way in to an owner.

    Carries no hash and no key: this is the object the API and the UI handle,
    and neither has any business holding credential material. The hash lives in
    the database and nowhere else in the process.
    """

    model_config = ConfigDict(frozen=True)

    credential_id: uuid.UUID
    owner_id: uuid.UUID
    label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    created_at: datetime = Field(default_factory=utc_now)


def clean_label(label: str | None) -> str:
    """Normalise a caller-supplied label.

    Whitespace-only and absent are the same thing — a nameless key in a list of
    keys helps nobody — and the length is bounded here rather than trusted.
    """
    trimmed = (label or "").strip()
    if not trimmed:
        return DEFAULT_LABEL
    return trimmed[:MAX_LABEL_LENGTH]


def new_credential(owner_id: uuid.UUID, label: str | None = None) -> tuple[Credential, str]:
    """A fresh credential for an existing owner, and its key.

    The key is returned **exactly once**, to be shown to the person who asked
    for it. Nothing keeps it: only its hash is stored, so this is the only
    moment it exists outside the holder's hands.

    Note what this does *not* do: create an owner. The owner id is an input,
    which is the entire point of the step — a credential is attached to an
    identity that already exists and already owns recordings.
    """
    key = new_owner_token()
    credential = Credential(
        credential_id=uuid.uuid4(),
        owner_id=owner_id,
        label=clean_label(label),
    )
    return credential, key


def credential_hash(key: str) -> str:
    """The stored form of a credential key.

    The same SHA-256 the anonymous key has always used — deliberately, so the
    migration could copy every existing hash across unchanged and every key that
    worked before this step still works after it.
    """
    return hash_token(key)


@runtime_checkable
class CredentialRepository(Protocol):
    """Storage for the ways in to an owner."""

    async def create(self, credential: Credential, key: str) -> Credential:
        """Persist a credential, storing only a hash of ``key``.

        Raises:
            CredentialExistsError: that hash is already registered.
        """

    async def owner_for_key(self, key: str) -> uuid.UUID | None:
        """Which owner a key belongs to, or ``None``.

        Looked up by hash, so the raw key never appears in a query, a query log
        or a slow-query report.
        """

    async def credential_for_key(self, key: str) -> uuid.UUID | None:
        """Which credential a key *is*, so the UI can mark the one in use.

        Distinct from :meth:`owner_for_key`: that answers "whose is this?" and
        is what resolution needs; this answers "which of my keys am I holding?"
        and is only ever used to render a list.
        """

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Credential]:
        """An owner's credentials, oldest first. Never their hashes."""

    async def revoke(self, credential_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        """Remove one credential belonging to ``owner_id``.

        Scoped by owner in SQL: a credential belonging to somebody else is not
        found rather than refused, so the operation cannot be used to discover
        that an id is real.

        Returns whether a row was removed.

        Raises:
            LastCredentialError: it is the owner's only credential.
        """


__all__ = [
    "DEFAULT_LABEL",
    "MAX_LABEL_LENGTH",
    "Credential",
    "CredentialExistsError",
    "CredentialRepository",
    "LastCredentialError",
    "clean_label",
    "credential_hash",
    "new_credential",
]
