"""The counting, and the decision about who is counted.

Two units, tested apart from HTTP because both are decisions rather than
plumbing: how many attempts fit in a window, and which string a request is
counted under. The clock is injected, so nothing here sleeps.

The rules that matter and are easy to get wrong:

* asking again after a refusal earns nothing — neither a reset count nor an
  extended window;
* the window ends, or the limit is a permanent ban;
* ``Retry-After`` is never zero, or a refusal is an invitation to retry now;
* memory is bounded, or the limiter is itself the exhaustion vector;
* ``X-Forwarded-For`` is ignored unless a proxy is configured, or anyone who
  reads the source can bypass the limit with one header.
"""

import pytest
from fastapi import Request

from app.api.rate_limit import UNKNOWN_CLIENT, client_address
from app.services.limits.window import Decision, FixedWindowLimiter


class Clock:
    """A hand-cranked monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limiter(limit: int = 3, window: int = 60, clock: Clock | None = None) -> FixedWindowLimiter:
    return FixedWindowLimiter(limit=limit, window_seconds=window, clock=clock or Clock())


def request_from(client: tuple[str, int] | None, headers: dict[str, str] | None = None) -> Request:
    """A Starlette request with just enough scope to be asked its address."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": client})


# --- Counting ---------------------------------------------------------------


def test_attempts_up_to_the_limit_are_allowed() -> None:
    bucket = limiter(limit=3)

    assert [bucket.check("a").allowed for _ in range(3)] == [True, True, True]


def test_the_attempt_past_the_limit_is_refused() -> None:
    bucket = limiter(limit=3)
    for _ in range(3):
        bucket.check("a")

    assert bucket.check("a").allowed is False


def test_hammering_never_earns_an_early_allowance() -> None:
    """A refusal that reset the count would hand out free retries forever."""
    clock = Clock()
    bucket = limiter(limit=3, window=60, clock=clock)
    for _ in range(3):
        assert bucket.check("a").allowed is True

    assert [bucket.check("a").allowed for _ in range(50)] == [False] * 50
    clock.advance(59)
    assert bucket.check("a").allowed is False, "the window ended early"


def test_hammering_does_not_extend_the_window() -> None:
    """The mirror failure: a refusal that restarted the window is a permanent ban."""
    clock = Clock()
    bucket = limiter(limit=1, window=60, clock=clock)
    bucket.check("a")
    for _ in range(30):
        clock.advance(1)
        assert bucket.check("a").allowed is False

    # 30 seconds of hammering, then the rest of the original window.
    clock.advance(30)

    assert bucket.check("a").allowed is True, "attempts pushed the window's end away"


def test_keys_are_counted_separately() -> None:
    bucket = limiter(limit=1)
    bucket.check("a")

    assert bucket.check("b").allowed is True


def test_the_window_ends() -> None:
    clock = Clock()
    bucket = limiter(limit=2, window=60, clock=clock)
    bucket.check("a")
    bucket.check("a")
    assert bucket.check("a").allowed is False

    clock.advance(60)

    assert bucket.check("a").allowed is True


def test_retry_after_counts_down_and_is_never_zero() -> None:
    clock = Clock()
    bucket = limiter(limit=1, window=60, clock=clock)
    bucket.check("a")

    early = bucket.check("a")
    clock.advance(59.5)
    late = bucket.check("a")

    assert early.retry_after_seconds > late.retry_after_seconds
    assert late.retry_after_seconds >= 1, "Retry-After: 0 is an invitation to retry now"
    assert early.retry_after_seconds <= 61


def test_an_allowed_decision_asks_for_no_wait() -> None:
    assert limiter().check("a") == Decision(allowed=True, retry_after_seconds=0)


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)])
def test_a_nonsense_configuration_is_refused_at_construction(limit: int, window: int) -> None:
    with pytest.raises(ValueError):
        FixedWindowLimiter(limit=limit, window_seconds=window)


# --- Bounded memory ---------------------------------------------------------


