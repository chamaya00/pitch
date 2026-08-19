"""One suite, run against both repository implementations.

Every other suite in this project runs against the in-memory doubles, because a
test that needs a database server is a test that does not run. That trade is
only safe if the doubles behave like the database, and "I wrote them to" is not
evidence. This file is the evidence: each test below is parametrised over the
in-memory repositories *and* the PostgreSQL ones, so a double that disagrees
with SQL fails the same assertion the real one passes.

Without ``TEST_DATABASE_URL`` the PostgreSQL parameter skips and the in-memory
one still runs — so the contract is always exercised, and fully exercised
wherever a server exists. ``scripts/check.sh`` sets the variable when one is
reachable.

What is deliberately *not* here: the properties that only a database can have —
cross-process uniqueness, transactional rollback, cascade deletes. Those are
statements about PostgreSQL, not about an interface two implementations share,
and they live in ``test_database.py`` where they run against a real server or
not at all.
"""

import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import pytest

from app.db.migrate import apply_migrations
from app.db.pool import Database
from app.services.analysis.models import (
    Analysis,
    AnalysisStatus,
    Provenance,
    SpeechMetrics,
    Transcript,
    new_analysis_id,
)
from app.services.analysis.postgres_repository import (
    ActiveAnalysisExistsError,
    AnalysisConflictError,
    PostgresAnalysisRepository,
)
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
    PitchFields,
    new_audio_analysis_id,
)
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
    PostgresAudioAnalysisRepository,
)
from app.services.owners.credential_repository import PostgresCredentialRepository
from app.services.owners.credentials import (
    CredentialExistsError,
    LastCredentialError,
    new_credential,
)
from app.services.owners.models import Owner, new_owner
from app.services.owners.repository import PostgresOwnerRepository
from app.services.recordings.cursor import decode_cursor
from app.services.recordings.models import Recording
from app.services.recordings.postgres_repository import PostgresRecordingRepository
from tests.doubles import (
    InMemoryAnalysisRepository,
    InMemoryAudioAnalysisRepository,
    InMemoryCredentialRepository,
    InMemoryOwnerRepository,
    InMemoryRecordingRepository,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


class Backend:
    """A wired set of repositories, however they happen to be stored."""

    def __init__(
        self, owners: Any, credentials: Any, recordings: Any, analyses: Any, audio: Any
    ) -> None:
        self.owners = owners
        self.credentials = credentials
        self.recordings = recordings
        self.analyses = analyses
        self.audio = audio

    async def owner(self) -> Owner:
        owner, token = new_owner()
        await self.owners.create(owner, token)
        return owner


@asynccontextmanager
async def _memory_backend() -> AsyncIterator[Backend]:
    analyses = InMemoryAnalysisRepository()
    audio = InMemoryAudioAnalysisRepository()
    recordings = InMemoryRecordingRepository(analyses, audio)
    # The owner double needs the recordings back-reference to answer the data
    # summary and to cascade a deletion — without it, it reports zeroes where
    # SQL reports counts. The contract suite caught exactly that.
    owners = InMemoryOwnerRepository(recordings)
    yield Backend(
        owners=owners,
        # Built over the owner double's own store, because in PostgreSQL there
        # is one table: a key created through one must resolve through the other.
        credentials=InMemoryCredentialRepository(owners),
        recordings=recordings,
        analyses=analyses,
        audio=audio,
    )


@asynccontextmanager
async def _postgres_backend() -> AsyncIterator[Backend]:
    database = Database(DATABASE_URL)
    await database.open()
    try:
        async with database.transaction() as connection:
            await apply_migrations(connection)
        async with database.transaction() as connection:
            # Truncating owners cascades to every table that references one, so
            # each test starts from an empty schema rather than a shared one.
            await connection.execute("TRUNCATE owners CASCADE")
        yield Backend(
            owners=PostgresOwnerRepository(database),
            credentials=PostgresCredentialRepository(database),
            recordings=PostgresRecordingRepository(database),
            analyses=PostgresAnalysisRepository(database),
            audio=PostgresAudioAnalysisRepository(database),
        )
    finally:
        await database.close()


BACKENDS: dict[str, Any] = {"memory": _memory_backend, "postgres": _postgres_backend}


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: Any) -> Callable[[Any], Any]:
    """Run a coroutine factory against one backend, and return its result.

    Returned as a runner rather than a Backend because the PostgreSQL one owns
    a pool that has to be opened and closed inside the event loop the test runs
    on. A fixture that yielded an open pool from a different loop would work
    until it did not.
    """
    if request.param == "postgres" and not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; the PostgreSQL half needs a real database")

    factory = BACKENDS[request.param]

    def drive(work: Any) -> Any:
        async def main() -> Any:
            async with factory() as prepared:
                return await work(prepared)

        return anyio.run(main)

    return drive


# --- Helpers ---------------------------------------------------------------


def make_recording(**overrides: Any) -> Recording:
    payload: dict[str, Any] = {
        "recording_id": uuid.uuid4().hex,
        "original_filename": "take.wav",
        "audio_format": "wav",
        "duration_seconds": 2.0,
        "sample_rate": 22050,
        "channels": 1,
        "size_bytes": 4096,
    }
    payload.update(overrides)
    return Recording.model_validate(payload)


def make_analysis(recording_id: str, **overrides: Any) -> Analysis:
    payload: dict[str, Any] = {
        "analysis_id": new_analysis_id(),
        "recording_id": recording_id,
    }
    payload.update(overrides)
    return Analysis.model_validate(payload)


def make_audio(recording_id: str, **overrides: Any) -> AudioAnalysis:
    payload: dict[str, Any] = {
        "audio_analysis_id": new_audio_analysis_id(),
        "recording_id": recording_id,
    }
    payload.update(overrides)
    return AudioAnalysis.model_validate(payload)


def _points(count: int) -> list[dict[str, Any]]:
    """A timeline of ``count`` measured frames, for reads that must not return it."""
    return [
        {
            "timestamp_seconds": round(index * 0.023, 3),
            "frequency_hz": 440.0,
            "midi_note": 69,
            "note_name": "A4",
            "cents": 0.0,
            "confidence": 0.95,
        }
        for index in range(count)
    ]


def completed_audio(recording_id: str, **overrides: Any) -> AudioAnalysis:
    """A finished audio analysis, which is the only kind feedback can be claimed on."""
    from tests.test_audio_feedback import metrics  # reuse the built AudioMetrics fixture data

    return make_audio(
        recording_id,
        status=AudioAnalysisStatus.COMPLETED,
        metrics=metrics().model_dump(),
        completed_at=None,
        **overrides,
    )


# --- Owners ----------------------------------------------------------------


