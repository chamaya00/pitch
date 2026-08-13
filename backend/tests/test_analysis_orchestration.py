"""Tests for the analysis workflow.

Real recordings on a real temporary filesystem, driven through the real
repository and the deterministic mock providers from Step 7B. The only things
substituted are providers that fail on purpose — and they fail by raising the
same `ProviderError` subclasses the Deepgram and Claude adapters raise, so the
failure paths here are the ones production would take.

Two properties get the most attention, because they are the ones that would
quietly cost a user something: a failure must never touch the recording, and a
transcription that succeeded must never be thrown away because a language model
was unavailable.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import anyio
import pytest

from app.core.errors import ApiError, ErrorCode
from app.services.ai.errors import (
    EmptyTranscriptError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.mock import MockFeedbackProvider, MockSpeechToTextProvider
from app.services.analysis.models import (
    Analysis,
    AnalysisStatus,
    Feedback,
    Provenance,
    SpeechMetrics,
    Transcript,
    new_analysis_id,
)
from app.services.audio.metadata import AudioFormat
from app.services.audio.storage import RecordingStorage
from app.services.orchestration.analysis import (
    ACTIVE_STATUSES,
    AnalysisService,
    StartedAnalysis,
)
from app.services.recordings.models import Recording
from tests.doubles import (
    InMemoryAnalysisRepository,
    InMemoryRecordingRepository,
)
from tests.fixtures import write_wav

RECORDING_SECONDS = 2.0

#: One fixed owner for this module. Ownership isolation has its own tests in
#: the API and contract suites; here it is a constant so these assertions stay
#: about the workflow.
OWNER_ID: Final = uuid.UUID("00000000-0000-4000-8000-0000000000a1")


# --- Doubles ---------------------------------------------------------------


class FailingSpeechToText:
    """A transcription provider that fails the way a real adapter would."""

    def __init__(self, error: ProviderError) -> None:
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return "failing"

    async def transcribe(self, audio_path: Path) -> Transcript:
        self.calls += 1
        raise self._error


class UnusedSpeechToText:
    """Fails the test if it is called at all."""

    @property
    def name(self) -> str:
        return "unused"

    async def transcribe(self, audio_path: Path) -> Transcript:
        raise AssertionError("the provider must not be called")


class FailingFeedback:
    def __init__(self, error: ProviderError) -> None:
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return "failing"

    async def generate(self, *, transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
        self.calls += 1
        raise self._error


class RecordingFeedback:
    """Captures exactly what the feedback provider was handed."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._inner = MockFeedbackProvider()

    @property
    def name(self) -> str:
        return "recording"

    async def generate(self, **kwargs: Any) -> Feedback:
        self.calls.append(kwargs)
        return await self._inner.generate(**kwargs)


class ExplodingFeedback:
    """Fails with something that is *not* a provider error."""

    @property
    def name(self) -> str:
        return "exploding"

    async def generate(self, *, transcript: Transcript, metrics: SpeechMetrics) -> Feedback:
        raise RuntimeError("password=hunter2 leaked from an internal call")


# --- Fixtures --------------------------------------------------------------


@dataclass
class Fixture:
    root: Path
    storage: RecordingStorage
    recordings: InMemoryRecordingRepository
    analyses: InMemoryAnalysisRepository
    recording_id: str

    def service(
        self,
        *,
        speech_to_text: Any = None,
        feedback: Any = None,
        stale_after_seconds: float = 900,
    ) -> AnalysisService:
        return AnalysisService(
            recordings=self.recordings,
            storage=self.storage,
            analyses=self.analyses,
            speech_to_text=speech_to_text or MockSpeechToTextProvider(),
            feedback=feedback or MockFeedbackProvider(),
            stale_after_seconds=stale_after_seconds,
        )

    def audio_bytes(self) -> bytes:
        return self.storage.path_for(self.recording_id).read_bytes()

    def stored(self, analysis_id: str) -> Analysis | None:
        return run(lambda: self.analyses.get(analysis_id))

    def stored_for_recording(self, recording_id: str | None = None) -> list[Analysis]:
        target = recording_id or self.recording_id
        return run(lambda: self.analyses.list_for_recording(target))

    def seed_analysis(self, **overrides: object) -> Analysis:
        payload: dict[str, object] = {
            "analysis_id": new_analysis_id(),
            "recording_id": self.recording_id,
        }
        payload.update(overrides)
        return run(lambda: self.analyses.create(Analysis.model_validate(payload)))


