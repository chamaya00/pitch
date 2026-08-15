"""What a rate limiter is, separately from where it keeps its count.

Step 10.3 built one limiter and was explicit about what it was not:

    It is one process's memory. Two API workers have two counters, so the
    effective limit is multiplied by the worker count.

That caveat was written down in three places and never measured. It is measured
now, against a real server, before anything here was built: four uvicorn workers
sharing a limit of five new identities per hour minted **twenty** — exactly
4.0× — where one worker minted five. Two workers minted ten. Eight workers
minted thirty-two, against a ceiling of forty that only more traffic would have
reached. The multiplier is not approximately the worker count; it *is* the
worker count.

**Why a shared counter is buildable now, when 10.3 said it was not.** 10.3
rejected a database-backed counter in one sentence:

    it would add a write to every request in order to bound the cost of writes,
    which is the wrong shape.

That reasoning does not survive contact with the two call sites. Neither guard
runs on every request:

* ``guard_new_identity`` is reached only from ``before_mint``, which the
  resolver consults **immediately before inserting an owner row and a
  credential row**. A caller presenting a recognised key returns before it.
* ``limit_costly_request`` is declared on uploading, starting either analysis,
  asking for feedback and adding a key — every one of which writes.

Reading is never limited, so no read acquires a write it did not already have,
and the requests that *are* counted were each about to write anyway. The
objection was true of a limiter on every request and false of this one. Step
10.6 reached the same conclusion from the other side: it added a write to
``owners.last_seen_at`` and made it sound by throttling, having quoted 10.3's
objection in its own migration.

**Two implementations, one contract.** The protocol below is what
``api/rate_limit.py`` depends on, so nothing at the call site knows whether the
count lives in this process's memory or in PostgreSQL. The in-process limiter
remains the default: the bundled ``docker-compose.yml`` runs a single uvicorn
worker, where it is already exactly correct and costs nothing. The shared
counter is what a deployment scaling past one worker turns on.

**Async, because one implementation does I/O.** ``FixedWindowLimiter`` is a
dictionary lookup and needs no ``await``; it is deliberately left synchronous
and I/O-free in :mod:`app.services.limits.window`, with
:class:`~app.services.limits.window.InProcessRateLimiter` as its face here. A
protocol shaped around the cheaper implementation would have no room for the
other one.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether this attempt is allowed, and when to come back if not."""

    allowed: bool
    #: Seconds until the current window ends. Always at least 1 when refused —
    #: ``Retry-After: 0`` invites an immediate retry, which is the opposite of
    #: what a refusal is for.
    retry_after_seconds: int = 0


@runtime_checkable
class RateLimiter(Protocol):
    """Counts attempts by key and says whether this one may proceed.

    The key is a string and the limiter does not care what it means. What a key
    *is* — a client address, an owner id — is decided in ``api/rate_limit.py``,
    which is where the two very different questions "who is knocking?" and
    "whose identity is this?" are kept apart.

    An implementation **counts the attempt whether or not it allows it**, so a
    refused caller does not earn a free retry by asking again.
    """

    async def check(self, key: str) -> Decision:
        """Record an attempt by ``key`` and say whether it may proceed."""
        ...


__all__ = ["Decision", "RateLimiter"]
