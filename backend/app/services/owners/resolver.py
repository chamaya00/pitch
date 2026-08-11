"""How a caller becomes an owner.

Step 10.1 declared :class:`IdentityResolver` and then never used it — the
protocol appeared in its own module and nowhere else, while the API called the
bearer-token function directly. The seam was documentation-shaped. This module
makes it code-shaped: there is one implementation today, it is behind the
protocol, and the API depends on the protocol rather than on it.

That matters for the thing this whole phase is about. A password or OAuth
resolver becomes a **second class implementing one method**, returning the same
``owner_id`` that already owns the recordings. Routes do not change, domain
services do not change, and nothing migrates.

What a resolver may not do is as important as what it does:

* it never takes an owner id from the client — the client presents a credential
  and the server decides which owner that is;
* it never returns a credential, a hash, or anything about how resolution
  happened;
* it never reveals whether an owner exists. An unrecognised key is answered the
  same way as an absent one.
"""

from collections.abc import Callable

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.owners.credentials import CredentialRepository
from app.services.owners.models import Owner, new_owner
from app.services.owners.repository import OwnerRepository

logger = get_logger(__name__)

#: What to do with a freshly minted key. A callback rather than a return value,
#: so ``resolve`` has one return type whatever happened. The API's
#: implementation writes it to a response header; a test's records it. Nothing
#: else is allowed to see it.
MintCallback = Callable[[str], None]


class BearerKeyResolver:
    """Resolves the anonymous bearer key, minting an identity when there is none.

    The only resolver in the system today. It is *not* authentication and does
    not pretend to be: whoever holds the key is the owner, there is no password
    and no second factor. See ``services/owners/credentials.py``.

    Two behaviours are deliberate and were settled in Step 7M:

    **An absent key mints a new identity.** A first-time visitor is not an error.

    **A well-formed but unrecognised key also mints one**, rather than refusing.
    A 401 would be right for authentication; this is not authentication, and a
    client holding a key from a reset database would otherwise be permanently
    stuck with an error it cannot clear. It also means an unrecognised key
    reveals nothing about whether some other owner exists.

    A **malformed** key still raises: it cannot be one this server issued, so
    treating it as absent would hide a client bug behind a silently changing
    identity.
    """

    def __init__(
        self,
        *,
        owners: OwnerRepository,
        credentials: CredentialRepository,
        key: str | None,
        on_mint: MintCallback,
    ) -> None:
        self._owners = owners
        self._credentials = credentials
        self._key = key
        self._on_mint = on_mint

    async def resolve(self) -> Owner:
        """Return the caller's owner. See the class docstring for the rules."""
        if self._key is not None:
            owner_id = await self._credentials.owner_for_key(self._key)
            if owner_id is not None:
                owner = await self._owners.get(owner_id)
                if owner is not None:
                    return owner
                # A credential whose owner is gone. Only reachable if a delete
                # raced this lookup; the foreign key removes credentials with
                # their owner, so there is nothing to resolve to.
                logger.warning("credential_without_owner")
            else:
                logger.info("owner_key_unrecognised")

        owner, minted = new_owner()
        await self._owners.create(owner, minted)
        # The id is safe to log; the key is not, and is not passed to the logger
        # in any form.
        logger.info("owner_created", extra={"owner_id": str(owner.owner_id)})
        self._on_mint(minted)
        return owner


def malformed_key_error(header: str) -> ApiError:
    """The documented refusal for a value that cannot be a key."""
    return ApiError(
        ErrorCode.VALIDATION_ERROR,
        f"The {header} header is not a valid identifier.",
    )


__all__ = ["BearerKeyResolver", "MintCallback", "malformed_key_error"]
