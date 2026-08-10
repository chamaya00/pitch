"""AI interpretation of audio measurements.

Three separable things are tested here, and the boundaries between them are the
point of the design:

* **The payload** — what a provider is given. Pure, so it is asserted directly
  against domain objects rather than through a request.
* **The workflow** — when a provider is called at all, and what happens when it
  fails. Driven with stub providers so a failure can be provoked deliberately.
* **The mock provider** — the executable statement of what the prompt asks a
  real model for: no invented numbers, no timbre labels, no score.

The rule under all of it: **a model explains measurements and never produces
them.** Every test that could be satisfied by a model inventing a number is
written so that it is not.
"""

from pathlib import Path
from typing import Any

import anyio
import pytest

from app.core.errors import ApiError, ErrorCode
from app.services.ai.errors import (
    ProviderNotConfiguredError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.services.ai.mock import MockFeedbackProvider
from app.services.ai.protocols import AudioFeedbackProvider
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.feedback_payload import (
    MAX_NOTES,
    build_request,
    to_json,
    to_payload,
)
from app.services.audio_analysis.models import (
    IN_TUNE_CENTS,
    AnalysisSettings,
    AudioAnalysisStatus,
    AudioFeedback,
    AudioFeedbackRequest,
    AudioFeedbackStatus,
    AudioMetrics,
    Loudness,
    NoteSummary,
    PitchStability,
    SpectralFeatures,
    VocalRange,
)
from app.services.audio_analysis.repository import JsonFileAudioAnalysisRepository
from app.services.orchestration.audio_analysis import AudioAnalysisService
from app.services.recordings.models import Recording
from app.services.recordings.repository import JsonFileRecordingRepository
from tests.fixtures import harmonic_samples, silence_samples, write_signal_wav

SAMPLE_RATE = 22050


def run(coroutine_factory: Any) -> Any:
    return anyio.run(coroutine_factory)


# --- Domain fixtures -------------------------------------------------------


def settings() -> AnalysisSettings:
    return AnalysisSettings(
        sample_rate_hz=SAMPLE_RATE,
        frame_length_samples=2048,
        hop_length_samples=512,
        min_frequency_hz=65.0,
        max_frequency_hz=1100.0,
        clarity_threshold=0.8,
        silence_rms=0.005,
    )


def stability(**overrides: Any) -> PitchStability:
    base: dict[str, Any] = {
        "voiced_ratio": 0.74,
        "total_frames": 100,
        "voiced_frames": 74,
        "mean_cents_deviation": -17.2,
        "mean_abs_cents_deviation": 21.5,
        "cents_std": 18.4,
        "semitone_variance": 3.2,
        "in_tune_ratio": 0.71,
    }
    base.update(overrides)
    return PitchStability(**base)


def metrics(**overrides: Any) -> AudioMetrics:
    base: dict[str, Any] = {
        "settings": settings(),
        "duration_seconds": 12.4,
        "pitch": VocalRange(
            lowest_frequency_hz=98.0,
            highest_frequency_hz=523.25,
            lowest_note="G2",
            highest_note="C5",
            semitone_span=29,
        ),
        "stability": stability(),
        "loudness": Loudness(rms=0.18, peak=0.92, dynamic_range_db=15.8, clipped_sample_ratio=0.0),
        "spectral": SpectralFeatures(
            centroid_hz=2180.0,
            bandwidth_hz=1900.0,
            rolloff_hz=4200.0,
            zero_crossing_rate=0.08,
            flatness=0.12,
        ),
    }
    base.update(overrides)
    return AudioMetrics(**base)


def note(midi: int, name: str, share: float) -> NoteSummary:
    return NoteSummary(
        midi_note=midi,
        note_name=name,
        duration_seconds=share / 10,
        percentage_of_voiced_time=share,
        frame_count=int(share),
        average_cents=-4.1,
        mean_abs_cents=8.7,
        in_tune_ratio=0.84,
    )


NOTES = (note(60, "C4", 31.2), note(62, "D4", 20.0), note(64, "E4", 16.0))


# --- The payload -----------------------------------------------------------


def test_the_payload_carries_the_measurements_a_model_can_interpret() -> None:
    payload = to_payload(build_request(metrics(), NOTES))

    assert payload["recording"]["duration_seconds"] == pytest.approx(12.4)
    assert payload["detected_range"]["lowest_note"] == "G2"
    assert payload["pitch"]["in_tune_ratio"] == pytest.approx(0.71)
    assert payload["loudness"]["peak_amplitude_0_to_1"] == pytest.approx(0.92)
    assert payload["note_breakdown"][0]["note_name"] == "C4"


def test_the_payload_defines_every_term_it_uses() -> None:
    """A model cannot state a definition it was never given."""
    definitions = to_payload(build_request(metrics(), NOTES))["definitions"]

    assert str(IN_TUNE_CENTS).startswith("25")
    assert "25" in definitions["in_tune_ratio"]
    assert "not a" in definitions["detected_range"]
    assert "physiological" in definitions["detected_range"]
    assert "not of the recording" in definitions["percentage_of_voiced_time"]


def test_an_unavailable_measurement_is_absent_not_null() -> None:
    """A key with a null value invites a model to fill it in. A missing key does not."""
    payload = to_payload(
        build_request(
            metrics(
                stability=stability(in_tune_ratio=None, mean_cents_deviation=None, cents_std=None),
                loudness=Loudness(
                    rms=0.1, peak=0.4, dynamic_range_db=None, clipped_sample_ratio=0.0
                ),
            ),
            (),
        )
    )

    assert "in_tune_ratio" not in payload["pitch"]
    assert "mean_cents_deviation" not in payload["pitch"]
    assert "dynamic_range_db_estimate" not in payload["loudness"]
    assert "null" not in to_json(build_request(metrics(stability=stability(cents_std=None)), ()))


def test_an_unavailable_measurement_never_becomes_zero() -> None:
    payload = to_payload(
        build_request(
            metrics(
                stability=stability(in_tune_ratio=None, mean_abs_cents_deviation=None),
                pitch=None,
                spectral=None,
            ),
            (),
        )
    )

    assert payload["pitch"].get("in_tune_ratio") != 0
    assert payload["pitch"].get("mean_abs_cents_deviation") != 0
    assert "detected_range" not in payload
    assert "spectral" not in payload


def test_a_measured_zero_is_still_sent() -> None:
    """Zero clipping is a measurement. Absent clipping is not."""
    payload = to_payload(
        build_request(
            metrics(
                loudness=Loudness(rms=0.1, peak=0.4, dynamic_range_db=1.0, clipped_sample_ratio=0.0)
            ),
            (),
        )
    )
    assert payload["loudness"]["clipped_sample_ratio"] == 0.0


def test_the_payload_leaves_out_what_cannot_be_interpreted_safely() -> None:
    """Documented exclusions — see the module docstring in feedback_payload."""
    text = to_json(build_request(metrics(), NOTES))
    assert "semitone_variance" not in text
    assert "unstable_section" not in text


def test_the_payload_carries_no_audio_no_path_and_no_identifier() -> None:
    request = build_request(metrics(), NOTES)
    fields = set(AudioFeedbackRequest.model_fields)

    assert not fields & {"recording_id", "path", "audio", "filename", "audio_analysis_id"}
    text = to_json(request)
    for leak in ("/tmp", ".wav", "recording_id", "storage"):
        assert leak not in text


def test_the_note_list_is_capped() -> None:
    many = tuple(note(60 + index, "C4", 1.0) for index in range(20))
    request = build_request(metrics(), many)
    assert len(request.notes) == MAX_NOTES


def test_the_payload_is_deterministic() -> None:
    first = to_json(build_request(metrics(), NOTES))
    second = to_json(build_request(metrics(), NOTES))
    assert first == second


# --- The mock provider -----------------------------------------------------


def test_the_mock_is_deterministic() -> None:
    provider = MockFeedbackProvider()
    request = build_request(metrics(), NOTES)

    first = run(lambda: provider.interpret_audio(request))
    second = run(lambda: provider.interpret_audio(request))
    assert first == second


def test_the_mock_declares_itself_as_demo_data() -> None:
    feedback = run(lambda: MockFeedbackProvider().interpret_audio(build_request(metrics(), NOTES)))

    assert feedback.provenance.is_mock is True
    assert feedback.provenance.provider == "mock"
    assert "Demo" in feedback.summary


def test_the_mock_says_nothing_about_measurements_it_was_not_given() -> None:
    feedback = run(
        lambda: MockFeedbackProvider().interpret_audio(
            build_request(
                metrics(pitch=None, spectral=None, stability=stability(in_tune_ratio=None)), ()
            )
        )
    )

    assert feedback.range_observations == ()
    assert feedback.note_observations == ()
    assert not any("in-tune" in item for item in feedback.pitch_observations)


def test_the_mock_never_claims_a_physiological_range() -> None:
    feedback = run(lambda: MockFeedbackProvider().interpret_audio(build_request(metrics(), NOTES)))
    text = " ".join(feedback.range_observations).lower()

    assert "detected in this recording" in text
    assert "not the limit of your voice" in text


def test_the_mock_never_labels_timbre_or_scores_the_singer() -> None:
    feedback = run(lambda: MockFeedbackProvider().interpret_audio(build_request(metrics(), NOTES)))
    text = feedback.model_dump_json().lower()

    for label in ("bright", "dark", "breathy", "nasal", "warm", "powerful", "weak", "healthy"):
        assert label not in text, f'the mock described the voice as "{label}"'
    for word in ("score", "grade", "rating", "singing ability", "out of 10"):
        assert word not in text, f'the mock produced a "{word}"'


def test_the_mock_calls_the_longest_note_frequent_not_best() -> None:
    feedback = run(lambda: MockFeedbackProvider().interpret_audio(build_request(metrics(), NOTES)))
    text = " ".join(feedback.note_observations).lower()

    assert "most frequent" in text
    assert "best" not in text.replace("not the same as the best one", "")


def test_the_mock_warns_about_clipping_only_when_it_was_measured() -> None:
    clipped = run(
        lambda: MockFeedbackProvider().interpret_audio(
            build_request(
                metrics(
                    loudness=Loudness(
                        rms=0.3, peak=1.0, dynamic_range_db=4.0, clipped_sample_ratio=0.02
                    )
                ),
                NOTES,
            )
        )
    )
    assert any("full scale" in item for item in clipped.audio_observations)

    clean = run(lambda: MockFeedbackProvider().interpret_audio(build_request(metrics(), NOTES)))
    assert not any("full scale" in item for item in clean.audio_observations)


def test_the_mock_satisfies_the_audio_protocol() -> None:
    assert isinstance(MockFeedbackProvider(), AudioFeedbackProvider)


# --- The workflow ----------------------------------------------------------


class StubFeedbackProvider:
    """Records what it was asked, and answers however a test wants."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.requests: list[AudioFeedbackRequest] = []
        self._error = error

    @property
    def name(self) -> str:
        return "stub"

    async def interpret_audio(self, request: AudioFeedbackRequest) -> AudioFeedback:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return run_feedback_fixture()


def run_feedback_fixture() -> AudioFeedback:
    from app.services.analysis.models import Provenance

    return AudioFeedback(
        summary="A stub interpretation.",
        strengths=("Something measurable.",),
        pitch_observations=("The pitch sat close to a note.",),
        provenance=Provenance(provider="stub", model="stub-1", is_mock=False),
    )


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


def store(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    samples: list[float],
    name: str,
) -> str:
    source = write_signal_wav(tmp_path / name, samples, sample_rate=SAMPLE_RATE)
    stored = storage.save(source, extension=".wav")
    recordings.create(
        Recording(
            recording_id=stored.recording_id,
            original_filename=name,
            audio_format="wav",
            duration_seconds=2.0,
            sample_rate=SAMPLE_RATE,
            channels=1,
            size_bytes=stored.size_bytes,
        )
    )
    return stored.recording_id


def service(
    *,
    recordings: JsonFileRecordingRepository,
    storage: RecordingStorage,
    analyses: JsonFileAudioAnalysisRepository,
    feedback: AudioFeedbackProvider,
) -> AudioAnalysisService:
    from app.services.audio_analysis.analyzer import SignalAudioAnalyzer

    return AudioAnalysisService(
        recordings=recordings,
        storage=storage,
        analyses=analyses,
        analyzer=SignalAudioAnalyzer(),
        feedback=feedback,
        stale_after_seconds=900,
    )


def analysed(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
    provider: AudioFeedbackProvider,
    *,
    samples: list[float] | None = None,
    name: str = "take.wav",
) -> tuple[AudioAnalysisService, str]:
    """A recording that has been through the real analyzer."""
    audio = samples or harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE)
    recording_id = store(tmp_path, storage, recordings, audio, name)
    built = service(recordings=recordings, storage=storage, analyses=analyses, feedback=provider)
    run(lambda: built.analyze(recording_id))
    return built, recording_id


def test_the_provider_receives_the_structured_payload(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider()
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    claimed = run(lambda: built.start_feedback(recording_id))
    run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert isinstance(request, AudioFeedbackRequest)
    assert request.pitch is not None
    assert request.pitch.lowest_note == "A4"
    assert request.notes


def test_feedback_is_persisted_on_the_analysis(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, StubFeedbackProvider())
    claimed = run(lambda: built.start_feedback(recording_id))
    completed = run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert completed.feedback_status is AudioFeedbackStatus.COMPLETED
    assert completed.feedback is not None
    assert completed.feedback.summary == "A stub interpretation."

    stored = analyses.get(completed.audio_analysis_id)
    assert stored is not None
    assert stored.feedback is not None


def test_feedback_is_not_generated_twice(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    """A re-render must not run up a provider bill."""
    provider = StubFeedbackProvider()
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    first = run(lambda: built.start_feedback(recording_id))
    run(lambda: built.run_feedback(first.audio_analysis_id))
    second = run(lambda: built.start_feedback(recording_id))
    run(lambda: built.run_feedback(second.audio_analysis_id))

    assert len(provider.requests) == 1
    assert second.feedback_status is AudioFeedbackStatus.COMPLETED


def test_a_claimed_run_is_not_executed_twice(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider()
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    claimed = run(lambda: built.start_feedback(recording_id))
    run(lambda: built.run_feedback(claimed.audio_analysis_id))
    run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert len(provider.requests) == 1


def test_a_provider_failure_leaves_the_measurements_intact(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    """The whole point of separating measurement from interpretation."""
    provider = StubFeedbackProvider(error=ProviderUnavailableError("stub", reason="down"))
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    claimed = run(lambda: built.start_feedback(recording_id))
    failed = run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert failed.feedback_status is AudioFeedbackStatus.FAILED
    assert failed.feedback_error_code is ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE
    assert failed.feedback is None
    # The analysis itself is untouched.
    assert failed.status is AudioAnalysisStatus.COMPLETED
    assert failed.metrics is not None
    assert failed.metrics.pitch is not None
    assert failed.pitch_points


def test_invalid_model_output_fails_safely(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider(
        error=ProviderResponseError("stub", reason="invalid_structured_output")
    )
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    claimed = run(lambda: built.start_feedback(recording_id))
    failed = run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert failed.feedback_error_code is ErrorCode.ANALYSIS_PROVIDER_ERROR
    assert failed.feedback is None


def test_an_unexpected_exception_does_not_take_the_process_down(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider(error=ZeroDivisionError("boom"))
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, provider)

    claimed = run(lambda: built.start_feedback(recording_id))
    failed = run(lambda: built.run_feedback(claimed.audio_analysis_id))

    assert failed.feedback_status is AudioFeedbackStatus.FAILED
    assert failed.feedback_error_code is ErrorCode.INTERNAL_ERROR


def test_a_failed_run_can_be_retried(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    failing = StubFeedbackProvider(error=ProviderUnavailableError("stub", reason="down"))
    built, recording_id = analysed(tmp_path, storage, recordings, analyses, failing)
    claimed = run(lambda: built.start_feedback(recording_id))
    run(lambda: built.run_feedback(claimed.audio_analysis_id))

    working = StubFeedbackProvider()
    retried_service = service(
        recordings=recordings, storage=storage, analyses=analyses, feedback=working
    )
    again = run(lambda: retried_service.start_feedback(recording_id))
    completed = run(lambda: retried_service.run_feedback(again.audio_analysis_id))

    assert completed.feedback_status is AudioFeedbackStatus.COMPLETED
    assert len(working.requests) == 1


def test_insufficient_pitch_never_reaches_the_provider(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    """Ordinary speech must not come back as a vocal assessment."""
    provider = StubFeedbackProvider()
    built, recording_id = analysed(
        tmp_path,
        storage,
        recordings,
        analyses,
        provider,
        samples=silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        name="silence.wav",
    )

    with pytest.raises(ApiError) as caught:
        run(lambda: built.start_feedback(recording_id))

    assert caught.value.code is ErrorCode.INSUFFICIENT_PITCH_SIGNAL
    assert provider.requests == [], "a provider was called with nothing to interpret"


def test_an_unanalysed_recording_never_reaches_the_provider(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider()
    recording_id = store(
        tmp_path,
        storage,
        recordings,
        harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE),
        "take.wav",
    )
    built = service(recordings=recordings, storage=storage, analyses=analyses, feedback=provider)

    with pytest.raises(ApiError) as caught:
        run(lambda: built.start_feedback(recording_id))

    assert caught.value.code is ErrorCode.AUDIO_ANALYSIS_NOT_FOUND
    assert provider.requests == []


def test_an_unknown_recording_never_reaches_the_provider(
    tmp_path: Path,
    storage: RecordingStorage,
    recordings: JsonFileRecordingRepository,
    analyses: JsonFileAudioAnalysisRepository,
) -> None:
    provider = StubFeedbackProvider()
    built = service(recordings=recordings, storage=storage, analyses=analyses, feedback=provider)

    with pytest.raises(ApiError) as caught:
        run(lambda: built.start_feedback("0" * 32))

    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND
    assert provider.requests == []


def test_a_missing_credential_is_a_configuration_error_not_a_silent_mock() -> None:
    """The factory has no fallback, and audio feedback does not add one."""
    from app.core.config import Settings
    from app.services.ai.factory import build_audio_feedback_provider

    configured = Settings(_env_file=None, feedback_provider="claude", anthropic_api_key=None)
    with pytest.raises(ProviderNotConfiguredError):
        build_audio_feedback_provider(configured)


def test_both_halves_of_the_product_share_one_provider() -> None:
    """One credential, one client — never real prose on one half and demo on the other."""
    from app.core.config import Settings
    from app.services.ai.factory import build_audio_feedback_provider, build_feedback_provider

    configured = Settings(_env_file=None, feedback_provider="mock")
    assert type(build_feedback_provider(configured)) is type(
        build_audio_feedback_provider(configured)
    )