def test_an_owner_round_trips_by_token(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner, token = new_owner()
        await prepared.owners.create(owner, token)

        assert await prepared.owners.get_by_token(token) == owner

    backend(work)


def test_an_unknown_token_resolves_to_nobody(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        await prepared.owner()
        _, other = new_owner()

        assert await prepared.owners.get_by_token(other) is None

    backend(work)


# --- Recordings and ownership ----------------------------------------------


def test_a_recording_round_trips_for_its_owner(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)

        assert await prepared.recordings.get(stored.recording_id, owner.owner_id) == stored

    backend(work)


def test_another_owners_recording_is_indistinguishable_from_a_missing_one(backend: Any) -> None:
    """The whole authorisation model in one assertion."""

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), theirs.owner_id)

        assert await prepared.recordings.get(stored.recording_id, mine.owner_id) is None
        assert await prepared.recordings.get(uuid.uuid4().hex, mine.owner_id) is None

    backend(work)


def test_history_is_newest_first_and_only_yours(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()

        first = await prepared.recordings.create(make_recording(), mine.owner_id)
        second = await prepared.recordings.create(make_recording(), mine.owner_id)
        await prepared.recordings.create(make_recording(), theirs.owner_id)

        listed = await prepared.recordings.list_for_owner(mine.owner_id, 10)

        assert [item.recording_id for item in listed] == [
            second.recording_id,
            first.recording_id,
        ] or [item.recording_id for item in listed] == [
            # Two recordings created in the same clock tick tie on created_at
            # and are separated by id, which is random. Either order is correct;
            # what matters is that both are mine and the other owner's is absent.
            first.recording_id,
            second.recording_id,
        ]
        assert len(listed) == 2

    backend(work)


def test_history_respects_its_limit(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for _ in range(5):
            await prepared.recordings.create(make_recording(), owner.owner_id)

        assert len(await prepared.recordings.list_for_owner(owner.owner_id, 3)) == 3

    backend(work)


# --- Paging through a history ----------------------------------------------
#
# Step 10.13's whole subject: a page that is not the whole history has to say
# so, and the rest of it has to be reachable. The double and the SQL are held to
# the same behaviour here, because a double that inferred "more" from
# `count == limit` — or that paged with an offset — would let the browser depend
# on something the database does not do.


def _dated(offset_minutes: int) -> Recording:
    """A recording created a fixed number of minutes after a shared epoch."""
    return make_recording(
        created_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    )


async def _walk(prepared: Backend, owner_id: uuid.UUID, page_size: int) -> list[str]:
    """Every recording id an owner can reach, by following the cursors."""
    seen: list[str] = []
    cursor = None
    for _ in range(50):  # a bound, so a cursor that never advances fails loudly
        page = await prepared.recordings.list_history(owner_id, page_size, cursor)
        seen.extend(entry.recording.recording_id for entry in page.entries)
        if page.next_cursor is None:
            return seen
        cursor = decode_cursor(page.next_cursor)
    raise AssertionError("paging did not terminate")


def test_a_full_history_is_reachable_one_page_at_a_time(backend: Any) -> None:
    """Seven recordings, three at a time: all seven, in order, exactly once."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        created = [
            await prepared.recordings.create(_dated(index), owner.owner_id) for index in range(7)
        ]
        newest_first = [record.recording_id for record in reversed(created)]

        assert await _walk(prepared, owner.owner_id, 3) == newest_first

    backend(work)


def test_the_last_page_says_it_is_the_last(backend: Any) -> None:
    """`next_cursor` is `None` from evidence, not from arithmetic on the count.

    The case that catches a guess: exactly as many recordings as fit on a page.
    An implementation that reports "there is more" whenever `count == limit`
    offers a next page here, and that page is empty.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for index in range(3):
            await prepared.recordings.create(_dated(index), owner.owner_id)

        exact = await prepared.recordings.list_history(owner.owner_id, 3)
        assert len(exact.entries) == 3
        assert exact.next_cursor is None, "a full page is not evidence of another one"

        short = await prepared.recordings.list_history(owner.owner_id, 10)
        assert len(short.entries) == 3
        assert short.next_cursor is None

    backend(work)


def test_recordings_created_in_the_same_instant_still_page(backend: Any) -> None:
    """The tie-break is why the cursor carries an id and not just a timestamp.

    Five recordings sharing one `created_at`: paged on the timestamp alone, the
    second page would either repeat all five or skip all five.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for _ in range(5):
            await prepared.recordings.create(_dated(0), owner.owner_id)

        walked = await _walk(prepared, owner.owner_id, 2)

        assert len(walked) == 5, "a shared timestamp lost or repeated rows"
        assert len(set(walked)) == 5

    backend(work)


def test_a_recording_uploaded_between_pages_does_not_shift_the_window(backend: Any) -> None:
    """Why this is keyset paging and not `OFFSET`.

    With an offset, a new newest recording pushes everything down one, and the
    second page begins by repeating the row the first page ended on.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for index in range(6):
            await prepared.recordings.create(_dated(index), owner.owner_id)

        first = await prepared.recordings.list_history(owner.owner_id, 3)
        assert first.next_cursor is not None

        # Somebody uploads while the reader is looking at page one.
        await prepared.recordings.create(_dated(99), owner.owner_id)

        second = await prepared.recordings.list_history(
            owner.owner_id, 3, decode_cursor(first.next_cursor)
        )

        page_one = {entry.recording.recording_id for entry in first.entries}
        page_two = {entry.recording.recording_id for entry in second.entries}
        assert page_one.isdisjoint(page_two), "the window shifted under the reader"
        assert len(page_two) == 3

    backend(work)


def test_a_cursor_cannot_reach_another_owners_recordings(backend: Any) -> None:
    """A cursor is a position, not a capability.

    Ownership stays in the `WHERE` clause, so somebody else's cursor selects a
    slice of *your* recordings — never theirs.
    """

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        for index in range(4):
            await prepared.recordings.create(_dated(index), mine.owner_id)
        for index in range(4):
            await prepared.recordings.create(_dated(index), theirs.owner_id)

        their_page = await prepared.recordings.list_history(theirs.owner_id, 2)
        assert their_page.next_cursor is not None

        # My history, resumed from their bookmark.
        mine_resumed = await prepared.recordings.list_history(
            mine.owner_id, 10, decode_cursor(their_page.next_cursor)
        )

        theirs_all = {
            entry.recording.recording_id
            for entry in (await prepared.recordings.list_history(theirs.owner_id, 10)).entries
        }
        returned = {entry.recording.recording_id for entry in mine_resumed.entries}
        assert returned.isdisjoint(theirs_all)

    backend(work)


def test_history_reports_analysis_state_without_results(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        untouched = await prepared.recordings.create(make_recording(), owner.owner_id)
        analysed = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.analyses.create(make_analysis(analysed.recording_id))

        entries = {
            entry.recording.recording_id: entry
            for entry in (await prepared.recordings.list_history(owner.owner_id, 10)).entries
        }

        # A recording nobody has analysed reports null, not "pending".
        assert entries[untouched.recording_id].speech_status is None
        assert entries[untouched.recording_id].last_analysed_at is None
        assert entries[analysed.recording_id].speech_status is AnalysisStatus.PENDING
        assert entries[analysed.recording_id].last_analysed_at is not None

    backend(work)


def test_history_never_includes_another_owners_recording(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        await prepared.recordings.create(make_recording(), theirs.owner_id)

        page = await prepared.recordings.list_history(mine.owner_id, 10)
        assert page.entries == []
        assert page.next_cursor is None

    backend(work)


# --- Speech analyses -------------------------------------------------------


def test_only_one_analysis_of_a_recording_may_be_active(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.analyses.create(make_analysis(stored.recording_id))

        with pytest.raises(ActiveAnalysisExistsError):
            await prepared.analyses.create(make_analysis(stored.recording_id))

    backend(work)


@pytest.mark.parametrize("terminal", [AnalysisStatus.COMPLETED, AnalysisStatus.FAILED])
def test_a_terminal_analysis_frees_the_recording_for_another(
    backend: Any, terminal: AnalysisStatus
) -> None:
    """A retry is a new record; the old one stays readable."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        first = await prepared.analyses.create(make_analysis(stored.recording_id))

        # A completed analysis must carry its measurements; a failed one must
        # carry an error code. The model enforces both, so the fixture satisfies
        # whichever terminal state is under test rather than bypassing them.
        done = terminal is AnalysisStatus.COMPLETED
        finished = Analysis.model_validate(
            {
                **first.model_dump(exclude={"provenance"}),
                "status": terminal,
                "error_code": None if done else "INTERNAL_ERROR",
                "transcript": (
                    Transcript(
                        text="one two",
                        provenance=Provenance(provider="stub", is_mock=False),
                    ).model_dump()
                    if done
                    else None
                ),
                "metrics": SpeechMetrics(word_count=2).model_dump() if done else None,
            }
        )
        await prepared.analyses.update(finished, expect_status=AnalysisStatus.PENDING)

        second = await prepared.analyses.create(make_analysis(stored.recording_id))
        assert second.analysis_id != first.analysis_id
        assert await prepared.analyses.get(first.analysis_id) is not None

    backend(work)


def test_an_update_from_the_wrong_status_is_refused(backend: Any) -> None:
    """A worker that resumes after its record was swept must not overwrite it."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.analyses.create(make_analysis(stored.recording_id))

        moved = Analysis.model_validate(
            {**created.model_dump(exclude={"provenance"}), "status": AnalysisStatus.TRANSCRIBING}
        )
        await prepared.analyses.update(moved, expect_status=AnalysisStatus.PENDING)

        with pytest.raises(AnalysisConflictError):
            await prepared.analyses.update(moved, expect_status=AnalysisStatus.PENDING)

    backend(work)


def test_analyses_of_one_recording_come_back_newest_first(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        first = await prepared.analyses.create(
            make_analysis(
                stored.recording_id, status=AnalysisStatus.FAILED, error_code="INTERNAL_ERROR"
            )
        )
        second = await prepared.analyses.create(make_analysis(stored.recording_id))

        listed = await prepared.analyses.list_for_recording(stored.recording_id)
        latest = await prepared.analyses.latest_for_recording(stored.recording_id)

        assert {item.analysis_id for item in listed} == {first.analysis_id, second.analysis_id}
        assert latest is not None
        assert latest.analysis_id == listed[0].analysis_id

    backend(work)


def test_a_missing_analysis_is_none_not_an_error(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        assert await prepared.analyses.get(new_analysis_id()) is None
        assert await prepared.analyses.latest_for_recording(uuid.uuid4().hex) is None
        assert await prepared.analyses.list_for_recording(uuid.uuid4().hex) == []

    backend(work)


# --- Audio analyses --------------------------------------------------------


def test_only_one_audio_analysis_of_a_recording_may_be_active(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(make_audio(stored.recording_id))

        with pytest.raises(ActiveAudioAnalysisExistsError):
            await prepared.audio.create(make_audio(stored.recording_id))

    backend(work)


def test_an_audio_update_from_the_wrong_status_is_refused(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(make_audio(stored.recording_id))

        moved = AudioAnalysis.model_validate(
            {**created.model_dump(), "status": AudioAnalysisStatus.ANALYZING}
        )
        await prepared.audio.update(moved, expect_status=AudioAnalysisStatus.PENDING)

        with pytest.raises(AudioAnalysisConflictError):
            await prepared.audio.update(moved, expect_status=AudioAnalysisStatus.PENDING)

    backend(work)


def test_feedback_can_be_claimed_exactly_once(backend: Any) -> None:
    """The claim that stops a duplicate paid provider call."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(completed_audio(stored.recording_id))

        first = await prepared.audio.claim_feedback(created.audio_analysis_id)
        second = await prepared.audio.claim_feedback(created.audio_analysis_id)

        assert first is not None
        assert first.feedback_status is AudioFeedbackStatus.GENERATING
        assert second is None, "a second claim must not reach a provider"

    backend(work)


def test_feedback_cannot_be_claimed_on_an_unfinished_analysis(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(make_audio(stored.recording_id))

        assert await prepared.audio.claim_feedback(created.audio_analysis_id) is None

    backend(work)


def test_a_failed_feedback_run_can_be_claimed_again(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(
            completed_audio(
                stored.recording_id,
                feedback_status=AudioFeedbackStatus.FAILED,
                feedback_error_code="ANALYSIS_PROVIDER_ERROR",
            )
        )

        claimed = await prepared.audio.claim_feedback(created.audio_analysis_id)

        assert claimed is not None
        assert claimed.feedback_status is AudioFeedbackStatus.GENERATING
        # The retry clears the previous failure rather than carrying it forward.
        assert claimed.feedback_error_code is None

    backend(work)


def test_a_claim_leaves_the_measurements_untouched(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(completed_audio(stored.recording_id))

        claimed = await prepared.audio.claim_feedback(created.audio_analysis_id)

        assert claimed is not None
        assert claimed.metrics == created.metrics
        assert claimed.status is AudioAnalysisStatus.COMPLETED

    backend(work)


def test_a_missing_audio_analysis_is_none_not_an_error(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        assert await prepared.audio.get(new_audio_analysis_id()) is None
        assert await prepared.audio.summary(new_audio_analysis_id()) is None
        assert await prepared.audio.claim_feedback(new_audio_analysis_id()) is None
        assert await prepared.audio.latest_for_recording(uuid.uuid4().hex) is None
        assert await prepared.audio.latest_summary_for_recording(uuid.uuid4().hex) is None
        assert await prepared.audio.list_summaries_for_recording(uuid.uuid4().hex) == []

    backend(work)


# --- Audio analyses, read without their timelines --------------------------
#
# The summary reads are the whole point of ``AudioAnalysisSummary``: PostgreSQL
# evaluates ``document - 'pitch_points'`` and never sends the points, and the
# double drops them in Python. These assert the two agree about what survives —
# everything except the timeline, plus a count of it — because a double that
# quietly kept the points would let a route depend on data the database does
# not return.


def test_a_summary_read_carries_everything_but_the_timeline(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_points(7))
        )

        summary = await prepared.audio.summary(created.audio_analysis_id)

        assert summary is not None
        assert not hasattr(summary, "pitch_points")
        assert summary.pitch_point_count == 7
        # Everything else is the record as stored, field for field.
        assert summary.model_dump() == created.model_dump(exclude={"pitch_points"})

    backend(work)


def test_a_summary_of_a_record_with_no_timeline_counts_zero(backend: Any) -> None:
    """A pending record's document has no ``pitch_points`` key at all."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(make_audio(stored.recording_id))

        summary = await prepared.audio.summary(created.audio_analysis_id)

        assert summary is not None
        assert summary.pitch_point_count == 0
        assert summary.status is AudioAnalysisStatus.PENDING

    backend(work)


def test_the_latest_summary_is_the_latest_analysis(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            make_audio(
                stored.recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code="INSUFFICIENT_PITCH_SIGNAL",
            )
        )
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(3)))

        summary = await prepared.audio.latest_summary_for_recording(stored.recording_id)
        whole = await prepared.audio.latest_for_recording(stored.recording_id)

        assert summary is not None
        assert whole is not None
        # The two reads must select the same row, or a poll and a graph would
        # describe different analyses of the same recording.
        assert summary.audio_analysis_id == whole.audio_analysis_id
        assert summary.pitch_point_count == len(whole.pitch_points) == 3

    backend(work)


# --- Audio analyses, read as a sample of their timeline --------------------
#
# The third form of the read, added in Step 10.14. The pitch graph has returned
# at most ``max_points`` points since Step 7I, and until 10.14 it built every
# stored point in order to throw most of them away: 1 685 kB across the socket
# and ~50 ms of parsing and validation per request, on the event loop, to return
# 995 of 12 931 points. PostgreSQL selects the sample now, with
# ``WITH ORDINALITY``; the double slices the tuple. These assert the two pick the
# *same* points, because "every n-th" is only a well-defined answer if both
# start at the same one.


def test_a_decimated_read_takes_every_nth_stored_point(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(100)))

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=10
        )

        assert sampled is not None
        assert sampled.decimation == 10
        assert len(sampled.points) == 10
        # Indices 0, 10, 20 … — the first point of the recording is always in,
        # so a graph starts where the audio does.
        assert [point.timestamp_seconds for point in sampled.points] == [
            round(index * 0.023, 3) for index in range(0, 100, 10)
        ]

    backend(work)


def test_a_decimated_read_is_the_whole_timeline_sliced(backend: Any) -> None:
    """The sample must be exactly what slicing the stored timeline would give.

    This is the assertion that makes the two implementations interchangeable:
    the double slices a tuple in Python and PostgreSQL filters on ordinality,
    and a stride that started at a different index — or a sort that lost the
    recording's order — would return a plausible-looking graph of the wrong
    frames.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(57)))

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=8
        )
        whole = await prepared.audio.latest_for_recording(stored.recording_id)

        assert sampled is not None
        assert whole is not None
        assert sampled.analysis.audio_analysis_id == whole.audio_analysis_id
        assert sampled.points == whole.pitch_points[:: sampled.decimation]
        # 57 points at a stride of 8 is 8 of them: ceil(57 / 8).
        assert sampled.decimation == 8
        assert len(sampled.points) == 8

    backend(work)


def test_a_decimated_read_says_how_long_the_timeline_really_is(backend: Any) -> None:
    """A sample that reported its own size as the measurement would be a lie.

    The same rule the summary read follows: ``pitch_point_count`` is the stored
    timeline, so "12 931 frames were measured, here are 995 of them" stays
    distinguishable from "995 frames were measured".
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(100)))

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=10
        )

        assert sampled is not None
        assert sampled.analysis.pitch_point_count == 100
        assert len(sampled.points) == 10

    backend(work)


def test_a_timeline_that_fits_is_returned_whole(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(7)))

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=1000
        )
        whole = await prepared.audio.latest_for_recording(stored.recording_id)

        assert sampled is not None
        assert whole is not None
        assert sampled.decimation == 1
        assert sampled.points == whole.pitch_points

    backend(work)