@pytest.fixture
def env(tmp_path: Path) -> Fixture:
    storage = RecordingStorage(tmp_path)
    source = write_wav(tmp_path / "source.wav", seconds=RECORDING_SECONDS)
    stored = storage.save(source, extension=".wav")

    analyses = InMemoryAnalysisRepository()
    recordings = InMemoryRecordingRepository(analyses)
    anyio.run(
        lambda: recordings.create(
            Recording(
                recording_id=stored.recording_id,
                original_filename="take.wav",
                audio_format=AudioFormat.WAV,
                duration_seconds=RECORDING_SECONDS,
                sample_rate=8000,
                channels=1,
                size_bytes=stored.size_bytes,
            ),
            OWNER_ID,
        )
    )
    return Fixture(
        root=tmp_path,
        storage=storage,
        recordings=recordings,
        analyses=analyses,
        recording_id=stored.recording_id,
    )


def run(coroutine_factory: Any) -> Any:
    return anyio.run(coroutine_factory)


def analyze(service: AnalysisService, recording_id: str) -> Analysis:
    return run(lambda: service.analyze(recording_id, OWNER_ID))


# --- The happy path --------------------------------------------------------


def test_a_recording_becomes_a_completed_analysis(env: Fixture):
    analysis = analyze(env.service(), env.recording_id)

    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.recording_id == env.recording_id
    assert analysis.error_code is None
    assert analysis.transcript is not None
    assert analysis.metrics is not None
    assert analysis.feedback is not None
    assert analysis.is_terminal is True


def test_the_metrics_are_computed_from_the_transcript(env: Fixture):
    """The deterministic layer runs on what the provider actually returned."""
    analysis = analyze(env.service(), env.recording_id)

    assert analysis.metrics is not None
    assert analysis.transcript is not None
    assert analysis.metrics.word_count == len(analysis.transcript.text.split())
    assert analysis.metrics.words_per_minute is not None
    assert analysis.metrics.pause_count is not None


def test_the_timestamps_describe_the_run(env: Fixture):
    before = datetime.now(UTC)
    analysis = analyze(env.service(), env.recording_id)

    assert analysis.started_at is not None
    assert analysis.completed_at is not None
    assert before <= analysis.created_at <= analysis.started_at <= analysis.completed_at


def test_the_analysis_is_persisted_and_readable_afterwards(env: Fixture):
    analysis = analyze(env.service(), env.recording_id)
    stored = env.stored(analysis.analysis_id)

    assert stored == analysis
    assert [item.analysis_id for item in env.stored_for_recording()] == [analysis.analysis_id]


def test_provenance_is_persisted(env: Fixture):
    analysis = analyze(env.service(), env.recording_id)
    stored = env.stored(analysis.analysis_id)

    assert stored is not None
    assert stored.provenance.transcription is not None
    assert stored.provenance.transcription.provider == "mock"
    assert stored.provenance.feedback is not None
    assert stored.provenance.feedback.provider == "mock"
    assert stored.provenance.is_mock is True
    assert '"is_mock":true' in stored.model_dump_json().replace(" ", "")


def test_the_record_is_written_at_every_stage(env: Fixture):
    """A process killed mid-run leaves a record saying where it got to."""
    seen: list[AnalysisStatus] = []
    repository = env.analyses
    original_update = repository.update

    async def observe(analysis: Analysis, **kwargs: Any) -> Analysis:
        seen.append(analysis.status)
        return await original_update(analysis, **kwargs)

    repository.update = observe  # type: ignore[method-assign]
    analyze(env.service(), env.recording_id)

    assert seen == [
        AnalysisStatus.TRANSCRIBING,
        AnalysisStatus.ANALYZING,
        AnalysisStatus.COMPLETED,
    ]


def test_a_successful_analysis_leaves_exactly_one_record(env: Fixture):
    """Every transition overwrites one row rather than appending another.

    The atomic-write behaviour that used to be asserted here belongs to the
    filesystem store and is tested in ``test_analysis_repository``; what matters
    to the workflow is that four persisted transitions are still one analysis.
    """
    analysis = analyze(env.service(), env.recording_id)

    assert [item.analysis_id for item in env.stored_for_recording()] == [analysis.analysis_id]


# --- What the feedback provider receives -----------------------------------


def test_the_feedback_provider_receives_only_a_transcript_and_metrics(env: Fixture):
    probe = RecordingFeedback()
    analyze(env.service(feedback=probe), env.recording_id)

    assert len(probe.calls) == 1
    assert set(probe.calls[0]) == {"transcript", "metrics"}
    assert isinstance(probe.calls[0]["transcript"], Transcript)
    assert isinstance(probe.calls[0]["metrics"], SpeechMetrics)


