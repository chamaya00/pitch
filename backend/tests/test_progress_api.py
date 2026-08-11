"""The progress endpoint, the window, and — above all — ownership.

Drives the real application through ``TestClient``, so the routing, the query
parameter and the response shape are exercised as a client meets them. The
service half is covered by driving ``ProgressService`` directly, which is where
the window bounds and the empty-history behaviour live.

Ownership matters here for a reason specific to this endpoint: it takes **no
recording ids at all**. The only thing deciding whose measurements come back is
the resolved owner, so if that is wrong there is nothing else to catch it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode
from app.main import create_app
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    new_audio_analysis_id,
)
from app.services.owners.models import new_owner
from app.services.progress.models import HistoryDepth, PointStatus, ProgressMetric
from app.services.progress.service import DEFAULT_WINDOW, MAX_WINDOW, ProgressService
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories
from tests.fixtures import write_wav
from tests.test_comparison import metrics

RECORDINGS_URL = "/api/v1/recordings"
PROGRESS_URL = f"{RECORDINGS_URL}/progress"
BASE = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def run(factory: Any) -> Any:
    return anyio.run(factory)


def seed(
    doubles: Doubles,
    owner_id: uuid.UUID,
    *,
    index: int = 0,
    recorded_at: datetime | None = None,
    recording_id: str | None = None,
    status: AudioAnalysisStatus | None = AudioAnalysisStatus.COMPLETED,
    error_code: ErrorCode | None = None,
    **metric_overrides: Any,
) -> str:
    """A recording at a known time, optionally with an audio analysis."""
    recording = Recording.model_validate(
        {
            "recording_id": recording_id or uuid.uuid4().hex,
            "original_filename": f"take-{index}.wav",
            "audio_format": "wav",
            "duration_seconds": 12.0,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 4096,
            "created_at": recorded_at or (BASE + timedelta(days=index)),
        }
    )
    run(lambda: doubles.recordings.create(recording, owner_id))

    if status is not None:
        payload: dict[str, Any] = {
            "audio_analysis_id": new_audio_analysis_id(),
            "recording_id": recording.recording_id,
            "status": status,
            "error_code": error_code,
        }
        if status is AudioAnalysisStatus.COMPLETED:
            payload["metrics"] = metrics(**metric_overrides).model_dump()
        run(lambda: doubles.audio_analyses.create(AudioAnalysis.model_validate(payload)))
    return recording.recording_id


def history_of(doubles: Doubles, owner_id: uuid.UUID | None = None, limit: int = 30) -> Any:
    owner = owner_id if owner_id is not None else doubles.owner.owner_id
    service = ProgressService(recordings=doubles.recordings)
    return run(lambda: service.history(owner, limit))


def series_of(history: Any, metric: ProgressMetric) -> Any:
    return next(item for item in history.series if item.metric is metric)


def stability(**overrides: Any) -> Any:
    return metrics().stability.model_copy(update=overrides)


# --- History shapes --------------------------------------------------------


def test_an_owner_with_no_recordings_gets_an_empty_history(doubles: Doubles) -> None:
    history = history_of(doubles)

    assert history.depth is HistoryDepth.EMPTY
    assert history.recordings == ()
    assert all(series.points == () for series in history.series)


def test_one_recording_is_a_single(doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id)

    assert history_of(doubles).depth is HistoryDepth.SINGLE


def test_two_recordings_are_a_pair(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1)

    assert history_of(doubles).depth is HistoryDepth.PAIR


def test_several_recordings_are_a_series(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    for index in range(4):
        seed(doubles, owner, index=index)

    history = history_of(doubles)
    assert history.depth is HistoryDepth.SERIES
    assert len(history.recordings) == 4


def test_points_come_back_oldest_first(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    for index in (2, 0, 1):  # inserted out of order on purpose
        seed(doubles, owner, index=index)

    history = history_of(doubles)
    stamps = [record.recorded_at for record in history.recordings]

    assert stamps == sorted(stamps)


def test_recordings_sharing_a_timestamp_are_ordered_by_id(doubles: Doubles) -> None:
    """Deterministic, so two takes saved in the same instant never swap places."""
    owner = doubles.owner.owner_id
    same = BASE + timedelta(days=5)
    seed(doubles, owner, recorded_at=same, recording_id="b" * 32)
    seed(doubles, owner, recorded_at=same, recording_id="a" * 32)

    ids = [record.recording_id for record in history_of(doubles).recordings]

    assert ids == ["a" * 32, "b" * 32]
    # Stable across calls, not merely stable within one.
    assert [record.recording_id for record in history_of(doubles).recordings] == ids


def test_the_window_keeps_the_most_recent_recordings(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    for index in range(5):
        seed(doubles, owner, index=index)

    history = history_of(doubles, limit=2)

    assert [record.original_filename for record in history.recordings] == [
        "take-3.wav",
        "take-4.wav",
    ]
    assert history.limit == 2


def test_the_window_is_bounded(doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id)

    assert history_of(doubles, limit=10_000).limit == MAX_WINDOW
    assert history_of(doubles, limit=0).limit == 1


# --- Eligibility -----------------------------------------------------------


def test_a_recording_with_no_analysis_appears_but_contributes_no_point(
    doubles: Doubles,
) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1, status=None)

    history = history_of(doubles)
    points = series_of(history, ProgressMetric.IN_TUNE_RATIO).points

    assert len(history.recordings) == 2, "it still belongs in its own history"
    assert points[1].status is PointStatus.NOT_ELIGIBLE
    assert points[1].value is None


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (AudioAnalysisStatus.PENDING, None),
        (AudioAnalysisStatus.ANALYZING, None),
        (AudioAnalysisStatus.FAILED, ErrorCode.AUDIO_UNSUPPORTED),
        (AudioAnalysisStatus.FAILED, ErrorCode.INSUFFICIENT_PITCH_SIGNAL),
    ],
)
def test_only_a_completed_analysis_contributes_a_point(
    doubles: Doubles, status: AudioAnalysisStatus, error_code: ErrorCode | None
) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1, status=status, error_code=error_code)

    points = series_of(history_of(doubles), ProgressMetric.IN_TUNE_RATIO).points

    assert points[1].status is PointStatus.NOT_ELIGIBLE
    assert history_of(doubles).depth is HistoryDepth.SINGLE


def test_a_null_metric_is_not_measured_rather_than_zero(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1, stability=stability(in_tune_ratio=None))

    point = series_of(history_of(doubles), ProgressMetric.IN_TUNE_RATIO).points[1]

    assert point.status is PointStatus.NOT_MEASURED
    assert point.value is None


def test_a_zero_metric_stays_zero(doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id, stability=stability(in_tune_ratio=0.0))

    point = series_of(history_of(doubles), ProgressMetric.IN_TUNE_RATIO).points[0]

    assert point.status is PointStatus.MEASURED
    assert point.value == 0.0


def test_a_missing_detected_range_leaves_the_other_series_measured(doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id, pitch=None)

    history = history_of(doubles)

    assert series_of(history, ProgressMetric.SEMITONE_SPAN).points[0].status is (
        PointStatus.NOT_MEASURED
    )
    assert series_of(history, ProgressMetric.IN_TUNE_RATIO).points[0].status is PointStatus.MEASURED
    assert history.recordings[0].lowest_note is None


# --- Ownership -------------------------------------------------------------


def other_owner(doubles: Doubles) -> uuid.UUID:
    owner, token = new_owner()
    run(lambda: doubles.owners.create(owner, token))
    return owner.owner_id


def test_another_owners_recordings_never_appear(doubles: Doubles) -> None:
    stranger = other_owner(doubles)
    for index in range(3):
        seed(doubles, stranger, index=index)

    history = history_of(doubles)

    assert history.recordings == ()
    assert history.depth is HistoryDepth.EMPTY


def test_an_empty_history_looks_the_same_whoever_owns_the_data(doubles: Doubles) -> None:
    """No signal that somebody else's recordings exist."""
    stranger = other_owner(doubles)
    seed(doubles, stranger)

    with_stranger_data = history_of(doubles)
    empty = history_of(doubles, owner_id=other_owner(doubles))

    assert with_stranger_data.model_dump() == empty.model_dump()


