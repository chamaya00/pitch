"""The identity seam, asserted rather than described.

Step 10.1 declared :class:`IdentityResolver` and consumed it nowhere: the API
called the bearer-key function directly, so "a second resolver would drop in"
was a claim about code that did not exist. This file is the evidence for the
claim.

The load-bearing test is :func:`test_a_second_resolver_needs_no_route_change`.
It substitutes a resolver that establishes identity a completely different way —
no header, no minting, no key — and then reads the *whole* owner-scoped product
through it. If any route, schema or service had learned how identity is
established, that test is where it would show.

The rest is what a resolver must never do: take an owner id from the client,
return credential material, or answer differently for an owner that exists and
one that does not.
"""

import uuid
from datetime import timedelta
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.owner import OWNER_HEADER, clean_key
from app.core.errors import ApiError, ErrorCode
from app.main import create_app
from app.services.owners.credentials import new_credential
from app.services.owners.identity import IdentityResolver
from app.services.owners.models import Owner, new_owner, new_owner_token
from app.services.owners.resolver import BearerKeyResolver
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories

IDENTITY_URL = "/api/v1/identity"
RECORDINGS_URL = "/api/v1/recordings"
PROGRESS_URL = "/api/v1/recordings/progress"


def run(factory: Any) -> Any:
    return anyio.run(factory)


class CountingGuard:
    """A mint guard that records how often it was consulted, and may refuse.

    Substituted for the real rate limiter so these tests assert the *seam* —
    that the resolver asks before it writes — without depending on a window, a
    clock or an address. ``api/rate_limit.py`` is where the policy is tested.
    """

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if not self.allow:
            raise ApiError(ErrorCode.RATE_LIMITED, "no more identities from here")


def resolver_for(
    doubles: Doubles,
    key: str | None,
    minted: list[str],
    guard: CountingGuard | None = None,
) -> BearerKeyResolver:
    return BearerKeyResolver(
        owners=doubles.owners,
        credentials=doubles.credentials,
        key=key,
        on_mint=minted.append,
        before_mint=guard or CountingGuard(),
        # Zero, so every resolution records activity. The throttle itself is
        # tested where it lives — in the repository contract suite, against both
        # backends — rather than being smuggled into these tests.
        activity_throttle=timedelta(0),
    )


# --- What the protocol is --------------------------------------------------


def test_the_bearer_resolver_satisfies_the_protocol(doubles: Doubles) -> None:
    assert isinstance(resolver_for(doubles, None, []), IdentityResolver)


def test_a_known_key_resolves_to_its_owner_and_mints_nothing(doubles: Doubles) -> None:
    minted: list[str] = []

    resolved = run(lambda: resolver_for(doubles, doubles.token, minted).resolve())

    assert resolved.owner_id == doubles.owner.owner_id
    assert minted == []


def test_any_of_an_owners_keys_resolves_to_the_same_owner(doubles: Doubles) -> None:
    credential, key = new_credential(doubles.owner.owner_id, "Phone")
    run(lambda: doubles.credentials.create(credential, key))

    resolved = run(lambda: resolver_for(doubles, key, []).resolve())

    assert resolved.owner_id == doubles.owner.owner_id


def test_an_absent_key_mints_an_identity(doubles: Doubles) -> None:
    minted: list[str] = []

    resolved = run(lambda: resolver_for(doubles, None, minted).resolve())

    assert resolved.owner_id != doubles.owner.owner_id
    assert len(minted) == 1
    assert run(lambda: doubles.credentials.owner_for_key(minted[0])) == resolved.owner_id


def test_an_unrecognised_key_mints_rather_than_refusing(doubles: Doubles) -> None:
    """This is not authentication, so a 401 would be the wrong answer.

    It also means an unrecognised key reveals nothing about whether some other
    owner exists: the reply is identical to the reply for no key at all.
    """
    minted: list[str] = []

    resolved = run(lambda: resolver_for(doubles, new_owner_token(), minted).resolve())

    assert len(minted) == 1
    assert resolved.owner_id != doubles.owner.owner_id


def test_a_minted_key_is_handed_over_exactly_once(doubles: Doubles) -> None:
    minted: list[str] = []

    run(lambda: resolver_for(doubles, None, minted).resolve())

    assert len(minted) == 1


def test_the_resolver_never_returns_credential_material(doubles: Doubles) -> None:
    """``resolve`` returns an ``Owner``, and an ``Owner`` is an id and a date."""
    resolved = run(lambda: resolver_for(doubles, doubles.token, []).resolve())

    assert set(resolved.model_dump()) == {"owner_id", "created_at"}


@pytest.mark.parametrize("bad", ["short", "!" * 22, "x" * 23, " ".join(["a"] * 11)])
def test_a_malformed_key_is_refused_before_any_lookup(bad: str) -> None:
    with pytest.raises(ApiError):
        clean_key(bad)


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_key_is_the_same_as_no_key(blank: str | None) -> None:
    assert clean_key(blank) is None