def test_no_raw_audio_reaches_the_feedback_provider(env: Fixture):
    probe = RecordingFeedback()
    analyze(env.service(feedback=probe), env.recording_id)

    audio = env.audio_bytes()
    assert len(audio) > 0
    for value in probe.calls[0].values():
        serialised = value.model_dump_json()
        assert not isinstance(value, bytes | bytearray)
        assert audio[:64].hex() not in serialised
        assert "RIFF" not in serialised


def test_no_filesystem_path_reaches_the_feedback_provider(env: Fixture):
    probe = RecordingFeedback()
    analyze(env.service(feedback=probe), env.recording_id)

    handed = " ".join(value.model_dump_json() for value in probe.calls[0].values())
    assert str(env.root) not in handed
    assert env.recording_id not in handed
    assert "take.wav" not in handed
    assert ".wav" not in handed
    for value in probe.calls[0].values():
        assert not isinstance(value, Path)


# --- Feedback failure ------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ProviderNotConfiguredError("claude"),
        ProviderUnavailableError("claude"),
        ProviderTimeoutError("claude"),
        ProviderRateLimitedError("claude"),
        ProviderResponseError("claude"),
    ],
)
def test_a_feedback_failure_still_completes_the_analysis(env: Fixture, error: ProviderError):
    """Numbers without prose beats losing a transcription that succeeded."""
    feedback = FailingFeedback(error)
    analysis = analyze(env.service(feedback=feedback), env.recording_id)

    assert feedback.calls == 1
    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.error_code is None
    assert analysis.transcript is not None
    assert analysis.metrics is not None
    assert analysis.feedback is None
    assert analysis.provenance.feedback is None
    assert analysis.provenance.transcription is not None


def test_a_feedback_failure_is_persisted_as_completed(env: Fixture):
    analysis = analyze(
        env.service(feedback=FailingFeedback(ProviderUnavailableError("claude"))),
        env.recording_id,
    )
    stored = env.stored(analysis.analysis_id)

    assert stored is not None
    assert stored.status is AnalysisStatus.COMPLETED
    assert stored.feedback is None
    assert stored.metrics is not None


def test_no_feedback_is_fabricated_when_the_provider_fails(env: Fixture):
    analysis = analyze(
        env.service(feedback=FailingFeedback(ProviderResponseError("claude"))),
        env.recording_id,
    )
    assert analysis.feedback is None


def test_an_unexpected_feedback_error_fails_the_analysis_without_leaking(env: Fixture):
    """Not a provider failure, so not degraded — but still no internals shown."""
    analysis = analyze(env.service(feedback=ExplodingFeedback()), env.recording_id)

    assert analysis.status is AnalysisStatus.FAILED
    assert analysis.error_code is ErrorCode.INTERNAL_ERROR
    assert "hunter2" not in analysis.model_dump_json()
    # The transcription that did succeed is still on the record.
    assert analysis.transcript is not None
    assert analysis.metrics is not None


# --- Transcription failure -------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderNotConfiguredError("deepgram"), ErrorCode.ANALYSIS_NOT_CONFIGURED),
        (ProviderUnavailableError("deepgram"), ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE),
        (ProviderTimeoutError("deepgram"), ErrorCode.ANALYSIS_PROVIDER_TIMEOUT),
        (ProviderRateLimitedError("deepgram"), ErrorCode.ANALYSIS_RATE_LIMITED),
        (ProviderResponseError("deepgram"), ErrorCode.ANALYSIS_PROVIDER_ERROR),
        (EmptyTranscriptError("deepgram"), ErrorCode.TRANSCRIPT_EMPTY),
    ],
)
def test_a_transcription_failure_is_persisted_with_its_error_code(
    env: Fixture, error: ProviderError, code: ErrorCode
):
    provider = FailingSpeechToText(error)
    analysis = analyze(env.service(speech_to_text=provider), env.recording_id)

    assert provider.calls == 1
    assert analysis.status is AnalysisStatus.FAILED
    assert analysis.error_code is code
    assert analysis.completed_at is not None
    assert analysis.transcript is None
    assert analysis.metrics is None
    assert analysis.feedback is None

    stored = env.stored(analysis.analysis_id)
    assert stored == analysis