def test_each_owner_sees_only_their_own(doubles: Doubles) -> None:
    """Guards the guard: the isolation above must not be a broken fixture."""
    mine = doubles.owner.owner_id
    stranger = other_owner(doubles)
    seed(doubles, mine, index=0)
    seed(doubles, stranger, index=1)
    seed(doubles, stranger, index=2)

    assert len(history_of(doubles, owner_id=mine).recordings) == 1
    assert len(history_of(doubles, owner_id=stranger).recordings) == 2


# --- The endpoint ----------------------------------------------------------


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_root=tmp_path / "storage", environment="test")


@pytest.fixture
def client(app_settings: Settings, doubles: Doubles) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: app_settings
    override_repositories(app, doubles)
    return TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: doubles.token})


def test_the_progress_path_is_not_swallowed_by_the_recording_id_route(
    client: TestClient,
) -> None:
    """`/recordings/progress` must beat `/recordings/{recording_id}`."""
    response = client.get(PROGRESS_URL)

    assert response.status_code == 200
    assert response.json()["depth"] == "empty"


def test_the_endpoint_returns_every_series_with_units_and_directions(
    client: TestClient, doubles: Doubles
) -> None:
    seed(doubles, doubles.owner.owner_id)

    body = client.get(PROGRESS_URL).json()

    assert {series["metric"] for series in body["series"]} == {
        "in_tune_ratio",
        "mean_abs_cents_deviation",
        "cents_std",
        "semitone_span",
        "voiced_ratio",
        "voiced_seconds",
        "duration_seconds",
    }
    for series in body["series"]:
        assert series["unit"] in {"percentage_points", "cents", "semitones", "seconds"}
        assert series["direction"] in {
            "higher_is_nearer_the_note",
            "lower_is_nearer_the_note",
            "lower_is_steadier",
            "neutral",
        }