def test_a_decimated_read_of_a_record_with_no_timeline_is_empty_not_missing(
    backend: Any,
) -> None:
    """A pending analysis exists and has measured nothing yet.

    Empty points and a live record, never ``None`` — which is reserved for "no
    analysis of this recording exists", a different answer the route turns into
    a 404.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(make_audio(stored.recording_id))

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=1000
        )

        assert sampled is not None
        assert sampled.points == ()
        assert sampled.decimation == 1
        assert sampled.analysis.pitch_point_count == 0
        assert sampled.analysis.status is AudioAnalysisStatus.PENDING

    backend(work)


def test_a_decimated_read_of_an_unknown_recording_is_none(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        assert (
            await prepared.audio.latest_decimated_for_recording(uuid.uuid4().hex, max_points=1000)
        ) is None

    backend(work)


def test_a_decimated_read_refuses_to_return_fewer_than_one_point(backend: Any) -> None:
    """``max_points`` is a size, and a stride derived from zero is a crash.

    The route constrains it to ``1..50000`` before a service is reached, so this
    is the guard behind that guard: a caller that bypasses HTTP validation is
    refused here rather than dividing by zero inside SQL.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id, pitch_points=_points(4)))

        for refused in (0, -1):
            with pytest.raises(ValueError):
                await prepared.audio.latest_decimated_for_recording(
                    stored.recording_id, max_points=refused
                )

    backend(work)


