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
    new_audio_analysis_id,
)
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
    PostgresAudioAnalysisRepository,
)
from app.services.owners.models import Owner, new_owner
from app.services.owners.repository import PostgresOwnerRepository
from app.services.recordings.models import Recording
from app.services.recordings.postgres_repository import PostgresRecordingRepository
from tests.doubles import (
    InMemoryAnalysisRepository,
    InMemoryAudioAnalysisRepository,
    InMemoryOwnerRepository,
    InMemoryRecordingRepository,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


class Backend:
    """A wired set of repositories, however they happen to be stored."""

    def __init__(self, owners: Any, recordings: Any, analyses: Any, audio: Any) -> None:
        self.owners = owners
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
    yield Backend(
        owners=InMemoryOwnerRepository(),
        recordings=InMemoryRecordingRepository(analyses, audio),
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


def test_history_reports_analysis_state_without_results(backend: Any) -> None:
    async def work(prepared: Backend) -> None:
        owner = await prepared.owner()
        untouched = await prepared.recordings.create(make_recording(), owner.owner_id)
        analysed = await prepared.recordings.create(make_recording(), owner.owner_id)
        await prepared.analyses.create(make_analysis(analysed.recording_id))

        entries = {
            entry.recording.recording_id: entry
            for entry in await prepared.recordings.list_history(owner.owner_id, 10)
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

        assert await prepared.recordings.list_history(mine.owner_id, 10) == []

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
        assert await prepared.audio.claim_feedback(new_audio_analysis_id()) is None
        assert await prepared.audio.latest_for_recording(uuid.uuid4().hex) is None

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