def test_the_key_table_is_capped() -> None:
    """A limiter that grows per caller is the exhaustion vector it was added to stop."""
    bucket = FixedWindowLimiter(limit=1, window_seconds=60, clock=Clock(), max_keys=10)

    for i in range(500):
        bucket.check(f"client-{i}")

    assert bucket.tracked_keys() <= 10


def test_expired_windows_are_reclaimed_before_live_ones() -> None:
    clock = Clock()
    bucket = FixedWindowLimiter(limit=1, window_seconds=60, clock=clock, max_keys=4)
    for i in range(4):
        bucket.check(f"old-{i}")

    clock.advance(61)
    bucket.check("fresh")

    # The four expired entries were garbage; reclaiming them left room without
    # evicting anything that was still enforcing.
    assert bucket.tracked_keys() == 1
    assert bucket.check("fresh").allowed is False


def test_eviction_never_grows_past_the_cap_under_live_pressure() -> None:
    bucket = FixedWindowLimiter(limit=5, window_seconds=600, clock=Clock(), max_keys=8)

    for i in range(200):
        bucket.check(f"live-{i}")

    assert bucket.tracked_keys() <= 8


# --- Who is counted ---------------------------------------------------------


def test_the_peer_address_is_the_client_by_default() -> None:
    request = request_from(("203.0.113.7", 5000))

    assert client_address(request, trusted_proxies=0) == "203.0.113.7"


def test_a_forwarded_header_is_ignored_without_a_trusted_proxy() -> None:
    """The header is client-supplied. Honouring it would be a one-header bypass."""
    request = request_from(("203.0.113.7", 5000), {"X-Forwarded-For": "1.2.3.4"})

    assert client_address(request, trusted_proxies=0) == "203.0.113.7"


def test_a_missing_peer_falls_back_to_one_shared_bucket() -> None:
    request = request_from(None)

    assert client_address(request, trusted_proxies=0) == UNKNOWN_CLIENT


def test_one_trusted_proxy_reads_the_last_hop() -> None:
    """A proxy appends what it saw, so the right-hand end is the trustworthy end."""
    request = request_from(("10.0.0.2", 5000), {"X-Forwarded-For": "9.9.9.9, 198.51.100.4"})

    assert client_address(request, trusted_proxies=1) == "198.51.100.4"


def test_two_trusted_proxies_read_two_hops_back() -> None:
    request = request_from(
        ("10.0.0.2", 5000), {"X-Forwarded-For": "9.9.9.9, 198.51.100.4, 10.0.0.9"}
    )

    assert client_address(request, trusted_proxies=2) == "198.51.100.4"


def test_a_spoofed_prefix_cannot_move_the_counted_address() -> None:
    """The attacker controls the left of the header and gains nothing by it."""
    honest = request_from(("10.0.0.2", 5000), {"X-Forwarded-For": "198.51.100.4"})
    spoofed = request_from(
        ("10.0.0.2", 5000),
        {"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3, 198.51.100.4"},
    )

    assert client_address(honest, 1) == client_address(spoofed, 1) == "198.51.100.4"


def test_a_header_too_short_for_the_configured_hops_falls_back_to_the_peer() -> None:
    """A truncated header means the request did not arrive as configured."""
    request = request_from(("10.0.0.2", 5000), {"X-Forwarded-For": "198.51.100.4"})

    assert client_address(request, trusted_proxies=3) == "10.0.0.2"


def test_an_absent_header_with_a_configured_proxy_falls_back_to_the_peer() -> None:
    request = request_from(("10.0.0.2", 5000))

    assert client_address(request, trusted_proxies=1) == "10.0.0.2"


def test_whitespace_and_empty_entries_are_tolerated() -> None:
    request = request_from(("10.0.0.2", 5000), {"X-Forwarded-For": " 9.9.9.9 ,  198.51.100.4 , "})

    assert client_address(request, trusted_proxies=1) == "198.51.100.4"
