"""Reclaiming unused identities, and refusing to reclaim anything else.

The eligibility rule is two conditions and the second one is what keeps this
honest: an identity that owns recordings is never reclaimed, at any age. So most
of what follows is about what cleanup must *not* do.

The service is driven against the in-memory doubles here. The parts that only a
database can be asked — the row lock, the cascade, two runs contending — are in
``test_repository_contract`` and ``test_database``, against real PostgreSQL.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import anyio
import pytest

from app.services.audio.storage import RecordingStorage
from app.services.owners.deletion import OwnerDeletionService
from app.services.owners.models import new_owner
from app.services.owners.retention import (
    IdentityRetentionService,
    RetentionPolicy,
)
from app.services.recordings.models import Recording, utc_now
from tests.doubles import Doubles

RETENTION = timedelta(days=30)


def run(factory: Any) -> Any:
    return anyio.run(factory)


def policy() -> RetentionPolicy:
    return RetentionPolicy(retention=RETENTION)


def service(doubles: Doubles, tmp_path: Any) -> IdentityRetentionService:
    return IdentityRetentionService(
        repository=doubles.owners,
        deletion=OwnerDeletionService(
            owners=doubles.owners, storage=RecordingStorage(tmp_path / "storage")
        ),
        policy=policy(),
    )


def make_owner(doubles: Doubles, *, last_seen: datetime | None = None) -> uuid.UUID:
    owner, token = new_owner()
    run(lambda: doubles.owners.create(owner, token))
    if last_seen is not None:
        doubles.owners.set_last_seen(owner.owner_id, last_seen)
    return owner.owner_id


def give_recording(doubles: Doubles, owner_id: uuid.UUID) -> str:
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
    run(lambda: doubles.recordings.create(recording, owner_id))
    return recording.recording_id


def long_ago() -> datetime:
    return utc_now() - RETENTION - timedelta(days=1)


def recently() -> datetime:
    return utc_now() - timedelta(minutes=5)


# --- Eligibility, as a rule -------------------------------------------------


def test_a_new_identity_is_never_eligible() -> None:
    assert policy().is_eligible(last_seen_at=utc_now(), recordings=0, song_references=0) is False


def test_an_unused_empty_identity_past_the_period_is_eligible() -> None:
    assert policy().is_eligible(last_seen_at=long_ago(), recordings=0, song_references=0) is True


def test_an_identity_with_recordings_is_never_eligible_however_old() -> None:
    """The condition that turns this from a judgement call into a safe one."""
    ancient = utc_now() - timedelta(days=3650)

    assert policy().is_eligible(last_seen_at=ancient, recordings=1, song_references=0) is False


def test_a_recently_seen_identity_is_never_eligible_however_empty() -> None:
    assert policy().is_eligible(last_seen_at=recently(), recordings=0, song_references=0) is False


def test_the_boundary_is_deterministic() -> None:
    """Exactly at the cutoff is retained; a moment older is eligible.

    Stated explicitly because "older than 30 days" is the kind of comparison
    that silently flips when somebody tidies an operator.
    """
    now = utc_now()
    at = now - RETENTION
    older = at - timedelta(microseconds=1)

    assert policy().is_eligible(last_seen_at=at, recordings=0, song_references=0, now=now) is False
    assert (
        policy().is_eligible(last_seen_at=older, recordings=0, song_references=0, now=now) is True
    )


def test_the_period_is_configuration_not_a_constant() -> None:
    seen = utc_now() - timedelta(days=10)

    empty = {"recordings": 0, "song_references": 0}
    assert RetentionPolicy(timedelta(days=30)).is_eligible(last_seen_at=seen, **empty) is False
    assert RetentionPolicy(timedelta(days=7)).is_eligible(last_seen_at=seen, **empty) is True


# --- What a run does --------------------------------------------------------


def test_an_expired_empty_identity_is_reclaimed(doubles: Doubles, tmp_path: Any) -> None:
    owner_id = make_owner(doubles, last_seen=long_ago())

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 1
    assert report.ok
    assert run(lambda: doubles.owners.get(owner_id)) is None


def test_its_credentials_go_with_it(doubles: Doubles, tmp_path: Any) -> None:
    owner_id = make_owner(doubles, last_seen=long_ago())
    assert run(lambda: doubles.credentials.list_for_owner(owner_id))

    run(lambda: service(doubles, tmp_path).run())

    assert run(lambda: doubles.credentials.list_for_owner(owner_id)) == []


def test_a_fresh_identity_is_left_alone(doubles: Doubles, tmp_path: Any) -> None:
    owner_id = make_owner(doubles)

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 0
    assert run(lambda: doubles.owners.get(owner_id)) is not None


def test_a_recently_used_identity_is_left_alone(doubles: Doubles, tmp_path: Any) -> None:
    """Old, but somebody used it this morning."""
    owner_id = make_owner(doubles, last_seen=long_ago())
    run(lambda: doubles.owners.touch(owner_id, timedelta(0)))

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 0
    assert run(lambda: doubles.owners.get(owner_id)) is not None


def test_an_identity_with_a_recording_is_left_alone(doubles: Doubles, tmp_path: Any) -> None:
    """Never, at any age. Deleting this would destroy somebody's history."""
    owner_id = make_owner(doubles, last_seen=long_ago())
    recording_id = give_recording(doubles, owner_id)

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 0
    assert run(lambda: doubles.owners.get(owner_id)) is not None
    assert run(lambda: doubles.recordings.get(recording_id, owner_id)) is not None


