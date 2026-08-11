"""Who a recording belongs to.

**This is ownership, not authentication.** An owner is a server-generated
bearer token and a row; there is no password, no email, no session, no login.
That is deliberate for this step: recording history needs to know *whose*
recordings to show, and building a real account system to answer that question
would be a larger, separate piece of work with its own security surface.

What it does provide is a clean path to Phase 10. An ``Owner`` has a stable id
that recordings reference; adding real authentication later means adding a
table of credentials that resolves *to* an owner id, and nothing that already
points at an owner has to change.

What it does **not** provide, stated plainly so nobody mistakes it for more:

* Anyone holding the token is the owner. There is no second factor and no way
  to revoke one.
* Losing the token loses the history. There is no recovery, because there is no
  account to recover.
* It is not a defence against a determined attacker enumerating tokens — the
  128-bit random value makes that impractical, but the guarantee is the entropy,
  not an access-control system.

The token is stored **hashed**. A leaked database yields no usable tokens, for
the same reason a leaked password database should yield no usable passwords.
"""

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.services.recordings.models import utc_now

#: Bytes of entropy in a token. 128 bits: enough that guessing one is not a
#: strategy, short enough to sit in a header and a localStorage value.
_TOKEN_BYTES: Final = 16

#: Tokens are URL-safe base64 of ``_TOKEN_BYTES``. Constraining the shape means
#: a value that cannot be a token is rejected before it reaches a query.
#:
#: Anchored with ``^``/``$`` rather than ``\A``/``\Z`` so the same literal is
#: valid for both Python's ``re`` and the Rust engine pydantic validates with.
TOKEN_PATTERN: Final = r"^[A-Za-z0-9_-]{22}$"


def new_owner_token() -> str:
    """Mint a token. Returned to the client once and never stored in the clear."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of a token.

    A plain SHA-256 rather than a password hash, and the difference matters:
    this is a high-entropy random value, not a human-chosen secret, so there is
    no dictionary to slow an attacker down against. What a slow hash buys for
    passwords, 128 bits of entropy already buys here.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Owner(BaseModel):
    """A recording's owner. Identity only — no profile, no credentials."""

    model_config = ConfigDict(frozen=True)

    owner_id: uuid.UUID
    created_at: datetime = Field(default_factory=utc_now)


def new_owner() -> tuple[Owner, str]:
    """A fresh owner and its token. The token is returned exactly once."""
    return Owner(owner_id=uuid.uuid4()), new_owner_token()