def test_a_decimated_read_selects_the_newest_analysis(backend: Any) -> None:
    """The same row every other read of this recording selects.

    A re-analysed recording has more than one record, and a graph drawn from the
    older one beside a summary read from the newer would describe two different
    measurements as though they were one.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            make_audio(
                stored.recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code="INSUFFICIENT_PITCH_SIGNAL",
            )
        )
        newest = await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_points(9))
        )

        sampled = await prepared.audio.latest_decimated_for_recording(
            stored.recording_id, max_points=3
        )
        summary = await prepared.audio.latest_summary_for_recording(stored.recording_id)

        assert sampled is not None
        assert summary is not None
        assert sampled.analysis.audio_analysis_id == newest.audio_analysis_id
        assert sampled.analysis.audio_analysis_id == summary.audio_analysis_id

    backend(work)


# --- Audio analyses, read as the fields an aggregation folds ---------------
#
# The fourth form of the read, added in Step 10.15. ``/notes`` and ``/key`` fold
# every stored frame, so neither may be handed a sample the way the graph is —
# but between them they read two of a frame's six fields, and building all six
# cost ~50 ms of event-loop time per request. PostgreSQL projects the two into
# an ``int[]`` and a ``float8[]``; the double takes the same two out of the
# points it holds. These assert the two produce the *same* arrays, because a
# breakdown folded from deviations that belong to other notes looks entirely
# reasonable and is wrong.


def _varied_points(count: int) -> list[dict[str, Any]]:
    """A timeline where no two frames share a note *and* a deviation.

    ``_points`` is 12 931 copies of A4 at 0.0 cents, which is exactly the
    fixture that cannot catch the mistakes worth catching here: a read that
    returned the arrays in different orders, paired one field with the other's
    order, or dropped a frame would all agree with it.
    """
    return [
        {
            "timestamp_seconds": round(index * 0.023, 3),
            "frequency_hz": 220.0 + index,
            "midi_note": 57 + index % 24,
            "note_name": "A3",
            "cents": round(-49.0 + (index * 3.5) % 98.0, 3),
            "confidence": 0.9,
        }
        for index in range(count)
    ]


def test_the_fields_read_is_the_stored_timeline_field_by_field(backend: Any) -> None:
    """What a fold sees must be what was written, in the order it was written.

    The assertion that makes the two implementations interchangeable, and the
    one that makes the read safe at all: ``cents[i]`` is the deviation of
    ``midi_notes[i]``, so two aggregates that disagreed about order would
    attribute real deviations to the wrong notes.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_varied_points(40))
        )

        read = await prepared.audio.latest_fields_for_recording(stored.recording_id)
        whole = await prepared.audio.latest_for_recording(stored.recording_id)

        assert read is not None
        assert whole is not None
        assert read.analysis.audio_analysis_id == whole.audio_analysis_id
        assert read.fields == PitchFields.of_points(whole.pitch_points)
        assert read.fields.midi_notes == tuple(point.midi_note for point in whole.pitch_points)
        assert read.fields.cents == tuple(point.cents for point in whole.pitch_points)

    backend(work)


