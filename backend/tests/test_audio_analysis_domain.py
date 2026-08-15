"""Invariants of the audio-analysis domain model and its store.

The model's job is to make an untrustworthy record impossible to construct: a
failure that does not say why, a completion with nothing measured, a range whose
top is below its bottom. Those are the assertions here.

The repository's job is that a record survives a process restart and that a
reader never sees a half-written one.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.errors import ErrorCode
from app.services.audio_analysis.models import (
    AnalysisSettings,
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioMetrics,
    Loudness,
    PitchPoint,
    PitchStability,
    SpectralFeatures,
    UnstableSection,
    VocalRange,
    new_audio_analysis_id,
)
from app.services.audio_analysis.repository import (
    AudioAnalysisAlreadyExistsError,
    AudioAnalysisNotFoundError,
    AudioAnalysisRepository,
    JsonFileAudioAnalysisRepository,
)

RECORDING_ID = "8f14e45fceea167a5a36dedd4bea2543"


def settings() -> AnalysisSettings:
    return AnalysisSettings(
        sample_rate_hz=44100,
        frame_length_samples=4096,
        hop_length_samples=1024,
        min_frequency_hz=65.0,
        max_frequency_hz=1100.0,
        clarity_threshold=0.8,
        silence_rms=0.005,
    )


def metrics(**overrides: object) -> AudioMetrics:
    base: dict[str, object] = {
        "settings": settings(),
        "duration_seconds": 3.0,
        "pitch": VocalRange(
            lowest_frequency_hz=220.0,
            highest_frequency_hz=440.0,
            lowest_note="A3",
            highest_note="A4",
            semitone_span=12,
        ),
        "stability": PitchStability(
            voiced_ratio=0.5, total_frames=100, voiced_frames=50, mean_cents_deviation=-3.0
        ),
        "loudness": Loudness(rms=0.2, peak=0.8, clipped_sample_ratio=0.0),
        "spectral": SpectralFeatures(
            centroid_hz=900.0,
            bandwidth_hz=400.0,
            rolloff_hz=1800.0,
            zero_crossing_rate=0.05,
            flatness=0.02,
        ),
    }
    base.update(overrides)
    return AudioMetrics(**base)  # type: ignore[arg-type]


def _points(count: int) -> tuple[PitchPoint, ...]:
    """A timeline of ``count`` measured frames."""
    return tuple(
        PitchPoint(
            timestamp_seconds=index * 0.02,
            frequency_hz=440.0,
            midi_note=69,
            note_name="A4",
            cents=0.0,
            confidence=0.95,
        )
        for index in range(count)
    )


def analysis(**overrides: object) -> AudioAnalysis:
    base: dict[str, object] = {
        "audio_analysis_id": new_audio_analysis_id(),
        "recording_id": RECORDING_ID,
    }
    base.update(overrides)
    return AudioAnalysis(**base)  # type: ignore[arg-type]


# --- Model invariants ------------------------------------------------------


def test_a_failed_analysis_must_say_why() -> None:
    with pytest.raises(ValidationError):
        analysis(status=AudioAnalysisStatus.FAILED)


def test_a_successful_analysis_cannot_carry_a_stale_error_code() -> None:
    with pytest.raises(ValidationError):
        analysis(status=AudioAnalysisStatus.ANALYZING, error_code=ErrorCode.AUDIO_UNSUPPORTED)


def test_a_completed_analysis_must_have_metrics() -> None:
    with pytest.raises(ValidationError):
        analysis(status=AudioAnalysisStatus.COMPLETED)


def test_pitch_points_cannot_exist_without_the_metrics_describing_them() -> None:
    point = PitchPoint(
        timestamp_seconds=0.1,
        frequency_hz=440.0,
        midi_note=69,
        note_name="A4",
        cents=0.0,
        confidence=0.95,
    )
    with pytest.raises(ValidationError):
        analysis(pitch_points=(point,))


def test_the_point_count_is_derived_from_the_points() -> None:
    """It is a fact about the timeline, not a field a caller maintains."""
    record = analysis(
        status=AudioAnalysisStatus.COMPLETED, metrics=metrics(), pitch_points=_points(4)
    )

    assert record.pitch_point_count == 4


def test_a_supplied_point_count_cannot_disagree_with_the_timeline() -> None:
    """A stored document carries the count; the points remain authoritative.

    Without this, a document edited or written by an older version of the code
    could claim a length its own array does not have — and the summary reads,
    which take the count on trust because they never see the array, would repeat
    the lie.
    """
    record = AudioAnalysis.model_validate(
        {
            "audio_analysis_id": new_audio_analysis_id(),
            "recording_id": RECORDING_ID,
            "status": AudioAnalysisStatus.COMPLETED,
            "metrics": metrics().model_dump(),
            "pitch_points": [point.model_dump() for point in _points(2)],
            "pitch_point_count": 9999,
        }
    )

    assert record.pitch_point_count == 2


def test_a_document_written_before_the_count_existed_still_reports_one() -> None:
    """Nothing was migrated, so every stored analysis has to answer this."""
    stored = analysis(
        status=AudioAnalysisStatus.COMPLETED, metrics=metrics(), pitch_points=_points(3)
    ).model_dump()
    del stored["pitch_point_count"]

    assert AudioAnalysis.model_validate(stored).pitch_point_count == 3


def test_a_summary_has_no_timeline_to_read() -> None:
    """The whole safety property of the split, asserted rather than assumed.

    A summary that carried an empty ``pitch_points`` would let the note
    breakdown and the key be computed from a timeline that was simply left in
    the database — a silently wrong answer rather than a failure. The attribute
    does not exist, so that code cannot be written.
    """
    summary = AudioAnalysisSummary.model_validate(
        analysis(
            status=AudioAnalysisStatus.COMPLETED, metrics=metrics(), pitch_points=_points(5)
        ).model_dump(exclude={"pitch_points"})
    )

    assert not hasattr(summary, "pitch_points")
    assert summary.pitch_point_count == 5
    # And a full record is usable everywhere a summary is, never the reverse.
    assert isinstance(
        analysis(status=AudioAnalysisStatus.COMPLETED, metrics=metrics()), AudioAnalysisSummary
    )


def test_a_summary_keeps_every_invariant_the_record_has() -> None:
    """The rules are inherited, so a timeline-free read cannot be validated loosely."""
    with pytest.raises(ValidationError):
        AudioAnalysisSummary(
            audio_analysis_id=new_audio_analysis_id(),
            recording_id=RECORDING_ID,
            status=AudioAnalysisStatus.FAILED,
        )
    with pytest.raises(ValidationError):
        AudioAnalysisSummary(
            audio_analysis_id=new_audio_analysis_id(),
            recording_id=RECORDING_ID,
            status=AudioAnalysisStatus.COMPLETED,
        )
    with pytest.raises(ValidationError):
        # Points counted without the metrics that describe them.
        AudioAnalysisSummary(
            audio_analysis_id=new_audio_analysis_id(),
            recording_id=RECORDING_ID,
            pitch_point_count=5,
        )


def test_completed_at_belongs_only_to_a_finished_analysis() -> None:
    with pytest.raises(ValidationError):
        analysis(status=AudioAnalysisStatus.ANALYZING, completed_at=datetime.now(UTC))


def test_a_completed_analysis_is_terminal() -> None:
    record = analysis(status=AudioAnalysisStatus.COMPLETED, metrics=metrics())
    assert record.is_terminal
    assert not analysis().is_terminal


def test_a_range_cannot_be_upside_down() -> None:
    with pytest.raises(ValidationError):
        VocalRange(
            lowest_frequency_hz=440.0,
            highest_frequency_hz=220.0,
            lowest_note="A4",
            highest_note="A3",
            semitone_span=12,
        )


def test_a_section_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError):
        UnstableSection(start_seconds=2.0, end_seconds=1.0, cents_std=40.0)


def test_more_voiced_frames_than_frames_is_refused() -> None:
    with pytest.raises(ValidationError):
        PitchStability(voiced_ratio=1.0, total_frames=10, voiced_frames=11)


def test_cents_beyond_half_a_semitone_are_refused() -> None:
    """±50 is a property of the definition, so the model enforces it."""
    with pytest.raises(ValidationError):
        PitchPoint(
            timestamp_seconds=0.0,
            frequency_hz=440.0,
            midi_note=69,
            note_name="A4",
            cents=80.0,
            confidence=0.9,
        )


def test_a_note_name_cannot_be_arbitrary_text() -> None:
    with pytest.raises(ValidationError):
        PitchPoint(
            timestamp_seconds=0.0,
            frequency_hz=440.0,
            midi_note=69,
            note_name="../../etc/passwd",
            cents=0.0,
            confidence=0.9,
        )


def test_an_identifier_that_is_not_an_identifier_is_refused() -> None:
    """A value that cannot be an id cannot become a record, so cannot become a path."""
    for bad in ("../../etc/passwd", "not-hex", "", "8F14E45FCEEA167A5A36DEDD4BEA2543"):
        with pytest.raises(ValidationError):
            analysis(audio_analysis_id=bad)


def test_missing_measurements_stay_none_rather_than_becoming_zero() -> None:
    quiet = metrics(pitch=None, spectral=None)
    assert quiet.pitch is None
    assert quiet.spectral is None
    assert quiet.has_reliable_pitch is False
    assert metrics().has_reliable_pitch is True


def test_stability_with_nothing_voiced_reports_none_not_zero() -> None:
    stability = PitchStability(voiced_ratio=0.0, total_frames=100, voiced_frames=0)
    assert stability.mean_cents_deviation is None
    assert stability.cents_std is None
    assert stability.in_tune_ratio is None
    assert stability.voiced_ratio == 0.0  # this one is a genuine zero


# --- Repository ------------------------------------------------------------


@pytest.fixture
def repository(tmp_path: Path) -> JsonFileAudioAnalysisRepository:
    return JsonFileAudioAnalysisRepository(tmp_path / "storage")


def test_the_repository_satisfies_its_protocol(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    assert isinstance(repository, AudioAnalysisRepository)


def test_a_record_survives_a_round_trip(repository: JsonFileAudioAnalysisRepository) -> None:
    record = analysis(status=AudioAnalysisStatus.COMPLETED, metrics=metrics())
    repository.create(record)

    loaded = repository.get(record.audio_analysis_id)
    assert loaded is not None
    assert loaded.metrics is not None
    assert loaded.metrics.pitch is not None
    assert loaded.metrics.pitch.highest_note == "A4"
    assert loaded.status is AudioAnalysisStatus.COMPLETED


def test_a_timeline_survives_a_round_trip(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    points = tuple(
        PitchPoint(
            timestamp_seconds=index * 0.02,
            frequency_hz=440.0,
            midi_note=69,
            note_name="A4",
            cents=0.5,
            confidence=0.9,
        )
        for index in range(250)
    )
    record = analysis(status=AudioAnalysisStatus.COMPLETED, metrics=metrics(), pitch_points=points)
    repository.create(record)

    loaded = repository.get(record.audio_analysis_id)
    assert loaded is not None
    assert len(loaded.pitch_points) == 250
    assert loaded.pitch_points[10].timestamp_seconds == pytest.approx(0.2)


def test_create_refuses_to_overwrite(repository: JsonFileAudioAnalysisRepository) -> None:
    """Create and update must mean different things, or a retry destroys a result."""
    record = analysis()
    repository.create(record)
    with pytest.raises(AudioAnalysisAlreadyExistsError):
        repository.create(record)


def test_update_refuses_a_record_that_does_not_exist(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    with pytest.raises(AudioAnalysisNotFoundError):
        repository.update(analysis())


def test_an_unknown_or_malformed_id_reads_as_missing(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    assert repository.get(new_audio_analysis_id()) is None
    assert repository.get("../../etc/passwd") is None
    assert repository.get("") is None
    assert repository.delete("../../etc/passwd") is False


def test_records_list_newest_first_for_their_recording(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    now = datetime.now(UTC)
    older = analysis(created_at=now - timedelta(minutes=5))
    newer = analysis(created_at=now)
    other = analysis(recording_id="0" * 32)
    for record in (older, newer, other):
        repository.create(record)

    listed = repository.list_for_recording(RECORDING_ID)
    assert [item.audio_analysis_id for item in listed] == [
        newer.audio_analysis_id,
        older.audio_analysis_id,
    ]


def test_one_corrupt_document_does_not_hide_the_others(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    """A single bad file must not make a recording permanently unanalysable."""
    good = analysis()
    repository.create(good)
    (repository.directory / f"{'a' * 32}.json").write_text("{ not json", encoding="utf-8")

    listed = repository.list_for_recording(RECORDING_ID)
    assert [item.audio_analysis_id for item in listed] == [good.audio_analysis_id]


def test_records_are_written_owner_readable_only(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    record = analysis()
    repository.create(record)
    path = repository.directory / f"{record.audio_analysis_id}.json"
    assert path.stat().st_mode & 0o077 == 0


def test_no_temporary_file_is_left_behind(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    repository.create(analysis())
    assert [p.name for p in repository.directory.glob("audio-*")] == []


def test_delete_reports_whether_it_removed_anything(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    record = analysis()
    repository.create(record)
    assert repository.delete(record.audio_analysis_id) is True
    assert repository.delete(record.audio_analysis_id) is False


def test_audio_analyses_are_stored_apart_from_speech_analyses(
    repository: JsonFileAudioAnalysisRepository,
) -> None:
    """Different measurements, different lifecycles, different documents."""
    repository.create(analysis())
    assert repository.directory.name == "audio-analyses"