def test_only_eligible_identities_are_touched(doubles: Doubles, tmp_path: Any) -> None:
    expired = make_owner(doubles, last_seen=long_ago())
    active = make_owner(doubles, last_seen=recently())
    with_data = make_owner(doubles, last_seen=long_ago())
    kept_recording = give_recording(doubles, with_data)

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 1
    assert run(lambda: doubles.owners.get(expired)) is None
    assert run(lambda: doubles.owners.get(active)) is not None
    assert run(lambda: doubles.owners.get(with_data)) is not None
    assert run(lambda: doubles.recordings.get(kept_recording, with_data)) is not None


def test_another_owners_credentials_are_untouched(doubles: Doubles, tmp_path: Any) -> None:
    expired = make_owner(doubles, last_seen=long_ago())
    keeper = make_owner(doubles, last_seen=recently())
    before = run(lambda: doubles.credentials.list_for_owner(keeper))

    run(lambda: service(doubles, tmp_path).run())

    assert run(lambda: doubles.owners.get(expired)) is None
    assert run(lambda: doubles.credentials.list_for_owner(keeper)) == before


def test_the_batch_limit_is_respected(doubles: Doubles, tmp_path: Any) -> None:
    for _ in range(5):
        make_owner(doubles, last_seen=long_ago())

    report = run(lambda: service(doubles, tmp_path).run(limit=2))

    assert report.inspected == 2
    assert report.deleted == 2


def test_the_oldest_are_reclaimed_first(doubles: Doubles, tmp_path: Any) -> None:
    oldest = make_owner(doubles, last_seen=utc_now() - timedelta(days=400))
    newer = make_owner(doubles, last_seen=long_ago())

    run(lambda: service(doubles, tmp_path).run(limit=1))

    assert run(lambda: doubles.owners.get(oldest)) is None
    assert run(lambda: doubles.owners.get(newer)) is not None


# --- Idempotency ------------------------------------------------------------


def test_running_twice_is_safe(doubles: Doubles, tmp_path: Any) -> None:
    make_owner(doubles, last_seen=long_ago())
    keeper = make_owner(doubles, last_seen=recently())

    first = run(lambda: service(doubles, tmp_path).run())
    second = run(lambda: service(doubles, tmp_path).run())

    assert first.deleted == 1
    assert second.deleted == 0
    assert second.inspected == 0
    assert second.ok
    assert run(lambda: doubles.owners.get(keeper)) is not None


def test_a_run_against_an_empty_database_reports_nothing(doubles: Doubles, tmp_path: Any) -> None:
    report = run(lambda: service(doubles, tmp_path).run())

    assert (report.inspected, report.deleted, report.failed) == (0, 0, 0)
    assert report.ok


# --- Failure is counted, never swallowed ------------------------------------


