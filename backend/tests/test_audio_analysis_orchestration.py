"""The audio-analysis workflow, driven with a stub analyzer.

The measurement itself is tested in ``test_audio_analyzer.py`` against real
files. What is tested here is everything *around* it: idempotency, the failure
path, the staleness sweep, and the guarantee that a background task cannot take
the process down. A stub analyzer makes those assertions fast and lets a
failure be provoked deliberately rather than by finding a file that breaks numpy.
"""

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import anyio
import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.services.ai.mock import MockFeedbackProvider
from app.services.ai.protocols import AudioFeedbackProvider
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.analyzer import (
    HOP_SECONDS,
    AudioAnalysisResult,
    AudioAnalyzer,
    SignalAudioAnalyzer,
)
from app.services.audio_analysis.errors import (
    AudioAnalysisError,
    AudioUnsupportedError,
    InsufficientPitchSignalError,
)
from app.services.audio_analysis.key import analyse_key
from app.services.audio_analysis.models import (
    AnalysisSettings,
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioFeedbackStatus,
    AudioMetrics,
    KeyMode,
    KeyUnmeasuredReason,
    Loudness,
    PitchPoint,
    PitchStability,
    VocalRange,
    new_audio_analysis_id,
)
from app.services.audio_analysis.pitch import midi_to_frequency, midi_to_note_name
from app.services.orchestration.audio_analysis import AudioAnalysisService
from app.services.recordings.models import Recording, utc_now
from tests.doubles import (
    InMemoryAudioAnalysisRepository,
    InMemoryRecordingRepository,
)
from tests.fixtures import harmonic_samples, write_signal_wav

SAMPLE_RATE = 22050

#: One fixed owner for this module; ownership isolation is tested elsewhere.
OWNER_ID: Final = uuid.UUID("00000000-0000-4000-8000-0000000000a2")


def _result() -> AudioAnalysisResult:
    return AudioAnalysisResult(
        metrics=AudioMetrics(
            settings=AnalysisSettings(
                sample_rate_hz=SAMPLE_RATE,
                frame_length_samples=2048,
                hop_length_samples=512,
                min_frequency_hz=65.0,
                max_frequency_hz=1100.0,
                clarity_threshold=0.8,
                silence_rms=0.005,
            ),
            duration_seconds=2.0,
            pitch=VocalRange(
                lowest_frequency_hz=440.0,
                highest_frequency_hz=440.0,
                lowest_note="A4",
                highest_note="A4",
                semitone_span=0,
            ),
            stability=PitchStability(voiced_ratio=1.0, total_frames=80, voiced_frames=80),
            loudness=Loudness(rms=0.2, peak=0.5, clipped_sample_ratio=0.0),
        ),
        pitch_points=(
            PitchPoint(
                timestamp_seconds=0.1,
                frequency_hz=440.0,
                midi_note=69,
                note_name="A4",
                cents=0.0,
                confidence=0.95,
            ),
        ),
    )


