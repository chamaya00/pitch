"""Counting requests per caller, per window.

Step 10.3 exists because a measurement, not a worry: sixty concurrent requests
carrying no credential at all created sixty owner rows and sixty credential rows
in 0.47 seconds — about 128 identities per second from one client. Every
owner-scoped route mints an identity when the key is absent or unrecognised, so
**an unauthenticated read is a write**, and nothing anywhere bounded how often.

This module is the counting, and only the counting. It knows nothing about HTTP,
requests, owners or credentials: it is given a key and answers whether that key
has had its allowance. What a key *is* — a client address, an owner id — is
decided in ``api/rate_limit.py``, which is where the two very different
questions "who is knocking?" and "whose identity is this?" are kept apart.

**A fixed window, not a token bucket.** A fixed window is a handful of lines,
has no background task, and its one weakness — up to twice the limit across a
window boundary — is irrelevant against an abuse rate four orders of magnitude
above the limit. A token bucket would be more elegant and would not change a
single outcome that matters here.

**Asking again never helps.** Once a window's allowance is spent every further
attempt in it is refused, hammering neither resets the count nor pushes the
window's end further away, and the only thing that restores an allowance is the
window ending. (Whether the counter keeps rising past the limit is not
observable in a fixed window and nothing here depends on it — a claim that it
mattered would be untestable, and was removed after a mutation showed the two
implementations are identical from outside.)

**Memory is bounded.** A limiter that grows a dictionary entry per distinct
caller is itself a memory-exhaustion vector — the precise failure it was added
to prevent. Expired entries are dropped as they are found, and the table is
capped: past the cap the oldest windows are evicted. Eviction is a real loss of
enforcement for the evicted key, and it is bounded and documented rather than
hidden — see :meth:`FixedWindowLimiter.check`.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.services.limits.limiter import Decision

#: How many distinct keys one limiter will track. Ten thousand entries of two
#: small integers is trivial memory; an attacker rotating source addresses past
#: this number is already past the point where an in-process limiter helps.
DEFAULT_MAX_KEYS: Final = 10_000


@dataclass(slots=True)
class _Window:
    started_at: float
    count: int


class FixedWindowLimiter:
    """Allow ``limit`` attempts per ``window_seconds`` for each key.

    The clock is injected so tests can advance time deterministically rather
    than sleeping. ``time.monotonic`` is the default and the right one: a
    wall-clock jump backwards — an NTP correction — would otherwise extend
    somebody's window arbitrarily.

    Not thread-safe, and does not need to be: the application runs one asyncio
    loop per process and :meth:`check` contains no ``await``, so it is atomic
    with respect to other coroutines. It is *not* shared between processes,
    which is stated plainly in :mod:`app.api.rate_limit` rather than left for
    somebody to discover.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._limit = limit
        self._window = float(window_seconds)
        self._clock = clock
        self._max_keys = max_keys
        self._windows: dict[str, _Window] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def check(self, key: str) -> Decision:
        """Record an attempt by ``key`` and say whether it may proceed.

        Counts the attempt whether or not it is allowed, so a refused caller
        does not get a free retry.
        """
        now = self._clock()
        window = self._windows.get(key)

        if window is None or now - window.started_at >= self._window:
            self._evict_if_full(now)
            self._windows[key] = _Window(started_at=now, count=1)
            return Decision(allowed=True)

        window.count += 1
        if window.count <= self._limit:
            return Decision(allowed=True)

        remaining = self._window - (now - window.started_at)
        # Round up, and never below 1: a client told to retry after 0 seconds
        # retries immediately, which is a retry storm rather than a back-off.
        return Decision(allowed=False, retry_after_seconds=max(1, int(remaining) + 1))

    def _evict_if_full(self, now: float) -> None:
        """Make room for a new key, dropping expired entries first.

        Expired entries are pure garbage, so they go before anything still
        enforcing. Only if that frees nothing is a live window evicted — the
        oldest, which has the least of its allowance left to protect.
        """
        if len(self._windows) < self._max_keys:
            return
        expired = [k for k, w in self._windows.items() if now - w.started_at >= self._window]
        for key in expired:
            del self._windows[key]
        if len(self._windows) < self._max_keys:
            return
        oldest = min(self._windows, key=lambda k: self._windows[k].started_at)
        del self._windows[oldest]

    def reset(self) -> None:
        """Forget every window. For tests and for nothing else."""
        self._windows.clear()

    def tracked_keys(self) -> int:
        """How many keys are being counted. Lets a test assert the memory bound."""
        return len(self._windows)


class InProcessRateLimiter:
    """:class:`FixedWindowLimiter` as a :class:`~app.services.limits.limiter.RateLimiter`.

    The algorithm above is a dictionary lookup and some arithmetic. It has no
    I/O to wait for, and it is left synchronous on purpose: that is what makes
    "``check`` contains no ``await``, so it is atomic with respect to other
    coroutines" a property of the code rather than a promise. Wrapping it here
    rather than making it ``async`` keeps that guarantee where it can be read,
    and keeps the counting testable without a clock, an event loop or a server.

    This is the default limiter, and for the bundled deployment it is also the
    correct one: ``docker-compose.yml`` runs a single uvicorn worker, so there
    is no second counter for it to disagree with. See
    :mod:`app.services.limits.postgres` for what changes when there is.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self._limiter = FixedWindowLimiter(
            limit=limit, window_seconds=window_seconds, clock=clock, max_keys=max_keys
        )

    @property
    def limit(self) -> int:
        return self._limiter.limit

    async def check(self, key: str) -> Decision:
        return self._limiter.check(key)

    def tracked_keys(self) -> int:
        return self._limiter.tracked_keys()

    def reset(self) -> None:
        """Forget every window. For tests and for nothing else."""
        self._limiter.reset()


__all__ = ["DEFAULT_MAX_KEYS", "Decision", "FixedWindowLimiter", "InProcessRateLimiter"]
