"""The audio-analysis workflow, driven with a stub analyzer.

The measurement itself is tested in ``test_audio_analyzer.py`` against real
files. What is tested here is everything *around* it: idempotency, the failure
path, the staleness sweep, and the guarantee that a background task cannot take
the process down. A stub analyzer makes those assertions fast and lets a
failure be provoked deliberately rather than by finding a file that breaks numpy.
"""

import asyncio
from pathlib import Path
from typing import Any

import anyio
import pytest

from app.core.errors import ApiError, ErrorCode
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.analyzer import (
    AudioAnalysisResult,
    AudioAnalyzer,
    SignalAudioAnalyzer,
)
from app.services.audio_analysis.errors import (
    AudioAnalysisError,
    AudioUnsupportedError,
    InsufficientPitchSignalError,
)
from app.services.audio_analysis.models import (
    AnalysisSettings,
    AudioAnalysisStatus,
    AudioMetrics,
    Loudness,
    PitchPoint,
    PitchStability,
    VocalRange,
)
from app.services.audio_analysis.repository import JsonFileAudioAnalysisRepository
from app.services.orchestration.audio_analysis import AudioAnalysisService
from app.services.recordings.models import Recording
from app.services.recordings.repository import JsonFileRecordingRepository
from tests.fixtures import harmonic_samples, write_signal_wav

SAMPLE_RATE = 22050


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
def recordings(storage_root: Path) -> JsonFileRecordingRepository:
    return JsonFileRecordingRepository(storage_root)


@pytest.fixture
def analyses(storage_root: Path) -> JsonFileAudioAnalysisRepository:
    return JsonFileAudioAnalysisRepository(storage_root)


@pytest.fixture
def storage(storage_root: Path) -> RecordingStorage:
    return RecordingStorage(storage_root)