def test_a_failure_keeps_the_progress_the_run_had_already_made(env: Fixture):
    """The failed record says when it started, not that it never did."""
    analysis = analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderTimeoutError("deepgram"))),
        env.recording_id,
    )
    assert analysis.started_at is not None
    assert analysis.status is AnalysisStatus.FAILED


def test_a_failure_never_raises_out_of_the_run(env: Fixture):
    """A background task must not be able to take the process down."""
    analysis = analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderUnavailableError("deepgram"))),
        env.recording_id,
    )
    assert analysis.status is AnalysisStatus.FAILED


def test_no_feedback_is_requested_when_transcription_failed(env: Fixture):
    feedback = RecordingFeedback()
    analyze(
        env.service(
            speech_to_text=FailingSpeechToText(ProviderUnavailableError("deepgram")),
            feedback=feedback,
        ),
        env.recording_id,
    )
    assert feedback.calls == []


def test_no_temporary_file_remains_after_a_failed_analysis(env: Fixture):
    analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderResponseError("deepgram"))),
        env.recording_id,
    )


def test_the_recording_is_untouched_by_a_failed_analysis(env: Fixture):
    """A provider's bad day must never cost the user their upload."""
    before = env.audio_bytes()
    stored_recording = run(lambda: env.recordings.get(env.recording_id, OWNER_ID))

    analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderResponseError("deepgram"))),
        env.recording_id,
    )

    assert env.storage.exists(env.recording_id) is True
    assert env.audio_bytes() == before
    assert run(lambda: env.recordings.get(env.recording_id, OWNER_ID)) == stored_recording


def test_the_recording_is_untouched_by_a_successful_analysis(env: Fixture):
    before = env.audio_bytes()
    analyze(env.service(), env.recording_id)

    assert env.audio_bytes() == before
    assert env.storage.exists(env.recording_id) is True


# --- Recording not found ---------------------------------------------------


def test_an_unknown_recording_is_refused(env: Fixture):
    with pytest.raises(ApiError) as caught:
        analyze(env.service(speech_to_text=UnusedSpeechToText()), new_analysis_id())

    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert caught.value.status_code == 404


def test_an_unknown_recording_creates_no_analysis_record(env: Fixture):
    """A record pointing at a recording that does not exist is a lie on disk."""
    with pytest.raises(ApiError):
        analyze(env.service(), new_analysis_id())

    assert env.stored_for_recording() == []


def test_a_recording_whose_bytes_are_gone_is_refused(env: Fixture):
    """Metadata without audio is not analysable."""
    env.storage.delete(env.recording_id)

    with pytest.raises(ApiError) as caught:
        analyze(env.service(speech_to_text=UnusedSpeechToText()), env.recording_id)
    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND


# --- Idempotency -----------------------------------------------------------


def test_analysing_the_same_recording_twice_reuses_the_completed_analysis(env: Fixture):
    first = analyze(env.service(), env.recording_id)
    second = analyze(env.service(speech_to_text=UnusedSpeechToText()), env.recording_id)

    assert second.analysis_id == first.analysis_id
    assert second.status is AnalysisStatus.COMPLETED
    assert len(env.stored_for_recording()) == 1


@pytest.mark.parametrize("status", sorted(ACTIVE_STATUSES, key=lambda item: item.value))
def test_an_analysis_already_in_flight_is_returned_rather_than_duplicated(
    env: Fixture, status: AnalysisStatus
):
    active = env.seed_analysis(status=status, started_at=datetime.now(UTC))
    service = env.service(speech_to_text=UnusedSpeechToText())

    returned = run(lambda: service.start(env.recording_id, OWNER_ID))

    assert returned.created is False
    assert returned.analysis.analysis_id == active.analysis_id
    assert returned.analysis.status is status
    assert len(env.stored_for_recording()) == 1


def test_an_in_flight_analysis_starts_no_second_provider_call(env: Fixture):
    env.seed_analysis(status=AnalysisStatus.TRANSCRIBING, started_at=datetime.now(UTC))
    service = env.service(speech_to_text=UnusedSpeechToText(), feedback=RecordingFeedback())

    analysis = analyze(service, env.recording_id)
    assert analysis.status is AnalysisStatus.TRANSCRIBING


def test_analyses_of_different_recordings_do_not_collide(env: Fixture, tmp_path: Path):
    other_source = write_wav(tmp_path / "other.wav", seconds=1.0)
    other = env.storage.save(other_source, extension=".wav")
    run(
        lambda: env.recordings.create(
            Recording(
                recording_id=other.recording_id,
                original_filename="other.wav",
                audio_format=AudioFormat.WAV,
                duration_seconds=1.0,
                sample_rate=8000,
                channels=1,
                size_bytes=other.size_bytes,
            ),
            OWNER_ID,
        )
    )

    first = analyze(env.service(), env.recording_id)
    second = analyze(env.service(), other.recording_id)

    assert first.analysis_id != second.analysis_id
    assert first.recording_id != second.recording_id