def test_the_fields_read_carries_the_whole_timeline_and_says_so(backend: Any) -> None:
    """A fold of part of a recording is a fold of a different recording.

    Unlike the graph's read this one is not allowed to be short, and the check
    is not a comment: ``pitch_point_count`` is written beside the document, so
    the length of the arrays and the stored count are independent statements
    about the same timeline and ``TimelineFields`` refuses a pair that disagree.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_varied_points(57))
        )

        read = await prepared.audio.latest_fields_for_recording(stored.recording_id)

        assert read is not None
        assert len(read.fields) == 57
        assert read.analysis.pitch_point_count == 57

    backend(work)


def test_a_record_with_no_timeline_reads_back_as_itself_with_no_fields(backend: Any) -> None:
    """An analysis still running has no ``pitch_points`` key at all.

    In PostgreSQL the two arrays come from a lateral over
    ``jsonb_array_elements``, which yields no rows for a document that has no
    timeline yet. It holds an aggregate, so it answers that with one row of
    nulls rather than none — without which a pending record would vanish from
    the result and the endpoint would report a recording it can see as unknown.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(make_audio(stored.recording_id))

        read = await prepared.audio.latest_fields_for_recording(stored.recording_id)

        assert read is not None
        assert read.analysis.audio_analysis_id == created.audio_analysis_id
        assert read.analysis.status is AudioAnalysisStatus.PENDING
        assert len(read.fields) == 0

    backend(work)


def test_a_fields_read_of_an_unknown_recording_is_none(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        assert await prepared.audio.latest_fields_for_recording(uuid.uuid4().hex) is None

    backend(work)


def test_a_fields_read_selects_the_newest_analysis(backend: Any) -> None:
    """The same row every other read of this recording selects.

    A breakdown folded from the older record beside a summary read from the
    newer would describe two different measurements as though they were one.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            make_audio(
                stored.recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code="INSUFFICIENT_PITCH_SIGNAL",
            )
        )
        newest = await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_varied_points(9))
        )

        read = await prepared.audio.latest_fields_for_recording(stored.recording_id)
        summary = await prepared.audio.latest_summary_for_recording(stored.recording_id)

        assert read is not None
        assert summary is not None
        assert read.analysis.audio_analysis_id == newest.audio_analysis_id
        assert read.analysis.audio_analysis_id == summary.audio_analysis_id

    backend(work)


def test_summaries_are_listed_newest_first(backend: Any) -> None:
    """``_existing_for`` walks this list to decide which analysis to hand back.

    An order that differed between the two implementations would make a repeated
    ``POST`` resume a different attempt depending on the store, so the ordering
    is asserted here rather than left to whichever the double happens to produce.
    """

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        for day in (2, 3, 1):
            await prepared.audio.create(
                make_audio(
                    stored.recording_id,
                    created_at=datetime(2026, 8, day, tzinfo=UTC),
                    status=AudioAnalysisStatus.FAILED,
                    error_code="AUDIO_ANALYSIS_FAILED",
                )
            )

        summaries = await prepared.audio.list_summaries_for_recording(stored.recording_id)

        assert [summary.created_at.day for summary in summaries] == [3, 2, 1]

    backend(work)


def test_claiming_feedback_answers_without_the_timeline(backend: Any) -> None:
    """The claim is a conditional UPDATE; it never needed to return the points."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        created = await prepared.audio.create(
            completed_audio(stored.recording_id, pitch_points=_points(5))
        )

        claimed = await prepared.audio.claim_feedback(created.audio_analysis_id)

        assert claimed is not None
        assert not hasattr(claimed, "pitch_points")
        assert claimed.pitch_point_count == 5
        # And the stored timeline is still there for the run that follows.
        whole = await prepared.audio.get(created.audio_analysis_id)
        assert whole is not None
        assert len(whole.pitch_points) == 5

    backend(work)


# --- Comparison sources ----------------------------------------------------