@pytest.fixture
def recording_id(
    tmp_path: Path, storage: RecordingStorage, recordings: JsonFileRecordingRepository
) -> str:
    """A genuinely stored recording, written through the real storage layer."""
    source = write_signal_wav(
        tmp_path / "take.wav",
        harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    stored = storage.save(source, extension=".wav")
    recordings.create(
        Recording(
            recording_id=stored.recording_id,
            original_filename="take.wav",
            audio_format="wav",
            duration_seconds=2.0,
            sample_rate=SAMPLE_RATE,
            channels=1,
            size_bytes=stored.size_bytes,
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
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    analyzer: AudioAnalyzer,
    stale_after_seconds: float = 900,
) -> AudioAnalysisService:
    return AudioAnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=analyzer,
        stale_after_seconds=stale_after_seconds,
    )


# --- The happy path --------------------------------------------------------


def test_an_analysis_completes_and_is_persisted(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    result = run(lambda: service.analyze(recording_id))

    assert result.status is AudioAnalysisStatus.COMPLETED
    assert result.metrics is not None
    assert result.metrics.pitch is not None
    assert result.pitch_points
    assert result.completed_at is not None

    stored = analyses.get(result.audio_analysis_id)
    assert stored is not None
    assert stored.status is AudioAnalysisStatus.COMPLETED


def test_the_analyzer_receives_the_stored_path_not_a_filename(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The id is the only thing that reaches the filesystem."""
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    run(lambda: service.analyze(recording_id))

    assert len(analyzer.calls) == 1
    assert analyzer.calls[0] == storage.path_for(recording_id)
    assert "take" not in analyzer.calls[0].name


def test_start_records_progress_before_the_slow_work(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    started = run(lambda: service.start(recording_id))
    assert started.created is True
    assert started.analysis.status is AudioAnalysisStatus.PENDING
    assert analyses.get(started.analysis.audio_analysis_id) is not None


# --- Idempotency -----------------------------------------------------------


def test_a_second_start_returns_the_first_analysis(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    first = run(lambda: service.start(recording_id))
    second = run(lambda: service.start(recording_id))

    assert second.created is False
    assert second.analysis.audio_analysis_id == first.analysis.audio_analysis_id


def test_analysing_twice_measures_the_file_once(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    first = run(lambda: service.analyze(recording_id))
    second = run(lambda: service.analyze(recording_id))

    assert len(analyzer.calls) == 1
    assert second.audio_analysis_id == first.audio_analysis_id


def test_running_the_same_analysis_twice_measures_once(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """Even a caller that schedules the work twice cannot make it happen twice."""
    analyzer = StubAnalyzer()
    service = build(recordings=recordings, storage=storage, analyses=analyses, analyzer=analyzer)

    started = run(lambda: service.start(recording_id))
    run(lambda: service.run(started.analysis.audio_analysis_id))
    run(lambda: service.run(started.analysis.audio_analysis_id))

    assert len(analyzer.calls) == 1


def test_a_failed_analysis_is_retried_as_a_new_record(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """The failed record stays on disk and inspectable; the retry is separate."""
    failing = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=AudioUnsupportedError(reason="RuntimeError")),
    )
    failed = run(lambda: failing.analyze(recording_id))
    assert failed.status is AudioAnalysisStatus.FAILED

    working = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    retried = run(lambda: working.analyze(recording_id))

    assert retried.audio_analysis_id != failed.audio_analysis_id
    assert retried.status is AudioAnalysisStatus.COMPLETED
    assert analyses.get(failed.audio_analysis_id) is not None


def test_concurrent_starts_create_one_analysis(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    async def start_many() -> list[Any]:
        return list(await asyncio.gather(*(service.start(recording_id) for _ in range(6))))

    results = run(start_many)

    assert sum(1 for started in results if started.created) == 1
    assert len({started.analysis.audio_analysis_id for started in results}) == 1


def test_an_abandoned_analysis_is_swept_so_the_recording_is_not_stuck(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
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
    started = run(lambda: stalled.start(recording_id))
    analyses.update(started.analysis.model_copy(update={"status": AudioAnalysisStatus.ANALYZING}))

    impatient = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(),
        stale_after_seconds=0,
    )
    retried = run(lambda: impatient.start(recording_id))

    assert retried.created is True
    assert retried.analysis.audio_analysis_id != started.analysis.audio_analysis_id
    swept = analyses.get(started.analysis.audio_analysis_id)
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
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
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

    result = run(lambda: service.analyze(recording_id))

    assert result.status is AudioAnalysisStatus.FAILED
    assert result.error_code is code
    assert result.completed_at is not None


def test_an_unexpected_exception_leaves_an_honest_record(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """A background task must never take the process down."""
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=ZeroDivisionError("numpy said no")),
    )

    result = run(lambda: service.analyze(recording_id))

    assert result.status is AudioAnalysisStatus.FAILED
    assert result.error_code is ErrorCode.INTERNAL_ERROR


def test_cancellation_is_recorded_and_re_raised(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=StubAnalyzer(error=asyncio.CancelledError()),
    )

    started = run(lambda: service.start(recording_id))
    with pytest.raises(asyncio.CancelledError):
        run(lambda: service.run(started.analysis.audio_analysis_id))

    stored = analyses.get(started.analysis.audio_analysis_id)
    assert stored is not None
    assert stored.status is AudioAnalysisStatus.FAILED


def test_an_unknown_recording_is_refused_without_creating_a_record(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )

    with pytest.raises(ApiError) as caught:
        run(lambda: service.start("0" * 32))
    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert analyses.list_for_recording("0" * 32) == []


def test_running_an_unknown_analysis_is_refused(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    with pytest.raises(ApiError) as caught:
        run(lambda: service.run("0" * 32))
    assert caught.value.code is ErrorCode.AUDIO_ANALYSIS_NOT_FOUND


def test_current_refuses_an_unknown_recording(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    with pytest.raises(ApiError) as caught:
        service.current("0" * 32)
    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND


def test_current_is_none_before_anything_has_run(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    service = build(
        recordings=recordings, storage=storage, analyses=analyses, analyzer=StubAnalyzer()
    )
    assert service.current(recording_id) is None


# --- With the real analyzer ------------------------------------------------


def test_the_real_analyzer_measures_a_real_stored_recording(
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    recording_id: str,
) -> None:
    """One pass with nothing stubbed: storage, decoder, detector, repository."""
    service = build(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=SignalAudioAnalyzer(),
    )

    result = run(lambda: service.analyze(recording_id))

    assert result.status is AudioAnalysisStatus.COMPLETED
    assert result.metrics is not None
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A4"
    assert len(result.pitch_points) > 10