class StubAnalyzer:
    """Records what it was asked to measure, and answers however a test wants."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[Path] = []
        self._error = error

    def analyze(self, path: Path) -> AudioAnalysisResult:
        self.calls.append(path)
        if self._error is not None:
            raise self._error
        return _result()


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def analyses() -> InMemoryAudioAnalysisRepository:
    return InMemoryAudioAnalysisRepository()


@pytest.fixture
def recordings(analyses: InMemoryAudioAnalysisRepository) -> InMemoryRecordingRepository:
    return InMemoryRecordingRepository(audio_analyses=analyses)


@pytest.fixture
def storage(storage_root: Path) -> RecordingStorage:
    return RecordingStorage(storage_root)


@pytest.fixture
def recording_id(
    tmp_path: Path, storage: RecordingStorage, recordings: InMemoryRecordingRepository
) -> str:
    """A genuinely stored recording, written through the real storage layer."""
    source = write_signal_wav(
        tmp_path / "take.wav",
        harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    stored = storage.save(source, extension=".wav")
    anyio.run(
        lambda: recordings.create(
            Recording(
                recording_id=stored.recording_id,
                original_filename="take.wav",
                audio_format="wav",
                duration_seconds=2.0,
                sample_rate=SAMPLE_RATE,
                channels=1,
                size_bytes=stored.size_bytes,
            ),
            OWNER_ID,
        )
    )
    return stored.recording_id


def run(coroutine_factory: Any) -> Any:
    """Drive a coroutine from a synchronous test, as the sibling suite does.

    The project has no async test plugin; ``anyio.run`` is enough and keeps the
    dependency list where it is.
    """
    return anyio.run(coroutine_factory)


def build(
    *,
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    analyzer: AudioAnalyzer,
    feedback: AudioFeedbackProvider | None = None,
    stale_after_seconds: float = 900,
) -> AudioAnalysisService:
    return AudioAnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=analyzer,
        # The measurement tests below never reach it; feedback generation has
        # its own suite.
        feedback=feedback or MockFeedbackProvider(),
        stale_after_seconds=stale_after_seconds,
    )


# --- The happy path --------------------------------------------------------


def test_an_analysis_completes_and_is_persisted(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    result = run(lambda: service.analyze(recording_id, OWNER_ID))

    assert result.status is AudioAnalysisStatus.COMPLETED
    assert result.metrics is not None
    assert result.metrics.pitch is not None
    assert result.pitch_points
    assert result.completed_at is not None

    stored = run(lambda: analyses.get(result.audio_analysis_id))
    assert stored is not None
    assert stored.status is AudioAnalysisStatus.COMPLETED


def test_the_analyzer_receives_the_stored_path_not_a_filename(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The id is the only thing that reaches the filesystem."""
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    run(lambda: service.analyze(recording_id, OWNER_ID))

    assert len(analyzer.calls) == 1
    assert analyzer.calls[0] == storage.path_for(recording_id)
    assert "take" not in analyzer.calls[0].name


