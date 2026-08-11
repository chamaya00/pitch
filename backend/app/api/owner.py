"""Resolving the caller's owner identity.

One header in, one header out:

* ``X-VocalLens-Owner`` on the request names an existing owner.
* ``X-VocalLens-Owner`` on the response carries a **newly minted** token, and
  only then. A client stores it and sends it back; a client that does not simply
  gets a new identity next time and sees no history.

**This is not authentication and is not presented as one.** The token is a
bearer credential with no password, no second factor and no revocation — see
``services/owners/models.py`` for the limits, stated plainly. What it buys is
the ability to answer "whose recordings are these?" without building an account
system, and to answer it *on the server*: every repository read below is scoped
by the resolved owner id in SQL.

Two decisions worth spelling out.

**An unknown token mints a new owner rather than failing.** A 401 would be the
right answer if this were authentication; it is not, and a client holding a
token from a wiped database would be permanently stuck with an error it cannot
clear. Minting is the same outcome as arriving with no token at all.

**A malformed token is rejected, not minted from.** It cannot be a token this
server issued, so treating it as "absent" would hide a client bug behind a
silently changing identity.
"""

import re
from typing import Annotated, Final

from fastapi import Header, Request, Response

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.owners.models import TOKEN_PATTERN, Owner, new_owner
from app.services.owners.repository import OwnerRepository

logger = get_logger(__name__)

#: The header carrying the identity, in both directions.
OWNER_HEADER: Final = "X-VocalLens-Owner"

_TOKEN_RE: Final = re.compile(TOKEN_PATTERN)


async def resolve_owner(
    request: Request,
    response: Response,
    owners: OwnerRepository,
    token: str | None,
) -> Owner:
    """Return the caller's owner, minting one if they arrived without a valid token.

    The minted token is written to the response header exactly once, at
    creation. It is never logged and never returned in a body — a header keeps
    it out of the places request bodies end up.
    """
    if token is not None:
        token = token.strip()
        if not token:
            token = None
        elif not _TOKEN_RE.match(token):
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                f"The {OWNER_HEADER} header is not a valid identifier.",
            )

    if token is not None:
        existing = await owners.get_by_token(token)
        if existing is not None:
            return existing
        # A well-formed token this server does not know: the database was reset,
        # or the value was invented. Either way, mint rather than refuse.
        logger.info("owner_token_unrecognised", extra={"path": request.url.path})

    owner, minted = await _mint(owners)
    response.headers[OWNER_HEADER] = minted
    return owner


async def _mint(owners: OwnerRepository) -> tuple[Owner, str]:
    owner, token = new_owner()
    await owners.create(owner, token)
    # The id is safe to log; the token is not, and is not passed to the logger
    # in any form.
    logger.info("owner_created", extra={"owner_id": str(owner.owner_id)})
    return owner, token


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


__all__ = ["OWNER_HEADER", "OwnerTokenHeader", "resolve_owner"]