# --- What the seam buys ----------------------------------------------------


class FixedOwnerResolver:
    """A resolver that establishes identity by an entirely different route.

    It stands in for the password or OAuth resolver this phase is building
    towards: no header, no key, no minting — just "I know who this is". If the
    product still works end to end through it, then identity really is resolved
    in one place.
    """

    def __init__(self, owner: Owner) -> None:
        self._owner = owner

    async def resolve(self) -> Owner:
        return self._owner


def test_a_second_resolver_needs_no_route_change(doubles: Doubles) -> None:
    """The whole point of the seam, exercised across the whole product."""
    recording = Recording.model_validate(
        {
            "recording_id": uuid.uuid4().hex,
            "original_filename": "take.wav",
            "audio_format": "wav",
            "duration_seconds": 12.0,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 4096,
        }
    )
    run(lambda: doubles.recordings.create(recording, doubles.owner.owner_id))

    resolver: IdentityResolver = FixedOwnerResolver(doubles.owner)
    assert isinstance(resolver, IdentityResolver)

    app = create_app()
    override_repositories(app, doubles)
    # No key anywhere: the substituted resolver is the only thing establishing
    # identity, so nothing here can be passing because of the header.
    app.dependency_overrides[deps.get_owner] = resolver.resolve
    client = TestClient(app, raise_server_exceptions=False)

    listed = client.get(RECORDINGS_URL).json()
    assert [item["recording"]["recording_id"] for item in listed["items"]] == [
        recording.recording_id
    ]
    assert client.get(f"{RECORDINGS_URL}/{recording.recording_id}").status_code == 200
    assert client.get(PROGRESS_URL).status_code == 200
    assert client.get(IDENTITY_URL).json()["recordings"] == 1


def test_the_substituted_resolver_owns_nothing_it_was_not_given(doubles: Doubles) -> None:
    """Swapping the resolver changes who is asking, never who owns what."""
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    recording = Recording.model_validate(
        {
            "recording_id": uuid.uuid4().hex,
            "original_filename": "theirs.wav",
            "audio_format": "wav",
            "duration_seconds": 9.0,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 2048,
        }
    )
    run(lambda: doubles.recordings.create(recording, doubles.owner.owner_id))

    app = create_app()
    override_repositories(app, doubles)
    app.dependency_overrides[deps.get_owner] = lambda: stranger
    client = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: doubles.token})

    assert client.get(f"{RECORDINGS_URL}/{recording.recording_id}").status_code == 404
    assert client.get(IDENTITY_URL).json()["recordings"] == 0


# --- The mint guard --------------------------------------------------------


def test_a_recognised_key_never_consults_the_mint_guard(doubles: Doubles) -> None:
    """The property that makes a per-address limit safe.

    Returning users share an address with strangers — an office, a school, a
    phone network. If resolving a *known* key consulted the guard, one
    stranger's behaviour could lock out everybody behind that address. It does
    not, so it cannot.
    """
    guard = CountingGuard()

    run(lambda: resolver_for(doubles, doubles.token, [], guard).resolve())

    assert guard.calls == 0


def test_minting_consults_the_guard_first(doubles: Doubles) -> None:
    guard = CountingGuard()

    run(lambda: resolver_for(doubles, None, [], guard).resolve())

    assert guard.calls == 1


def test_an_unrecognised_key_consults_the_guard(doubles: Doubles) -> None:
    """It is about to mint, so it is about to write."""
    guard = CountingGuard()

    run(lambda: resolver_for(doubles, new_owner_token(), [], guard).resolve())

    assert guard.calls == 1


def test_a_refused_mint_writes_nothing(doubles: Doubles) -> None:
    """The whole point: a refusal that still created the rows is not a refusal."""
    before = len(doubles.owners._by_id)  # noqa: SLF001 - counting rows is the assertion
    minted: list[str] = []
    guard = CountingGuard(allow=False)

    with pytest.raises(ApiError) as raised:
        run(lambda: resolver_for(doubles, None, minted, guard).resolve())

    assert raised.value.code is ErrorCode.RATE_LIMITED
    assert len(doubles.owners._by_id) == before  # noqa: SLF001
    assert minted == []


def test_a_refused_mint_leaves_existing_identities_reachable(doubles: Doubles) -> None:
    """Refusing a newcomer must not touch anybody who already has a key."""
    guard = CountingGuard(allow=False)

    with pytest.raises(ApiError):
        run(lambda: resolver_for(doubles, None, [], guard).resolve())

    resolved = run(lambda: resolver_for(doubles, doubles.token, []).resolve())
    assert resolved.owner_id == doubles.owner.owner_id