def test_comparison_sources_return_both_recordings_and_their_analyses(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        left = await prepared.recordings.create(make_recording(), owner.owner_id)
        right = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(make_audio(left.recording_id))

        sources = await prepared.recordings.comparison_sources(
            owner.owner_id, [left.recording_id, right.recording_id]
        )

        assert set(sources) == {left.recording_id, right.recording_id}
        assert sources[left.recording_id].audio_analysis is not None
        # A recording nobody has measured comes back with no analysis, not with
        # a fabricated one.
        assert sources[right.recording_id].audio_analysis is None

    backend(work)


def test_comparison_sources_omit_another_owners_recording(backend: Any) -> None:
    """The security property of the comparison query, asserted against real SQL."""

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        ours = await prepared.recordings.create(make_recording(), mine.owner_id)
        stolen = await prepared.recordings.create(make_recording(), theirs.owner_id)

        sources = await prepared.recordings.comparison_sources(
            mine.owner_id, [ours.recording_id, stolen.recording_id]
        )

        assert set(sources) == {ours.recording_id}
        assert stolen.recording_id not in sources

    backend(work)


def test_comparison_sources_omit_an_unknown_recording(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        known = await prepared.recordings.create(make_recording(), owner.owner_id)

        sources = await prepared.recordings.comparison_sources(
            owner.owner_id, [known.recording_id, uuid.uuid4().hex]
        )

        assert set(sources) == {known.recording_id}

    backend(work)


def test_comparison_sources_take_the_most_recent_analysis(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)

        first = await prepared.audio.create(
            make_audio(
                stored.recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code="INSUFFICIENT_PITCH_SIGNAL",
            )
        )
        second = await prepared.audio.create(make_audio(stored.recording_id))

        sources = await prepared.recordings.comparison_sources(
            owner.owner_id, [stored.recording_id]
        )
        loaded = sources[stored.recording_id].audio_analysis

        assert loaded is not None
        assert loaded.audio_analysis_id in {first.audio_analysis_id, second.audio_analysis_id}
        latest = await prepared.audio.latest_for_recording(stored.recording_id)
        assert latest is not None
        assert loaded.audio_analysis_id == latest.audio_analysis_id

    backend(work)


def test_comparison_sources_of_nothing_is_empty(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()

        assert await prepared.recordings.comparison_sources(owner.owner_id, []) == {}

    backend(work)


def test_comparison_sources_preserve_the_stored_pitch_timeline(backend: Any) -> None:
    """The note breakdown is derived from these points, so they have to survive."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            completed_audio(
                stored.recording_id,
                pitch_points=[
                    {
                        "timestamp_seconds": 0.1,
                        "frequency_hz": 440.0,
                        "midi_note": 69,
                        "note_name": "A4",
                        "cents": 3.0,
                        "confidence": 0.95,
                    }
                ],
            )
        )

        sources = await prepared.recordings.comparison_sources(
            owner.owner_id, [stored.recording_id]
        )
        analysis = sources[stored.recording_id].audio_analysis

        assert analysis is not None
        assert len(analysis.pitch_points) == 1
        assert analysis.pitch_points[0].note_name == "A4"

    backend(work)


# --- Progress rows ---------------------------------------------------------


def test_progress_rows_come_back_oldest_first(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for day in (3, 1, 2):
            await prepared.recordings.create(
                make_recording(created_at=datetime(2026, 8, day, tzinfo=UTC)), owner.owner_id
            )

        rows = await prepared.recordings.progress_rows(owner.owner_id, 10)

        assert [row.recorded_at.day for row in rows] == [1, 2, 3]

    backend(work)


def test_progress_rows_tie_break_on_recording_id(backend: Any) -> None:
    """Two recordings saved in the same instant must have one stable order."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        same = datetime(2026, 8, 5, tzinfo=UTC)
        for recording_id in ("b" * 32, "a" * 32):
            await prepared.recordings.create(
                make_recording(recording_id=recording_id, created_at=same), owner.owner_id
            )

        rows = await prepared.recordings.progress_rows(owner.owner_id, 10)

        assert [row.recording_id for row in rows] == ["a" * 32, "b" * 32]

    backend(work)


def test_progress_rows_window_the_most_recent_recordings(backend: Any) -> None:
    """The window is taken from the newest end, then returned oldest first."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        for day in range(1, 6):
            await prepared.recordings.create(
                make_recording(created_at=datetime(2026, 8, day, tzinfo=UTC)), owner.owner_id
            )

        rows = await prepared.recordings.progress_rows(owner.owner_id, 2)

        assert [row.recorded_at.day for row in rows] == [4, 5]

    backend(work)


def test_progress_rows_read_the_metrics_out_of_a_completed_analysis(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id))

        row = (await prepared.recordings.progress_rows(owner.owner_id, 10))[0]

        assert row.analysed is True
        assert row.in_tune_ratio is not None
        assert row.voiced_frames is not None
        assert row.hop_length_samples is not None
        assert row.sample_rate_hz is not None
        assert row.lowest_note == "G2"

    backend(work)


def test_progress_rows_include_a_recording_with_no_analysis(backend: Any) -> None:
    """It still belongs in its own history; the gap has to be visible."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)

        rows = await prepared.recordings.progress_rows(owner.owner_id, 10)

        assert [row.recording_id for row in rows] == [stored.recording_id]
        assert rows[0].analysed is False
        assert rows[0].in_tune_ratio is None

    backend(work)


@pytest.mark.parametrize("status", [AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING])
def test_progress_rows_ignore_an_unfinished_analysis(backend: Any, status: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(make_audio(stored.recording_id, status=status))

        rows = await prepared.recordings.progress_rows(owner.owner_id, 10)

        assert rows[0].analysed is False

    backend(work)


def test_progress_rows_ignore_a_failed_analysis(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(
            make_audio(
                stored.recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code="INSUFFICIENT_PITCH_SIGNAL",
            )
        )

        rows = await prepared.recordings.progress_rows(owner.owner_id, 10)

        assert rows[0].analysed is False
        assert rows[0].in_tune_ratio is None

    backend(work)


def test_progress_rows_never_include_another_owners_recording(backend: Any) -> None:
    """The security property of the progress query, asserted against real SQL."""

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        await prepared.recordings.create(make_recording(), theirs.owner_id)
        ours = await prepared.recordings.create(make_recording(), mine.owner_id)

        rows = await prepared.recordings.progress_rows(mine.owner_id, 10)

        assert [row.recording_id for row in rows] == [ours.recording_id]

    backend(work)


def test_progress_rows_are_empty_for_an_owner_with_nothing(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()

        assert await prepared.recordings.progress_rows(owner.owner_id, 10) == []

    backend(work)


# --- Owner data and deletion -----------------------------------------------


def test_an_owner_summary_counts_only_their_own_data(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()

        first = await prepared.recordings.create(make_recording(), mine.owner_id)
        await prepared.recordings.create(make_recording(), mine.owner_id)
        await prepared.recordings.create(make_recording(), theirs.owner_id)
        await prepared.audio.create(completed_audio(first.recording_id))

        summary = await prepared.owners.data_summary(mine.owner_id)

        assert summary.recordings == 2
        assert summary.analysed_recordings == 1
        assert summary.ai_feedback == 0

    backend(work)


def test_an_empty_owner_summarises_to_zeroes(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()

        summary = await prepared.owners.data_summary(owner.owner_id)

        assert (summary.recordings, summary.analysed_recordings, summary.ai_feedback) == (0, 0, 0)

    backend(work)


def test_an_unfinished_analysis_does_not_count_as_analysed(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(make_audio(stored.recording_id))

        summary = await prepared.owners.data_summary(owner.owner_id)

        assert summary.recordings == 1
        assert summary.analysed_recordings == 0

    backend(work)


def test_recording_ids_are_only_the_owners_and_are_ordered(backend: Any) -> None:
    """The deletion service names files from this list, so it must be complete."""

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        ours = [
            (await prepared.recordings.create(make_recording(), mine.owner_id)).recording_id
            for _ in range(3)
        ]
        stolen = await prepared.recordings.create(make_recording(), theirs.owner_id)

        ids = await prepared.owners.recording_ids(mine.owner_id)

        assert ids == sorted(ours)
        assert stolen.recording_id not in ids

    backend(work)


def test_deleting_an_owner_removes_their_recordings_and_analyses(backend: Any) -> None:
    """The cascade, asserted against real foreign keys rather than assumed."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        stored = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.audio.create(completed_audio(stored.recording_id))
        await prepared.analyses.create(make_analysis(stored.recording_id))

        assert await prepared.owners.delete_owner(owner.owner_id) is True

        assert await prepared.recordings.get(stored.recording_id, owner.owner_id) is None
        assert await prepared.audio.latest_for_recording(stored.recording_id) is None
        assert await prepared.analyses.latest_for_recording(stored.recording_id) is None
        assert await prepared.owners.get(owner.owner_id) is None

    backend(work)


def test_deleting_an_owner_leaves_every_other_owner_alone(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        await prepared.recordings.create(make_recording(), mine.owner_id)
        survivor = await prepared.recordings.create(make_recording(), theirs.owner_id)

        await prepared.owners.delete_owner(mine.owner_id)

        assert await prepared.recordings.get(survivor.recording_id, theirs.owner_id) is not None
        assert await prepared.owners.get(theirs.owner_id) is not None

    backend(work)


def test_deleting_an_owner_twice_is_harmless(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()

        assert await prepared.owners.delete_owner(owner.owner_id) is True
        assert await prepared.owners.delete_owner(owner.owner_id) is False

    backend(work)


def test_a_deleted_owners_key_no_longer_resolves(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner, token = new_owner()
        await prepared.owners.create(owner, token)

        await prepared.owners.delete_owner(owner.owner_id)

        assert await prepared.owners.get_by_token(token) is None

    backend(work)


# --- Credentials -----------------------------------------------------------


async def _attach(prepared: Backend, owner: Owner, label: str = "Second key") -> tuple[Any, str]:
    credential, key = new_credential(owner.owner_id, label)
    return await prepared.credentials.create(credential, key), key


def test_creating_an_owner_creates_its_first_credential(backend: Any) -> None:
    """An owner with no way in would be an identity nobody could reach."""

    async def work(prepared: Backend) -> None:
        owner, token = new_owner()
        await prepared.owners.create(owner, token)

        listed = await prepared.credentials.list_for_owner(owner.owner_id)

        assert len(listed) == 1
        assert await prepared.credentials.owner_for_key(token) == owner.owner_id

    backend(work)


def test_a_second_credential_resolves_to_the_same_owner(backend: Any) -> None:
    """The whole point of Step 10.2: another key, not another identity."""

    async def work(prepared: Backend) -> None:
        owner, first = new_owner()
        await prepared.owners.create(owner, first)

        _, second = await _attach(prepared, owner)

        assert await prepared.credentials.owner_for_key(second) == owner.owner_id
        assert await prepared.credentials.owner_for_key(first) == owner.owner_id
        assert await prepared.owners.get_by_token(second) == owner

    backend(work)


def test_credentials_are_listed_oldest_first_and_carry_no_hash(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _attach(prepared, owner, "Phone")
        await _attach(prepared, owner, "Laptop")

        listed = await prepared.credentials.list_for_owner(owner.owner_id)

        assert [c.label for c in listed] == ["Original key", "Phone", "Laptop"]
        assert all(not hasattr(c, "credential_hash") for c in listed)

    backend(work)


def test_a_credential_list_never_includes_another_owners(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        await _attach(prepared, theirs, "Theirs")

        listed = await prepared.credentials.list_for_owner(mine.owner_id)

        assert all(c.owner_id == mine.owner_id for c in listed)
        assert "Theirs" not in [c.label for c in listed]

    backend(work)


def test_an_unknown_key_belongs_to_nobody(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        await prepared.owner()
        _, stranger = new_owner()

        assert await prepared.credentials.owner_for_key(stranger) is None
        assert await prepared.credentials.credential_for_key(stranger) is None

    backend(work)


def test_the_same_key_cannot_be_registered_twice(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        credential, key = new_credential(owner.owner_id, "Phone")
        await prepared.credentials.create(credential, key)

        duplicate, _ = new_credential(owner.owner_id, "Phone again")
        with pytest.raises(CredentialExistsError):
            await prepared.credentials.create(duplicate, key)

    backend(work)


def test_revoking_a_credential_stops_it_resolving(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner, original = new_owner()
        await prepared.owners.create(owner, original)
        credential, key = await _attach(prepared, owner)

        assert await prepared.credentials.revoke(credential.credential_id, owner.owner_id) is True

        assert await prepared.credentials.owner_for_key(key) is None
        # The other one is untouched, and so is the owner.
        assert await prepared.credentials.owner_for_key(original) == owner.owner_id
        assert await prepared.owners.get(owner.owner_id) == owner

    backend(work)


def test_the_last_credential_cannot_be_revoked(backend: Any) -> None:
    """Removing it would strand the identity: owned recordings nobody can reach."""

    async def work(prepared: Backend) -> None:
        owner, token = new_owner()
        await prepared.owners.create(owner, token)
        only = (await prepared.credentials.list_for_owner(owner.owner_id))[0]

        with pytest.raises(LastCredentialError):
            await prepared.credentials.revoke(only.credential_id, owner.owner_id)

        assert await prepared.credentials.owner_for_key(token) == owner.owner_id

    backend(work)


def test_revoking_another_owners_credential_finds_nothing(backend: Any) -> None:
    """Scoped in SQL, and reported as absent rather than refused.

    "Not found" and "not yours" have to be the same answer, or the endpoint
    becomes a way to discover that an id is real.
    """

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs = await prepared.owner()
        credential, key = await _attach(prepared, theirs)

        assert await prepared.credentials.revoke(credential.credential_id, mine.owner_id) is False

        assert await prepared.credentials.owner_for_key(key) == theirs.owner_id

    backend(work)


def test_revoking_the_last_of_another_owner_is_not_refused(backend: Any) -> None:
    """The refusal must not leak that somebody else is down to one key."""

    async def work(prepared: Backend) -> None:
        mine = await prepared.owner()
        theirs, _ = new_owner()
        await prepared.owners.create(theirs, new_owner()[1])
        only = (await prepared.credentials.list_for_owner(theirs.owner_id))[0]

        assert await prepared.credentials.revoke(only.credential_id, mine.owner_id) is False

    backend(work)


def test_revoking_an_unknown_credential_finds_nothing(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _attach(prepared, owner)

        assert await prepared.credentials.revoke(uuid.uuid4(), owner.owner_id) is False

    backend(work)


def test_a_key_names_the_credential_it_is(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        credential, key = await _attach(prepared, owner)

        assert await prepared.credentials.credential_for_key(key) == credential.credential_id

    backend(work)


def test_deleting_an_owner_takes_every_credential_with_it(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner, original = new_owner()
        await prepared.owners.create(owner, original)
        _, second = await _attach(prepared, owner)

        await prepared.owners.delete_owner(owner.owner_id)

        assert await prepared.credentials.owner_for_key(original) is None
        assert await prepared.credentials.owner_for_key(second) is None
        assert await prepared.credentials.list_for_owner(owner.owner_id) == []

    backend(work)


def test_attaching_a_credential_changes_no_ownership(backend: Any) -> None:
    """The invariant the whole step turns on, asserted on the rows themselves."""

    async def work(prepared: Backend) -> None:
        owner, original = new_owner()
        await prepared.owners.create(owner, original)
        recording = make_recording()
        await prepared.recordings.create(recording, owner.owner_id)
        before = await prepared.owners.data_summary(owner.owner_id)

        _, second = await _attach(prepared, owner)

        resolved = await prepared.owners.get_by_token(second)
        assert resolved is not None
        assert resolved.owner_id == owner.owner_id
        assert await prepared.owners.data_summary(owner.owner_id) == before
        assert await prepared.recordings.get(recording.recording_id, owner.owner_id) is not None

    backend(work)


# --- Identity retention -----------------------------------------------------


async def _age(prepared: Backend, owner: Owner, when: datetime) -> None:
    """Move an identity's activity signal into the past, whichever backend."""
    if hasattr(prepared.owners, "set_last_seen"):
        prepared.owners.set_last_seen(owner.owner_id, when)
        return
    async with prepared.owners._db.transaction() as connection:  # noqa: SLF001
        await connection.execute(
            "UPDATE owners SET last_seen_at = %s WHERE id = %s", (when, owner.owner_id)
        )


def _long_ago() -> datetime:
    return datetime.now(UTC) - timedelta(days=400)


def test_a_newly_created_owner_is_not_a_cleanup_candidate(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        await prepared.owner()

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == []

    backend(work)


def test_an_unused_empty_owner_becomes_a_candidate(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _age(prepared, owner, _long_ago())

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == [owner.owner_id]

    backend(work)


def test_an_owner_with_a_recording_is_never_a_candidate(backend: Any) -> None:
    """However old. Deleting it would destroy somebody's history."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await prepared.recordings.create(make_recording(), owner.owner_id)
        await _age(prepared, owner, _long_ago())

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == []

    backend(work)


def test_touching_an_owner_removes_it_from_the_candidates(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _age(prepared, owner, _long_ago())
        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == [owner.owner_id]

        await prepared.owners.touch(owner.owner_id, timedelta(0))

        assert await prepared.owners.expired_owner_ids(cutoff, 100) == []

    backend(work)


def test_touching_is_throttled(backend: Any) -> None:
    """A busy identity must not become a write on every read."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        recent = datetime.now(UTC) - timedelta(minutes=1)
        await _age(prepared, owner, recent)

        for _ in range(5):
            await prepared.owners.touch(owner.owner_id, timedelta(hours=1))

        # Still not a candidate against a cutoff just after the stored value:
        # if any of those touches had written, it would have moved forward.
        cutoff = datetime.now(UTC) - timedelta(seconds=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == [owner.owner_id]

    backend(work)


def test_candidates_come_back_oldest_first(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        newer = await prepared.owner()
        older = await prepared.owner()
        await _age(prepared, newer, datetime.now(UTC) - timedelta(days=100))
        await _age(prepared, older, datetime.now(UTC) - timedelta(days=900))

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.expired_owner_ids(cutoff, 100) == [
            older.owner_id,
            newer.owner_id,
        ]

    backend(work)


def test_the_candidate_limit_is_honoured(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        for _ in range(4):
            owner = await prepared.owner()
            await _age(prepared, owner, _long_ago())

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert len(await prepared.owners.expired_owner_ids(cutoff, 2)) == 2

    backend(work)


def test_claiming_deletes_the_owner_and_its_credentials(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner, token = new_owner()
        await prepared.owners.create(owner, token)
        await _age(prepared, owner, _long_ago())

        cutoff = datetime.now(UTC) - timedelta(days=30)
        assert await prepared.owners.claim_expired_owner(owner.owner_id, cutoff) is True

        assert await prepared.owners.get(owner.owner_id) is None
        assert await prepared.owners.get_by_token(token) is None

    backend(work)


def test_claiming_refuses_an_owner_that_came_back(backend: Any) -> None:
    """Race A: eligible when queried, active by the time it is claimed."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _age(prepared, owner, _long_ago())
        cutoff = datetime.now(UTC) - timedelta(days=30)

        await prepared.owners.touch(owner.owner_id, timedelta(0))

        assert await prepared.owners.claim_expired_owner(owner.owner_id, cutoff) is False
        assert await prepared.owners.get(owner.owner_id) is not None

    backend(work)


def test_claiming_refuses_an_owner_that_uploaded(backend: Any) -> None:
    """Race C: a recording arrives between the query and the claim."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _age(prepared, owner, _long_ago())
        cutoff = datetime.now(UTC) - timedelta(days=30)

        stored = await prepared.recordings.create(make_recording(), owner.owner_id)

        assert await prepared.owners.claim_expired_owner(owner.owner_id, cutoff) is False
        assert await prepared.recordings.get(stored.recording_id, owner.owner_id) is not None

    backend(work)


def test_claiming_twice_deletes_once(backend: Any) -> None:
    """Race B, and idempotency: the second attempt is a no-op, not an error."""

    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        await _age(prepared, owner, _long_ago())
        cutoff = datetime.now(UTC) - timedelta(days=30)

        assert await prepared.owners.claim_expired_owner(owner.owner_id, cutoff) is True
        assert await prepared.owners.claim_expired_owner(owner.owner_id, cutoff) is False

    backend(work)


def test_claiming_never_touches_another_owner(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        doomed = await prepared.owner()
        keeper, keeper_token = new_owner()
        await prepared.owners.create(keeper, keeper_token)
        await _age(prepared, doomed, _long_ago())
        await _age(prepared, keeper, _long_ago())
        stored = await prepared.recordings.create(make_recording(), keeper.owner_id)

        cutoff = datetime.now(UTC) - timedelta(days=30)
        await prepared.owners.claim_expired_owner(doomed.owner_id, cutoff)

        assert await prepared.owners.get(keeper.owner_id) is not None
        assert await prepared.owners.get_by_token(keeper_token) == keeper
        assert await prepared.recordings.get(stored.recording_id, keeper.owner_id) is not None

    backend(work)