class ExplodingRepository:
    """Wraps the double and fails on the claim."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def expired_owner_ids(self, cutoff: datetime, limit: int) -> list[uuid.UUID]:
        return await self._inner.expired_owner_ids(cutoff, limit)

    async def claim_expired_owner(self, owner_id: uuid.UUID, cutoff: datetime) -> bool:
        raise RuntimeError("the database went away")


def test_a_failed_deletion_is_reported_not_swallowed(doubles: Doubles, tmp_path: Any) -> None:
    """A run that lost a deletion must not be able to claim success."""
    owner_id = make_owner(doubles, last_seen=long_ago())
    failing = IdentityRetentionService(
        repository=ExplodingRepository(doubles.owners),
        deletion=OwnerDeletionService(
            owners=doubles.owners, storage=RecordingStorage(tmp_path / "storage")
        ),
        policy=policy(),
    )

    report = run(lambda: failing.run())

    assert report.failed == 1
    assert report.deleted == 0
    assert report.ok is False
    # And the owner is still there, so a retry can finish the job.
    assert run(lambda: doubles.owners.get(owner_id)) is not None


def test_a_retry_after_a_failure_completes_the_job(doubles: Doubles, tmp_path: Any) -> None:
    owner_id = make_owner(doubles, last_seen=long_ago())
    failing = IdentityRetentionService(
        repository=ExplodingRepository(doubles.owners),
        deletion=OwnerDeletionService(
            owners=doubles.owners, storage=RecordingStorage(tmp_path / "storage")
        ),
        policy=policy(),
    )
    run(lambda: failing.run())

    report = run(lambda: service(doubles, tmp_path).run())

    assert report.deleted == 1
    assert run(lambda: doubles.owners.get(owner_id)) is None


class LyingRepository:
    """A repository whose every safety check has failed.

    It offers an owner that owns recordings as a candidate **and** claims the
    claim succeeded. That is deliberate: if it only lied about the candidate
    list, the double's own claim would still refuse and the test would pass
    without the second opinion ever mattering — which is exactly what the first
    version of this test did, and a mutation removing the second opinion
    survived it.

    With both checks lying, the service's own ``summary`` call is the only thing
    left between a candidate and a wrongful deletion.
    """

    def __init__(self, owner_id: uuid.UUID) -> None:
        self._owner_id = owner_id
        self.claims = 0

    async def expired_owner_ids(self, cutoff: datetime, limit: int) -> list[uuid.UUID]:
        return [self._owner_id]

    async def claim_expired_owner(self, owner_id: uuid.UUID, cutoff: datetime) -> bool:
        self.claims += 1
        return True


def test_a_candidate_that_owns_recordings_is_refused(doubles: Doubles, tmp_path: Any) -> None:
    """Two reads must agree before a row is touched."""
    owner_id = make_owner(doubles, last_seen=long_ago())
    recording_id = give_recording(doubles, owner_id)
    repository = LyingRepository(owner_id)
    lying = IdentityRetentionService(
        repository=repository,
        deletion=OwnerDeletionService(
            owners=doubles.owners, storage=RecordingStorage(tmp_path / "storage")
        ),
        policy=policy(),
    )

    report = run(lambda: lying.run())

    assert report.deleted == 0
    assert report.skipped == 1
    # The claim was never even attempted: the second opinion stopped it first.
    assert repository.claims == 0
    assert run(lambda: doubles.owners.get(owner_id)) is not None
    assert run(lambda: doubles.recordings.get(recording_id, owner_id)) is not None


# --- The activity signal ----------------------------------------------------


def test_resolving_an_identity_records_activity(doubles: Doubles) -> None:
    owner_id = make_owner(doubles, last_seen=long_ago())

    run(lambda: doubles.owners.touch(owner_id, timedelta(0)))

    seen = doubles.owners.last_seen(owner_id)
    assert seen is not None
    assert seen > long_ago()


def test_activity_is_throttled(doubles: Doubles) -> None:
    """A busy identity costs one write per interval, not one per request."""
    owner_id = make_owner(doubles, last_seen=recently())
    before = doubles.owners.last_seen(owner_id)

    for _ in range(20):
        run(lambda: doubles.owners.touch(owner_id, timedelta(hours=1)))

    assert doubles.owners.last_seen(owner_id) == before


@pytest.mark.parametrize("throttle", [timedelta(0), timedelta(seconds=1)])
def test_a_stale_signal_is_always_refreshed(doubles: Doubles, throttle: timedelta) -> None:
    owner_id = make_owner(doubles, last_seen=long_ago())

    run(lambda: doubles.owners.touch(owner_id, throttle))

    seen = doubles.owners.last_seen(owner_id)
    assert seen is not None
    assert seen > utc_now() - timedelta(minutes=1)


def test_an_identity_holding_only_song_references_is_never_eligible() -> None:
    """The rule that "empty" means empty, not "has no recordings".

    Step 11.3 gave an identity a second kind of thing it can own. Until then the
    two sentences were the same one; an owner who had typed a song reference and
    uploaded nothing would have been reclaimed by the older rule, silently
    taking the references with it.
    """
    assert policy().is_eligible(last_seen_at=long_ago(), recordings=0, song_references=1) is False


def test_holding_both_kinds_of_thing_is_still_not_eligible() -> None:
    assert policy().is_eligible(last_seen_at=long_ago(), recordings=2, song_references=3) is False