def test_start_records_progress_before_the_slow_work(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    started = run(lambda: service.start(recording_id, OWNER_ID))
    assert started.created is True
    assert started.analysis.status is AudioAnalysisStatus.PENDING
    assert run(lambda: analyses.get(started.analysis.audio_analysis_id)) is not None


# --- Idempotency -----------------------------------------------------------


def test_a_second_start_returns_the_first_analysis(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    first = run(lambda: service.start(recording_id, OWNER_ID))
    second = run(lambda: service.start(recording_id, OWNER_ID))

    assert second.created is False
    assert second.analysis.audio_analysis_id == first.analysis.audio_analysis_id


def test_analysing_twice_measures_the_file_once(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    first = run(lambda: service.analyze(recording_id, OWNER_ID))
    second = run(lambda: service.analyze(recording_id, OWNER_ID))

    assert len(analyzer.calls) == 1
    assert second.audio_analysis_id == first.audio_analysis_id


def test_running_the_same_analysis_twice_measures_once(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """Even a caller that schedules the work twice cannot make it happen twice."""
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    started = run(lambda: service.start(recording_id, OWNER_ID))
    run(lambda: service.run(started.analysis.audio_analysis_id))
    run(lambda: service.run(started.analysis.audio_analysis_id))

    assert len(analyzer.calls) == 1


def test_a_failed_analysis_is_retried_as_a_new_record(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The failed record stays on disk and inspectable; the retry is separate."""
    failing = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=AudioUnsupportedError(reason="RuntimeError")),
    )
    failed = run(lambda: failing.analyze(recording_id, OWNER_ID))
    assert failed.status is AudioAnalysisStatus.FAILED

    working = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    retried = run(lambda: working.analyze(recording_id, OWNER_ID))

    assert retried.audio_analysis_id != failed.audio_analysis_id
    assert retried.status is AudioAnalysisStatus.COMPLETED
    assert run(lambda: analyses.get(failed.audio_analysis_id)) is not None


def test_concurrent_starts_create_one_analysis(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    async def start_many() -> list[Any]:
        return list(
            await asyncio.gather(*(service.start(recording_id, OWNER_ID) for _ in range(6)))
        )

    results = run(start_many)

    assert sum(1 for started in results if started.created) == 1
    assert len({started.analysis.audio_analysis_id for started in results}) == 1


def test_an_abandoned_analysis_is_swept_so_the_recording_is_not_stuck(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """A process killed mid-run must not make a recording permanently unanalysable."""
    stalled = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(),
        stale_after_seconds=60,
    )
    started = run(lambda: stalled.start(recording_id, OWNER_ID))
    run(
        lambda: analyses.update(
            started.analysis.model_copy(update={"status": AudioAnalysisStatus.ANALYZING}),
            expect_status=started.analysis.status,
        )
    )

    impatient = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(),
        stale_after_seconds=0,
    )
    retried = run(lambda: impatient.start(recording_id, OWNER_ID))

    assert retried.created is True
    assert retried.analysis.audio_analysis_id != started.analysis.audio_analysis_id
    swept = run(lambda: analyses.get(started.analysis.audio_analysis_id))
    assert swept is not None
    assert swept.status is AudioAnalysisStatus.FAILED


# --- Failures --------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AudioUnsupportedError(reason="RuntimeError"), ErrorCode.AUDIO_UNSUPPORTED),
        (InsufficientPitchSignalError(), ErrorCode.INSUFFICIENT_PITCH_SIGNAL),
        (AudioAnalysisError(), ErrorCode.AUDIO_ANALYSIS_FAILED),
    ],
)
def test_a_measurement_failure_is_recorded_not_raised(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
    error: AudioAnalysisError,
    code: ErrorCode,
) -> None:
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=error),
    )

    result = run(lambda: service.analyze(recording_id, OWNER_ID))

    assert result.status is AudioAnalysisStatus.FAILED
    assert result.error_code is code
    assert result.completed_at is not None


def test_an_unexpected_exception_leaves_an_honest_record(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """A background task must never take the process down."""
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=ZeroDivisionError("numpy said no")),
    )

    result = run(lambda: service.analyze(recording_id, OWNER_ID))

    assert result.status is AudioAnalysisStatus.FAILED
    assert result.error_code is ErrorCode.INTERNAL_ERROR


def test_cancellation_is_recorded_and_re_raised(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=asyncio.CancelledError()),
    )

    started = run(lambda: service.start(recording_id, OWNER_ID))
    with pytest.raises(asyncio.CancelledError):
        run(lambda: service.run(started.analysis.audio_analysis_id))

    stored = run(lambda: analyses.get(started.analysis.audio_analysis_id))
    assert stored is not None
    assert stored.status is AudioAnalysisStatus.FAILED


def test_an_unknown_recording_is_refused_without_creating_a_record(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    with pytest.raises(ApiError) as caught:
        run(lambda: service.start("0" * 32, OWNER_ID))
    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert run(lambda: analyses.list_for_recording("0" * 32)) == []


def test_running_an_unknown_analysis_is_refused(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    with pytest.raises(ApiError) as caught:
        run(lambda: service.run("0" * 32))
    assert caught.value.code is ErrorCode.AUDIO_ANALYSIS_NOT_FOUND


def test_current_refuses_an_unknown_recording(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    with pytest.raises(ApiError) as caught:
        run(lambda: service.current("0" * 32, OWNER_ID))
    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND


def test_current_is_none_before_anything_has_run(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    assert run(lambda: service.current(recording_id, OWNER_ID)) is None


# --- With the real analyzer ------------------------------------------------


def test_the_real_analyzer_measures_a_real_stored_recording(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """One pass with nothing stubbed: storage, decoder, detector, repository."""
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=SignalAudioAnalyzer(),
    )

    result = run(lambda: service.analyze(recording_id, OWNER_ID))

    assert result.status is AudioAnalysisStatus.COMPLETED
    assert result.metrics is not None
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A4"
    assert len(result.pitch_points) > 10


# --- Derived key estimation (Phase 8, Slice 2) -----------------------------
#
# The estimator itself is exhaustively tested in ``test_audio_key.py`` against
# constructed timelines. What is tested here is the seam: that the service
# reaches the right record, through the ownership boundary the rest of this
# class uses, hands over the *stored* timeline, and persists nothing.

#: A second identity, for the ownership assertion only. Every other test in this
#: module deliberately uses one owner.
OTHER_OWNER_ID: Final = uuid.UUID("00000000-0000-4000-8000-0000000000b7")

#: Middle C, and the hop the stub analysis records. Kept here rather than
#: imported from the estimator so a change to its constants cannot silently
#: change what these fixtures mean.
_C4: Final = 60
_HOP_SECONDS: Final = 512 / SAMPLE_RATE

#: A melody's shape: tonic and dominant carry it. The same weighting
#: ``test_audio_key.py`` uses, so the two suites agree about what C major is.
_C_MAJOR_MELODY: Final = {0: 40, 2: 20, 4: 30, 5: 20, 7: 40, 9: 20, 11: 10}


def _timeline(weights: dict[int, int]) -> tuple[PitchPoint, ...]:
    """A pitch timeline spending ``frames`` on each pitch class above middle C."""
    points: list[PitchPoint] = []
    timestamp = 0.0
    for pitch_class, frames in sorted(weights.items()):
        midi = _C4 + pitch_class
        frequency = midi_to_frequency(midi)
        name = midi_to_note_name(midi)
        assert frequency is not None and name is not None
        for _ in range(frames):
            points.append(
                PitchPoint(
                    timestamp_seconds=timestamp,
                    frequency_hz=frequency,
                    midi_note=midi,
                    note_name=name,
                    cents=0.0,
                    confidence=0.95,
                )
            )
            timestamp += _HOP_SECONDS
    return tuple(points)


class CountingAnalysisRepository(InMemoryAudioAnalysisRepository):
    """The established double, counting every write it is asked to perform.

    Subclassed rather than replaced so this cannot drift from the behaviour the
    rest of the suite is written against.

    ``loads`` counts the reads that fetch a whole analysis document. In
    PostgreSQL that document carries the pitch timeline as JSONB — tens of
    thousands of points at the accepted maximum — and it is the expensive part
    of any request that derives something from it, far outweighing the
    arithmetic. Counting them is how a second one gets noticed.

    ``summary_loads`` counts the reads that leave the timeline in the database.
    Those are the cheap ones — 1.7 ms against 87 ms, measured — and they are
    counted separately rather than ignored so a test can say "this path read the
    record, and read it the inexpensive way" instead of only "it read nothing".
    """

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0
        self.loads = 0
        self.summary_loads = 0

    async def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        self.writes += 1
        return await super().create(analysis)

    async def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        self.loads += 1
        return await super().get(audio_analysis_id)

    async def summary(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        self.summary_loads += 1
        return await super().summary(audio_analysis_id)

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        self.loads += 1
        return await super().latest_for_recording(recording_id)

    async def latest_summary_for_recording(self, recording_id: str) -> AudioAnalysisSummary | None:
        self.summary_loads += 1
        return await super().latest_summary_for_recording(recording_id)

    async def list_summaries_for_recording(self, recording_id: str) -> list[AudioAnalysisSummary]:
        self.summary_loads += 1
        return await super().list_summaries_for_recording(recording_id)

    async def update(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        self.writes += 1
        return await super().update(analysis, expect_status=expect_status)

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        self.writes += 1
        return await super().claim_feedback(audio_analysis_id)


def _store_completed(
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
    points: tuple[PitchPoint, ...],
) -> AudioAnalysis:
    """Put a finished analysis carrying ``points`` in the repository."""
    result = _result()
    analysis = AudioAnalysis(
        audio_analysis_id=new_audio_analysis_id(),
        recording_id=recording_id,
        status=AudioAnalysisStatus.COMPLETED,
        metrics=result.metrics.model_dump(),
        pitch_points=[point.model_dump() for point in points],
        completed_at=utc_now(),
    )
    run(lambda: analyses.create(analysis))
    return analysis


def test_a_completed_analysis_yields_the_key_its_notes_fit(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    analysis = run(lambda: service.key(recording_id, OWNER_ID))

    assert analysis is not None
    assert analysis.key is not None
    assert analysis.key.tonic == "C"
    assert analysis.key.mode is KeyMode.MAJOR
    assert analysis.distinct_pitch_classes == 7


def test_the_service_reports_exactly_what_the_estimator_decided(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """No second opinion, no rounding, no re-derivation of the confidence."""
    points = _timeline(_C_MAJOR_MELODY)
    _store_completed(analyses, recording_id, points)
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    through_service = run(lambda: service.key(recording_id, OWNER_ID))
    directly = analyse_key(points, frame_seconds=_HOP_SECONDS)

    assert through_service is not None
    assert through_service.model_dump() == directly.model_dump()


def test_a_recording_with_no_completed_analysis_has_no_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    assert run(lambda: service.key(recording_id, OWNER_ID)) is None


def test_an_analysis_still_running_has_no_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    run(lambda: service.start(recording_id, OWNER_ID))

    assert run(lambda: service.key(recording_id, OWNER_ID)) is None


def test_a_failed_analysis_has_no_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=InsufficientPitchSignalError(reason="voiced=0")),
    )
    run(lambda: service.analyze(recording_id, OWNER_ID))

    assert run(lambda: service.key(recording_id, OWNER_ID)) is None


def test_a_failed_analysis_carrying_measurements_still_has_no_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """Only a *completed* analysis is ever derived from.

    Mutation found this: dropping the status guard changes nothing for records
    the pipeline produces today, because a failed one has no metrics to work
    from. But the model does not forbid metrics on a failed record, and a key
    read off an analysis that ended in failure would be a measurement presented
    as if the measurement had succeeded. The guard is what stops that, and this
    is the record shape that proves it.
    """
    run(
        lambda: analyses.create(
            AudioAnalysis(
                audio_analysis_id=new_audio_analysis_id(),
                recording_id=recording_id,
                status=AudioAnalysisStatus.FAILED,
                error_code=ErrorCode.AUDIO_ANALYSIS_FAILED,
                metrics=_result().metrics.model_dump(),
                pitch_points=[point.model_dump() for point in _timeline(_C_MAJOR_MELODY)],
                completed_at=utc_now(),
            )
        )
    )
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    assert run(lambda: service.key(recording_id, OWNER_ID)) is None


def test_a_completed_analysis_without_a_key_is_not_the_same_as_no_analysis(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The distinction this method exists to keep.

    A hum is a *measured* recording whose notes do not establish a key. It must
    come back as a verdict carrying its reason and its evidence — never as
    ``None``, which means there was nothing to look at.
    """
    _store_completed(analyses, recording_id, _timeline({0: 140}))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    analysis = run(lambda: service.key(recording_id, OWNER_ID))

    assert analysis is not None
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES
    assert len(analysis.pitch_classes) == 12


def test_another_owners_recording_has_no_key_and_says_only_that(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The same answer a recording that does not exist would give.

    Ownership is not re-implemented here: ``key`` goes through ``current``, so
    it inherits the owner-scoped read every other method uses.
    """
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    with pytest.raises(ApiError) as owned:
        run(lambda: service.key(recording_id, OTHER_OWNER_ID))
    with pytest.raises(ApiError) as absent:
        run(lambda: service.key("f" * 32, OTHER_OWNER_ID))

    assert owned.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert absent.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert owned.value.message == absent.value.message


def test_the_same_stored_analysis_always_yields_the_same_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    first = run(lambda: service.key(recording_id, OWNER_ID))
    second = run(lambda: service.key(recording_id, OWNER_ID))

    assert first is not None and second is not None
    assert first.model_dump() == second.model_dump()


def test_estimating_a_key_leaves_the_stored_analysis_untouched(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """A derived measurement writes nothing, and the double counts the writes."""
    analyses = CountingAnalysisRepository()
    stored = _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    before = stored.model_dump()
    writes_before = analyses.writes

    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    assert run(lambda: service.key(recording_id, OWNER_ID)) is not None

    after = run(lambda: analyses.get(stored.audio_analysis_id))
    assert after is not None
    assert after.model_dump() == before
    assert analyses.writes == writes_before


def test_estimating_a_key_re_measures_nothing(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The stored timeline is the input; the decoder is never reached.

    If this ever fails, the key and the pitch graph are being measured from two
    different passes over the audio and could disagree with each other.
    """
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    run(lambda: service.key(recording_id, OWNER_ID))

    assert analyzer.calls == []


def test_a_key_is_read_from_the_stored_timeline_and_not_from_the_audio(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """Proved by disagreement: the stored file is a 440 Hz tone.

    A key derived from the audio would find one pitch class and refuse. This
    stores a C major timeline against that same recording, so an answer of
    C major can only have come from the timeline.
    """
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=SignalAudioAnalyzer()
    )

    analysis = run(lambda: service.key(recording_id, OWNER_ID))

    assert analysis is not None and analysis.key is not None
    assert analysis.key.tonic == "C"


# --- The service under the performance ceiling ------------------------------
#
# Layer 1 bounds the arithmetic in ``test_audio_key.py``. What is bounded here
# is everything the service adds around it: how many times the analysis document
# is loaded, whether anything is written, and whether the work it does per call
# is the estimator's or something that grew around it.


def _ceiling_points() -> int:
    """Points in the longest recording this product accepts. See ``test_audio_key``."""
    return math.floor(Settings().max_audio_duration_seconds / HOP_SECONDS)


def _long_melody(points: int) -> tuple[PitchPoint, ...]:
    """``_timeline`` stretched to exactly ``points`` frames, keeping the melody's shape.

    The remainder is spent on the tonic rather than truncated away, so the
    timeline is the requested length exactly. The ceiling is the assertion, and
    a benchmark 151 points short of it is measuring a different recording.
    """
    scale = max(1, points // sum(_C_MAJOR_MELODY.values()))
    stretched = {pitch_class: frames * scale for pitch_class, frames in _C_MAJOR_MELODY.items()}
    stretched[0] += points - sum(stretched.values())
    return _timeline(stretched)


def test_estimating_a_key_loads_the_analysis_exactly_once(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """One document read per call, and the count is the assertion.

    The document is the cost of this request — the timeline inside it is up to
    12 931 points of JSONB, against ~1.4 ms of arithmetic to fold it. A second
    load would roughly double the expensive half of a read-only endpoint, so it
    is worth a test that says so in numbers rather than a comment that says so
    in prose.
    """
    analyses = CountingAnalysisRepository()
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    analyses.loads = 0
    analyses.writes = 0

    assert run(lambda: service.key(recording_id, OWNER_ID)) is not None

    assert analyses.loads == 1
    assert analyses.writes == 0


def test_folding_a_key_from_a_record_already_held_loads_nothing(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """The seam the endpoint uses, and the reason it exists.

    A caller holding the record — the endpoint does, because the response
    carries the recording and analysis ids — must be able to fold its key
    without going back to the repository. Zero loads is the whole point of the
    method, so zero loads is what is asserted.
    """
    analyses = CountingAnalysisRepository()
    stored = _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    analyses.loads = 0
    analyses.writes = 0

    folded = service.key_of(stored)

    assert folded is not None and folded.key is not None
    assert folded.key.tonic == "C"
    assert analyses.loads == 0
    assert analyses.writes == 0


def test_folding_a_key_from_a_record_agrees_with_looking_one_up(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The two seams must not be two answers.

    ``key_of`` exists to save a read, not to become a second implementation. If
    these ever differ, the endpoint and the service method are describing the
    same recording differently.
    """
    stored = _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    looked_up = run(lambda: service.key(recording_id, OWNER_ID))
    folded = service.key_of(stored)

    assert looked_up is not None and folded is not None
    assert looked_up.model_dump() == folded.model_dump()


def test_folding_a_key_from_nothing_is_the_same_absence_as_looking_one_up(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """``None`` means "no completed measurement", at both seams and for all four causes."""
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    assert service.key_of(None) is None

    for status in (
        AudioAnalysisStatus.PENDING,
        AudioAnalysisStatus.ANALYZING,
        AudioAnalysisStatus.FAILED,
    ):
        record = AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(),
            recording_id=recording_id,
            status=status,
            error_code=(
                ErrorCode.AUDIO_ANALYSIS_FAILED if status is AudioAnalysisStatus.FAILED else None
            ),
        )
        assert service.key_of(record) is None


def test_a_completed_analysis_cannot_reach_the_fold_without_metrics(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The fourth cause of ``None`` is unreachable, and that is worth stating.

    ``key_of`` refuses a completed record with no metrics, because without the
    stored settings there is no hop to fold the timeline by. The model refuses
    to represent one at all, so the guard cannot fire — it is kept for the same
    reason ``_correlation``'s zero-variance branch is kept: a function must not
    misbehave for an input its own signature permits. Asserting the model's
    refusal here says which of the two is load-bearing.
    """
    with pytest.raises(ValueError, match="must have metrics"):
        AudioAnalysis(
            audio_analysis_id=new_audio_analysis_id(),
            recording_id=recording_id,
            status=AudioAnalysisStatus.COMPLETED,
            completed_at=utc_now(),
        )


def test_the_service_adds_nothing_measurable_to_the_estimator_at_the_ceiling(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """A full-length recording through the service, against the same fold alone.

    The doubles hold the record in a dictionary, so this deliberately does not
    measure PostgreSQL — it measures what the *service* contributes. What it
    must not contain is a second fold, a copy of the timeline, or a
    re-validation of every point: any of those would put the call well beyond
    twice the arithmetic it exists to perform.

    The upper bound is stated against the estimator rather than in milliseconds
    so it survives being run on hardware nobody has measured.
    """
    points = _long_melody(_ceiling_points())
    assert len(points) == _ceiling_points()

    analyses = CountingAnalysisRepository()
    _store_completed(analyses, recording_id, points)
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    def through_service() -> None:
        run(lambda: service.key(recording_id, OWNER_ID))

    def estimator_only() -> None:
        analyse_key(points, frame_seconds=_HOP_SECONDS)

    through_service()
    estimator_only()
    service_seconds = min(_seconds(through_service) for _ in range(5))
    estimator_seconds = min(_seconds(estimator_only) for _ in range(5))

    assert service_seconds < estimator_seconds * 3


def test_a_full_length_recording_still_yields_its_key(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The ceiling is a length the feature answers, not merely one it survives.

    A benchmark against an input the estimator refuses would be timing the
    gates. This asserts the 300-second case is a measured one.
    """
    points = _long_melody(_ceiling_points())
    _store_completed(analyses, recording_id, points)
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    analysis = run(lambda: service.key(recording_id, OWNER_ID))

    assert analysis is not None and analysis.key is not None
    assert analysis.key.tonic == "C"
    assert analysis.key.mode is KeyMode.MAJOR
    assert analysis.voiced_seconds == pytest.approx(300.0, abs=1.0)


def _seconds(call: Callable[[], None]) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


# --- Which reads carry a timeline ------------------------------------------
#
# The stored document is almost entirely its pitch timeline — 1 596 kB against
# 1.2 kB for everything else at the longest accepted recording, which measured
# 87 ms and 19 MB of peak allocation per read versus 1.7 ms and 14 kB without
# the points. These count which paths pay for it, so a decision about state that
# starts loading a timeline fails here.


def test_starting_an_analysis_reads_no_timeline(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """`start` decides from statuses and timestamps, never from points.

    It is also the worst place to load one: the decision walks *every* analysis
    the recording has ever had, so a re-analysed recording would cost a document
    per attempt on each ``POST``.
    """
    analyses = CountingAnalysisRepository()
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    started = run(lambda: service.start(recording_id, OWNER_ID))

    assert started.created is False
    assert analyses.loads == 0
    assert analyses.summary_loads > 0
    # The record it hands back knows how much was measured without holding it.
    assert started.analysis.pitch_point_count > 0
    assert not hasattr(started.analysis, "pitch_points")


def test_claiming_feedback_reads_no_timeline(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """The claim decides whether a paid provider call may happen.

    Every input to that decision is a status, an error code or the presence of
    metrics. The timeline is loaded once by `run_feedback`, after the claim has
    been won, so a request that loses the race pays nothing for the points.
    """
    analyses = CountingAnalysisRepository()
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    claimed = run(lambda: service.start_feedback(recording_id, OWNER_ID))

    assert claimed.feedback_status is AudioFeedbackStatus.GENERATING
    assert analyses.loads == 0
    assert not hasattr(claimed, "pitch_points")


def test_reading_the_note_breakdown_loads_the_analysis_once(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    recording_id: str,
) -> None:
    """The property Phase 8 slice 5 established for the key, now for the notes."""
    analyses = CountingAnalysisRepository()
    _store_completed(analyses, recording_id, _timeline(_C_MAJOR_MELODY))
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    notes = run(lambda: service.notes(recording_id, OWNER_ID))

    assert notes
    assert analyses.loads == 1


def test_a_summary_read_still_reports_the_measured_frames(
    recordings: InMemoryRecordingRepository,
    storage: RecordingStorage,
    analyses: InMemoryAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """Leaving the points in the store must not lose the fact that they exist.

    A summary reporting no points would read exactly like an analysis that
    measured nothing — the one thing a stripped document could quietly get
    wrong.
    """
    points = _timeline(_C_MAJOR_MELODY)
    _store_completed(analyses, recording_id, points)
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    summary = run(lambda: service.current(recording_id, OWNER_ID))
    whole = run(lambda: service.current_timeline(recording_id, OWNER_ID))

    assert summary is not None and whole is not None
    assert summary.pitch_point_count == len(points) == len(whole.pitch_points)
    assert summary.audio_analysis_id == whole.audio_analysis_id
