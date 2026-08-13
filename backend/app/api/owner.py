"""Resolving the caller's owner identity.

One header in, one header out:

* ``X-VocalLens-Owner`` on the request names an existing owner.
* ``X-VocalLens-Owner`` on the response carries a **newly minted** key, and only
  then. A client stores it and sends it back; a client that does not simply gets
  a new identity next time and sees no history.

Since Step 10.3 the adapter also supplies the resolver's mint guard: creating an
identity is a write, so it is rate-limited per client address. Presenting a
recognised key never reaches that guard.

**This is not authentication and is not presented as one.** The key is a bearer
credential with no password and no second factor — see
``services/owners/credentials.py`` for the limits, stated plainly. What it buys
is the ability to answer "whose recordings are these?" without building an
account system, and to answer it *on the server*: every repository read below is
scoped by the resolved owner id in SQL.

Since Step 10.2 this module is a **thin HTTP adapter and nothing more**. It
normalises a header and hands the value to an
:class:`~app.services.owners.identity.IdentityResolver`; every decision about
*who* the caller is lives in the resolver, which is what lets a second one be
added without touching a single route.
"""

import re
from datetime import timedelta
from typing import Annotated, Final

from fastapi import Header, Request, Response

from app.api.rate_limit import guard_new_identity
from app.core.logging import get_logger
from app.services.owners.credentials import CredentialRepository
from app.services.owners.identity import IdentityResolver
from app.services.owners.models import TOKEN_PATTERN, Owner
from app.services.owners.repository import OwnerRepository
from app.services.owners.resolver import BearerKeyResolver, malformed_key_error

logger = get_logger(__name__)

#: The header carrying the identity, in both directions.
OWNER_HEADER: Final = "X-VocalLens-Owner"

_TOKEN_RE: Final = re.compile(TOKEN_PATTERN)


def clean_key(raw: str | None) -> str | None:
    """Normalise the header value, or refuse one that cannot be a key.

    Whitespace-only is the same as absent. A **malformed** value is refused
    rather than treated as absent: it cannot be a key this server issued, so
    ignoring it would hide a client bug behind a silently changing identity.
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if not _TOKEN_RE.match(key):
        raise malformed_key_error(OWNER_HEADER)
    return key


async def resolve_owner(
    request: Request,
    response: Response,
    owners: OwnerRepository,
    credentials: CredentialRepository,
    token: str | None,
    activity_throttle: timedelta,
) -> Owner:
    """Resolve the caller through the bearer-key resolver.

    A thin HTTP adapter: it normalises the header, hands the key to an
    :class:`IdentityResolver`, and writes a freshly minted key to the response
    header exactly once. Every decision about *who* the caller is belongs to the
    resolver, which is what lets a second one be added without touching this.

    The minted key never reaches a body and is never logged — a header keeps it
    out of the places request bodies end up.
    """
    resolver: IdentityResolver = BearerKeyResolver(
        owners=owners,
        credentials=credentials,
        key=clean_key(token),
        on_mint=lambda minted: response.headers.__setitem__(OWNER_HEADER, minted),
        # The one place the identity boundary meets the rate limit. It is passed
        # in rather than reached for, so the resolver stays a domain object that
        # knows nothing about addresses, and a test can substitute a guard that
        # counts calls.
        before_mint=lambda: guard_new_identity(request),
        activity_throttle=activity_throttle,
    )
    return await resolver.resolve()


OwnerTokenHeader = Annotated[
    str | None,
    Header(
        alias=OWNER_HEADER,
        description=(
            "Anonymous owner identifier. Omit it on a first request: the "
            "response returns a newly minted value in the same header, which "
            "the client stores and sends on every later request to see its own "
            "recording history. It is a bearer identifier, not a login."
        ),
    ),
]


__all__ = ["OWNER_HEADER", "OwnerTokenHeader", "clean_key", "resolve_owner"]