def test_the_endpoint_reports_the_latest_observation(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0, stability=stability(in_tune_ratio=0.5))
    seed(doubles, owner, index=1, stability=stability(in_tune_ratio=0.62))

    body = client.get(PROGRESS_URL).json()
    in_tune = next(s for s in body["series"] if s["metric"] == "in_tune_ratio")

    assert in_tune["observation"]["direction"] == "higher"
    assert in_tune["observation"]["delta"] == pytest.approx(12.0)


def test_one_recording_reports_insufficient_data_not_unchanged(
    client: TestClient, doubles: Doubles
) -> None:
    seed(doubles, doubles.owner.owner_id)

    body = client.get(PROGRESS_URL).json()

    assert body["depth"] == "single"
    for series in body["series"]:
        assert series["observation"]["direction"] == "insufficient_data"
        assert series["observation"]["delta"] is None


def test_a_null_point_is_null_in_the_response(client: TestClient, doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id, stability=stability(in_tune_ratio=None))

    body = client.get(PROGRESS_URL).json()
    point = next(s for s in body["series"] if s["metric"] == "in_tune_ratio")["points"][0]

    assert point["value"] is None
    assert point["status"] == "not_measured"


def test_the_response_respects_the_limit(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    for index in range(4):
        seed(doubles, owner, index=index)

    body = client.get(PROGRESS_URL, params={"limit": 2}).json()

    assert body["limit"] == 2
    assert len(body["recordings"]) == 2


def test_the_default_window_is_published(client: TestClient) -> None:
    assert client.get(PROGRESS_URL).json()["limit"] == DEFAULT_WINDOW


@pytest.mark.parametrize("bad", [0, -1, MAX_WINDOW + 1])
def test_an_out_of_range_limit_is_refused(client: TestClient, bad: int) -> None:
    response = client.get(PROGRESS_URL, params={"limit": bad})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE recordings; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM owners --",
        "../../etc/passwd",
    ],
)
def test_injection_attempts_are_refused_at_the_edge(client: TestClient, payload: str) -> None:
    response = client.get(PROGRESS_URL, params={"limit": payload})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_another_identity_sees_an_empty_history_through_the_endpoint(
    client: TestClient, doubles: Doubles, app_settings: Settings
) -> None:
    seed(doubles, doubles.owner.owner_id)
    stranger_owner, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger_owner, stranger_token))

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: app_settings
    override_repositories(app, doubles)
    stranger = TestClient(
        app, raise_server_exceptions=False, headers={OWNER_HEADER: stranger_token}
    )

    body = stranger.get(PROGRESS_URL).json()

    assert body["recordings"] == []
    assert body["depth"] == "empty"


def test_the_endpoint_never_leaks_internals(client: TestClient, doubles: Doubles) -> None:
    seed(doubles, doubles.owner.owner_id)

    text = client.get(PROGRESS_URL).text

    for leak in (
        "owner_id",
        "audio_analysis_id",
        "storage_root",
        "/tmp",
        "provider",
        "is_mock",
        "document",
        "clarity_threshold",
        "pitch_points",
    ):
        assert leak not in text, f"{leak} reached the progress response"


def test_the_response_contains_no_score_or_verdict(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    for index in range(3):
        seed(doubles, owner, index=index)

    text = client.get(PROGRESS_URL).text.lower()

    for word in ("score", "grade", "level", "rank", "skill", "ability", "improve", "better"):
        assert word not in text


def test_the_endpoint_is_documented_in_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/v1/recordings/progress" in schema["paths"]
    names = {
        parameter["name"]
        for parameter in schema["paths"]["/api/v1/recordings/progress"]["get"]["parameters"]
    }
    assert {"limit", OWNER_HEADER} <= names


def test_progress_works_end_to_end_from_real_uploads(
    client: TestClient, doubles: Doubles, tmp_path: Path
) -> None:
    """Genuinely uploaded and genuinely measured recordings.

    Slower than the seeded tests and worth it once: it proves the endpoint reads
    what the real analysis pipeline actually wrote.
    """
    for index, frequency in enumerate((440.0, 330.0)):
        source = write_wav(tmp_path / f"take-{index}.wav", seconds=1.0, frequency=frequency)
        upload = client.post(
            RECORDINGS_URL, files={"file": (source.name, source.read_bytes(), "audio/wav")}
        )
        assert upload.status_code == 201, upload.text
        started = client.post(f"{RECORDINGS_URL}/{upload.json()['recording_id']}/audio-analysis")
        assert started.status_code in (200, 202), started.text

    body = client.get(PROGRESS_URL).json()

    assert body["depth"] == "pair"
    assert len(body["recordings"]) == 2
    in_tune = next(s for s in body["series"] if s["metric"] == "in_tune_ratio")
    assert in_tune["measured_count"] == 2
    assert all(point["value"] is not None for point in in_tune["points"])
