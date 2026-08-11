"""Deciding who is knocking, and how often they may.

Two limits, keyed by two different things, because they answer two different
questions.

**New identities are limited per client address.** The measured problem was that
an unauthenticated request *mints* an owner: sixty concurrent requests carrying
no credential created sixty owner rows and sixty credential rows in under half a
second. There is no identity to key that on — creating one is the thing being
limited — so the only handle is where the request came from.

**Costly requests are limited per owner.** Uploading, starting an analysis,
asking for generated feedback and adding a key all consume disk, CPU or provider
budget. Keying these by address would make one person on an office network able
to spend everybody else's allowance; keying them by owner cannot. It also means
a limit can never be reached by someone merely *reading* their own history.

Together they bound the whole surface: a client may create N identities per
window, and each identity may spend M costly requests per window.

**What this is not.** It is one process's memory. Two API workers have two
counters, so the effective limit is multiplied by the worker count — the same
honest caveat ``0001_initial.sql`` records about the ``asyncio.Lock`` the
partial unique indexes replaced. It is defence in depth beside a real edge
limiter, exactly as ``MaxBodySizeMiddleware`` is defence in depth beside a
proxy's body cap, and it is not a defence against a distributed attacker with
many source addresses.

A database-backed counter was considered and rejected for this slice: it would
add a write to every request in order to bound the cost of writes, which is the
wrong shape. If a shared counter is ever needed, it belongs behind a protocol
here, not inside the resolver.
"""

import uuid
from typing import Final

from fastapi import Request

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.limits.window import Decision, FixedWindowLimiter

logger = get_logger(__name__)

#: Used when the peer address is unavailable — ASGI allows ``client`` to be
#: ``None``, and a test client has no socket. Everything unattributable shares
#: one bucket, which is the safe direction: it can over-limit an unusual
#: deployment, never under-limit an attacker.
UNKNOWN_CLIENT: Final = "unknown"

_FORWARDED_FOR: Final = "x-forwarded-for"


class RateLimits:
    """The limiters for one running application.

    Held on ``app.state`` rather than at module scope so that two applications
    in one test process — which is every API test file — cannot share counters
    and refuse each other's requests.
    """

    def __init__(self, settings: Settings) -> None:
        self.new_identities = FixedWindowLimiter(
            limit=settings.rate_limit_new_identities,
            window_seconds=settings.rate_limit_new_identities_window_seconds,
        )
        self.costly = FixedWindowLimiter(
            limit=settings.rate_limit_costly_requests,
            window_seconds=settings.rate_limit_costly_requests_window_seconds,
        )
        self.trusted_proxies = settings.rate_limit_trusted_proxies


def client_address(request: Request, trusted_proxies: int) -> str:
    """Which address to hold responsible for this request.

    With no trusted proxy the peer address is the client, and
    ``X-Forwarded-For`` is ignored entirely — it is client-supplied, so
    honouring it would let anyone bypass the limit by inventing a header.

    With ``trusted_proxies = n`` the client is ``n`` hops back from the
    right-hand end of the header, because a proxy appends the address it saw.
    Entries further left were written by whoever is being limited and are worth
    nothing. If the header is too short to reach that far, the peer address is
    used: a truncated header means the request did not arrive the way the
    configuration claims, and guessing would be a bypass.
    """
    peer = request.client.host if request.client is not None else UNKNOWN_CLIENT
    if trusted_proxies < 1:
        return peer

    raw = request.headers.get(_FORWARDED_FOR)
    if not raw:
        return peer
    hops = [part.strip() for part in raw.split(",") if part.strip()]
    index = len(hops) - trusted_proxies
    if index < 0 or index >= len(hops):
        return peer
    return hops[index]


def _limits(request: Request) -> RateLimits | None:
    limits: RateLimits | None = getattr(request.app.state, "rate_limits", None)
    return limits


def _refuse(decision: Decision, message: str) -> ApiError:
    """The documented refusal.

    Carries ``Retry-After`` because a 429 without one tells a client to back off
    without saying how far, which produces the retry storm the limit exists to
    prevent. It carries nothing else: not the limit, not the count, not the key
    it was counted under — a refusal is not a place to describe the defence.
    """
    return ApiError(
        ErrorCode.RATE_LIMITED,
        message,
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def guard_new_identity(request: Request) -> None:
    """Refuse if this client has created too many identities.

    Called from the identity boundary **only when an identity would actually be
    minted** — a caller presenting a recognised key never reaches it, so a
    returning user behind a shared address is never refused for a stranger's
    behaviour.
    """
    limits = _limits(request)
    if limits is None:  # pragma: no cover - only when an app is built without state
        return
    decision = limits.new_identities.check(client_address(request, limits.trusted_proxies))
    if decision.allowed:
        return
    # The address is deliberately absent from this log line: it is the thing
    # being counted, not something worth recording against a person.
    logger.warning("rate_limited", extra={"limit": "new_identities"})
    raise _refuse(
        decision,
        "Too many new identities have been created from here recently. "
        "Please wait and try again — if you already have a key, use it and this "
        "limit will not apply.",
    )


async def limit_costly_request(request: Request, owner_id: uuid.UUID) -> None:
    """Refuse if this owner has made too many costly requests.

    Keyed by owner, so it bounds what one identity may spend and can never be
    exhausted by somebody else sharing an address.
    """
    limits = _limits(request)
    if limits is None:  # pragma: no cover - only when an app is built without state
        return
    decision = limits.costly.check(str(owner_id))
    if decision.allowed:
        return
    logger.warning("rate_limited", extra={"limit": "costly"})
    raise _refuse(
        decision,
        "You have made a lot of requests in a short time. Please wait a moment "
        "and try again — nothing you have already recorded is affected.",
    )


__all__ = [
    "UNKNOWN_CLIENT",
    "RateLimits",
    "client_address",
    "guard_new_identity",
    "limit_costly_request",
]