def test_concurrent_starts_converge_on_one_analysis(env: Fixture):
    """Eight simultaneous starts produce one record, not eight.

    Note what this does and does not show. It runs against the in-memory
    double, so it demonstrates the *service* logic converging — read, insert,
    and hand back the winner's record when the insert is refused. What makes
    the same property hold across processes is the partial unique index, and
    that is proved against a real server in ``test_database`` and
    ``test_repository_contract``, not here.
    """
    service = env.service()

    async def start_many() -> list[StartedAnalysis]:
        return list(
            await asyncio.gather(*(service.start(env.recording_id, OWNER_ID) for _ in range(8)))
        )

    started = run(start_many)

    assert len({item.analysis.analysis_id for item in started}) == 1
    assert [item.created for item in started].count(True) == 1
    assert len(env.stored_for_recording()) == 1


# --- Retry -----------------------------------------------------------------


def test_a_failed_analysis_is_retried_as_a_new_analysis(env: Fixture):
    failed = analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderUnavailableError("deepgram"))),
        env.recording_id,
    )
    retried = analyze(env.service(), env.recording_id)

    assert failed.status is AnalysisStatus.FAILED
    assert retried.status is AnalysisStatus.COMPLETED
    assert retried.analysis_id != failed.analysis_id


def test_a_retry_leaves_the_failed_analysis_inspectable(env: Fixture):
    """The failed record is the evidence of what went wrong; it is not erased."""
    failed = analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderTimeoutError("deepgram"))),
        env.recording_id,
    )
    analyze(env.service(), env.recording_id)

    preserved = env.stored(failed.analysis_id)
    assert preserved == failed
    assert preserved is not None
    assert preserved.error_code is ErrorCode.ANALYSIS_PROVIDER_TIMEOUT
    assert len(env.stored_for_recording(env.recording_id)) == 2


def test_a_completed_analysis_is_not_re_run_after_an_earlier_failure(env: Fixture):
    analyze(
        env.service(speech_to_text=FailingSpeechToText(ProviderTimeoutError("deepgram"))),
        env.recording_id,
    )
    completed = analyze(env.service(), env.recording_id)
    again = analyze(env.service(speech_to_text=UnusedSpeechToText()), env.recording_id)

    assert again.analysis_id == completed.analysis_id


# --- Abandoned runs --------------------------------------------------------


def test_an_abandoned_analysis_does_not_block_the_recording_forever(env: Fixture):
    """A process that died mid-run must not make a recording unanalysable."""
    abandoned = env.seed_analysis(
        status=AnalysisStatus.TRANSCRIBING,
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )

    fresh = analyze(env.service(stale_after_seconds=60), env.recording_id)

    assert fresh.analysis_id != abandoned.analysis_id
    assert fresh.status is AnalysisStatus.COMPLETED


def test_an_abandoned_analysis_is_recorded_as_failed_rather_than_left_running(env: Fixture):
    abandoned = env.seed_analysis(
        status=AnalysisStatus.ANALYZING,
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )

    analyze(env.service(stale_after_seconds=60), env.recording_id)

    swept = env.stored(abandoned.analysis_id)
    assert swept is not None
    assert swept.status is AnalysisStatus.FAILED
    assert swept.error_code is ErrorCode.INTERNAL_ERROR
    assert swept.is_terminal is True


def test_a_slow_but_healthy_analysis_is_not_swept(env: Fixture):
    active = env.seed_analysis(
        status=AnalysisStatus.TRANSCRIBING,
        started_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    service = env.service(speech_to_text=UnusedSpeechToText(), stale_after_seconds=900)

    started = run(lambda: service.start(env.recording_id, OWNER_ID))
    assert started.analysis.analysis_id == active.analysis_id


# --- Running a specific analysis -------------------------------------------


def test_run_executes_a_pending_analysis(env: Fixture):
    """The split Step 7E needs: create fast, execute in the background."""
    service = env.service()
    pending = run(lambda: service.start(env.recording_id, OWNER_ID)).analysis
    assert pending.status is AnalysisStatus.PENDING

    completed = run(lambda: service.run(pending.analysis_id))

    assert completed.analysis_id == pending.analysis_id
    assert completed.status is AnalysisStatus.COMPLETED


def test_run_on_a_finished_analysis_changes_nothing(env: Fixture):
    service = env.service()
    completed = analyze(service, env.recording_id)

    again = run(lambda: service.run(completed.analysis_id))
    assert again == completed


def test_run_on_an_unknown_analysis_is_refused(env: Fixture):
    service = env.service()
    with pytest.raises(ApiError) as caught:
        run(lambda: service.run(new_analysis_id()))
    assert caught.value.code is ErrorCode.ANALYSIS_NOT_FOUND


def test_cancellation_leaves_a_record_and_propagates(env: Fixture):
    """A cancelled task must not look like it is still running."""

    class Cancelling:
        @property
        def name(self) -> str:
            return "cancelling"

        async def transcribe(self, audio_path: Path) -> Transcript:
            raise asyncio.CancelledError

    service = env.service(speech_to_text=Cancelling())
    pending = run(lambda: service.start(env.recording_id, OWNER_ID)).analysis

    with pytest.raises(asyncio.CancelledError):
        run(lambda: service.run(pending.analysis_id))

    stored = env.stored(pending.analysis_id)
    assert stored is not None
    assert stored.status is AnalysisStatus.FAILED
    assert stored.is_terminal is True


def test_a_store_that_breaks_while_recording_a_failure_reports_the_real_cause(env: Fixture):
    """The provider timed out; a write error on the way out must not replace it."""
    service = env.service(speech_to_text=FailingSpeechToText(ProviderTimeoutError("deepgram")))
    pending = run(lambda: service.start(env.recording_id, OWNER_ID)).analysis

    original_update = env.analyses.update

    async def fail_only_on_the_failure_write(analysis: Analysis, **kwargs: Any) -> Analysis:
        if analysis.status is AnalysisStatus.FAILED:
            raise RuntimeError("disk full")
        return await original_update(analysis, **kwargs)

    env.analyses.update = fail_only_on_the_failure_write  # type: ignore[method-assign]

    result = run(lambda: service.run(pending.analysis_id))

    assert result.status is AnalysisStatus.FAILED
    assert result.error_code is ErrorCode.ANALYSIS_PROVIDER_TIMEOUT


def test_a_store_that_breaks_mid_run_fails_the_analysis(env: Fixture):
    """When persistence itself is what broke, that is the honest error code."""
    service = env.service()
    pending = run(lambda: service.start(env.recording_id, OWNER_ID)).analysis

    async def explode(analysis: Analysis, **kwargs: Any) -> Analysis:
        raise RuntimeError("disk full")

    env.analyses.update = explode  # type: ignore[method-assign]

    result = run(lambda: service.run(pending.analysis_id))

    assert result.status is AnalysisStatus.FAILED
    assert result.error_code is ErrorCode.INTERNAL_ERROR


# --- Invariants ------------------------------------------------------------


def test_transitions_are_revalidated_rather_than_copied(env: Fixture):
    """Every persisted state satisfies the model's own invariants."""
    analysis = analyze(env.service(), env.recording_id)
    stored = env.stored(analysis.analysis_id)
    assert stored is not None

    # Re-validating the stored document must not raise.
    assert Analysis.model_validate_json(stored.model_dump_json()) == stored


def test_a_completed_analysis_always_carries_its_measurements(env: Fixture):
    for feedback in (MockFeedbackProvider(), FailingFeedback(ProviderResponseError("claude"))):
        env.analyses = InMemoryAnalysisRepository()

        analysis = analyze(env.service(feedback=feedback), env.recording_id)
        assert analysis.status is AnalysisStatus.COMPLETED
        assert analysis.transcript is not None
        assert analysis.metrics is not None


def test_the_recording_duration_backs_up_a_provider_without_timings(env: Fixture):
    """Metrics fall back to the duration measured at upload, not to a guess."""

    class TextOnly:
        @property
        def name(self) -> str:
            return "text-only"

        async def transcribe(self, audio_path: Path) -> Transcript:
            return Transcript(
                text="one two three four",
                provenance=Provenance(provider="text-only", is_mock=False),
            )

    analysis = analyze(env.service(speech_to_text=TextOnly()), env.recording_id)

    assert analysis.metrics is not None
    assert analysis.metrics.speaking_duration_seconds == pytest.approx(RECORDING_SECONDS)
    assert analysis.metrics.words_per_minute == pytest.approx(120.0)
    assert analysis.metrics.pause_count is None
