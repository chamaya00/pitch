"""Comparison eligibility, the endpoint, and — above all — ownership.

Two halves. The first drives ``ComparisonService`` directly to pin down every
way a comparison can be refused, because each refusal has to name a distinct
situation the reader can act on. The second drives the real application through
``TestClient`` so the routing, the query parameters and the response shape are
exercised as a client meets them.

The ownership tests are the ones that matter most. A comparison endpoint takes
*two* recording ids from the client, which doubles the surface for asking about
somebody else's recording — so both sides are tested separately, in both
positions, and the refusal is checked to be indistinguishable from an id that
was never real.
"""

import uuid
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
from app.services.comparison.models import SideStatus
from app.services.comparison.service import ComparisonService
from app.services.owners.models import new_owner
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories
from tests.fixtures import write_wav
from tests.test_comparison import metrics

RECORDINGS_URL = "/api/v1/recordings"
COMPARE_URL = f"{RECORDINGS_URL}/compare"


def run(factory: Any) -> Any:
    return anyio.run(factory)


# --- Seeding ---------------------------------------------------------------


def a_recording(**overrides: Any) -> Recording:
    payload: dict[str, Any] = {
        "recording_id": uuid.uuid4().hex,
        "original_filename": "take.wav",
        "audio_format": "wav",
        "duration_seconds": 12.0,
        "sample_rate": 22050,
        "channels": 1,
        "size_bytes": 4096,
    }
    payload.update(overrides)
    return Recording.model_validate(payload)


def points(*specs: tuple[float, int, str]) -> list[dict[str, Any]]:
    """A minimal pitch timeline, enough for the note aggregation to work on."""
    return [
        {
            "timestamp_seconds": timestamp,
            "frequency_hz": 440.0,
            "midi_note": midi,
            "note_name": name,
            "cents": 4.0,
            "confidence": 0.95,
        }
        for timestamp, midi, name in specs
    ]


def seed(
    doubles: Doubles,
    owner_id: uuid.UUID,
    *,
    status: AudioAnalysisStatus | None = AudioAnalysisStatus.COMPLETED,
    error_code: ErrorCode | None = None,
    timeline: list[dict[str, Any]] | None = None,
    **metric_overrides: Any,
) -> str:
    """A recording owned by ``owner_id``, optionally with an audio analysis.

    ``status=None`` means the recording exists and nobody has measured it.
    """
    recording = a_recording()
    run(lambda: doubles.recordings.create(recording, owner_id))

    if status is None:
        return recording.recording_id

    payload: dict[str, Any] = {
        "audio_analysis_id": new_audio_analysis_id(),
        "recording_id": recording.recording_id,
        "status": status,
        "error_code": error_code,
    }
    if status is AudioAnalysisStatus.COMPLETED:
        payload["metrics"] = metrics(**metric_overrides).model_dump()
        payload["pitch_points"] = timeline if timeline is not None else points((0.1, 69, "A4"))
    run(lambda: doubles.audio_analyses.create(AudioAnalysis.model_validate(payload)))
    return recording.recording_id


def service_for(doubles: Doubles) -> ComparisonService:
    return ComparisonService(recordings=doubles.recordings)


def compare(doubles: Doubles, left: str, right: str, owner_id: uuid.UUID | None = None) -> Any:
    owner = owner_id if owner_id is not None else doubles.owner.owner_id
    return run(lambda: service_for(doubles).compare(left, right, owner))


# --- Eligibility -----------------------------------------------------------


def test_two_measured_recordings_compare(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, duration_seconds=14.0)

    result = compare(doubles, left, right)

    assert result.comparable is True
    assert result.left.status is SideStatus.READY
    assert result.right.status is SideStatus.READY
    assert result.metrics
    assert result.left.original_filename == "take.wav"


def test_comparing_a_recording_with_itself_is_refused(doubles: Doubles) -> None:
    """Not an outcome — a malformed request."""
    from app.core.errors import ApiError

    recording_id = seed(doubles, doubles.owner.owner_id)

    with pytest.raises(ApiError) as caught:
        compare(doubles, recording_id, recording_id)

    assert caught.value.code is ErrorCode.VALIDATION_ERROR


def test_an_unknown_left_recording_is_reported_not_raised(doubles: Doubles) -> None:
    right = seed(doubles, doubles.owner.owner_id)

    result = compare(doubles, "0" * 32, right)

    assert result.comparable is False
    assert result.left.status is SideStatus.NOT_FOUND
    assert result.right.status is SideStatus.READY
    assert result.metrics == ()
    assert result.notes == ()


def test_an_unknown_right_recording_is_reported_not_raised(doubles: Doubles) -> None:
    left = seed(doubles, doubles.owner.owner_id)

    result = compare(doubles, left, "0" * 32)

    assert result.comparable is False
    assert result.right.status is SideStatus.NOT_FOUND


def test_a_recording_nobody_has_measured_says_so(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, status=None)

    result = compare(doubles, left, right)

    assert result.right.status is SideStatus.ANALYSIS_MISSING
    assert result.right.original_filename == "take.wav", "the recording itself is still known"


def test_an_analysis_still_running_is_distinguished_from_a_missing_one(
    doubles: Doubles,
) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, status=AudioAnalysisStatus.ANALYZING)

    result = compare(doubles, left, right)

    assert result.right.status is SideStatus.ANALYSIS_IN_PROGRESS


def test_a_failed_analysis_reports_its_code(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(
        doubles,
        owner,
        status=AudioAnalysisStatus.FAILED,
        error_code=ErrorCode.AUDIO_UNSUPPORTED,
    )

    result = compare(doubles, left, right)

    assert result.right.status is SideStatus.ANALYSIS_FAILED
    assert result.right.error_code is ErrorCode.AUDIO_UNSUPPORTED


def test_insufficient_pitch_signal_is_its_own_state(doubles: Doubles) -> None:
    """A whisper is a normal outcome and must not read as a broken recording."""
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(
        doubles,
        owner,
        status=AudioAnalysisStatus.FAILED,
        error_code=ErrorCode.INSUFFICIENT_PITCH_SIGNAL,
    )

    result = compare(doubles, left, right)

    assert result.right.status is SideStatus.INSUFFICIENT_PITCH_SIGNAL
    assert result.right.status is not SideStatus.ANALYSIS_FAILED


def test_an_ineligible_comparison_carries_no_measurements(doubles: Doubles) -> None:
    """There is no half-comparison for a client to render as a whole one."""
    left = seed(doubles, doubles.owner.owner_id)

    result = compare(doubles, left, "0" * 32)

    assert result.metrics == ()
    assert result.notes == ()
    assert result.caveats == ()


# --- Ownership -------------------------------------------------------------


def other_owner(doubles: Doubles) -> uuid.UUID:
    owner, token = new_owner()
    run(lambda: doubles.owners.create(owner, token))
    return owner.owner_id


def test_another_owners_recording_cannot_be_compared_from_the_left(doubles: Doubles) -> None:
    theirs = seed(doubles, other_owner(doubles))
    mine = seed(doubles, doubles.owner.owner_id)

    result = compare(doubles, theirs, mine)

    assert result.comparable is False
    assert result.left.status is SideStatus.NOT_FOUND
    assert result.left.original_filename is None, "not even the filename may leak"


def test_another_owners_recording_cannot_be_compared_from_the_right(doubles: Doubles) -> None:
    mine = seed(doubles, doubles.owner.owner_id)
    theirs = seed(doubles, other_owner(doubles))

    result = compare(doubles, mine, theirs)

    assert result.right.status is SideStatus.NOT_FOUND
    assert result.right.original_filename is None


def test_two_of_another_owners_recordings_cannot_be_compared(doubles: Doubles) -> None:
    stranger = other_owner(doubles)
    first = seed(doubles, stranger)
    second = seed(doubles, stranger)

    result = compare(doubles, first, second)

    assert result.comparable is False
    assert result.left.status is SideStatus.NOT_FOUND
    assert result.right.status is SideStatus.NOT_FOUND


def test_an_unowned_recording_is_indistinguishable_from_a_nonexistent_one(
    doubles: Doubles,
) -> None:
    """A different answer would confirm the id is real to somebody with no right."""
    mine = seed(doubles, doubles.owner.owner_id)
    theirs = seed(doubles, other_owner(doubles))

    stolen = compare(doubles, mine, theirs)
    imaginary = compare(doubles, mine, "0" * 32)

    assert stolen.right.model_dump(exclude={"recording_id"}) == imaginary.right.model_dump(
        exclude={"recording_id"}
    )


def test_the_owner_can_compare_the_same_recordings_the_stranger_could_not(
    doubles: Doubles,
) -> None:
    """Guards the guard: the refusals above must not be a broken fixture."""
    stranger = other_owner(doubles)
    first = seed(doubles, stranger)
    second = seed(doubles, stranger)

    refused = compare(doubles, first, second)
    allowed = compare(doubles, first, second, owner_id=stranger)

    assert refused.comparable is False
    assert allowed.comparable is True


# --- Missing metrics inside an eligible comparison --------------------------


def test_a_null_metric_produces_no_delta_through_the_service(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, pitch=None)

    result = compare(doubles, left, right)
    span = next(metric for metric in result.metrics if metric.key == "semitone_span")

    assert result.comparable is True, "a missing range does not make the pair ineligible"
    assert span.right is None
    assert span.delta is None
    assert result.right.lowest_note is None
    assert result.right.highest_note is None


def test_very_different_durations_still_compare_but_carry_a_caveat(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner, duration_seconds=8.0)
    right = seed(doubles, owner, duration_seconds=240.0)

    result = compare(doubles, left, right)

    assert result.comparable is True
    assert "different_duration" in {caveat.value for caveat in result.caveats}


def test_note_breakdowns_are_aligned_through_the_service(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner, timeline=points((0.1, 60, "C4"), (0.2, 60, "C4")))
    right = seed(doubles, owner, timeline=points((0.1, 62, "D4")))

    result = compare(doubles, left, right)

    assert [row.midi_note for row in result.notes] == [60, 62]
    assert result.notes[0].presence.value == "left_only"
    assert result.notes[0].right is None


def test_the_comparison_is_deterministic_through_the_service(doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, duration_seconds=20.0)

    assert compare(doubles, left, right).model_dump() == compare(doubles, left, right).model_dump()


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


def test_the_compare_path_is_not_swallowed_by_the_recording_id_route(
    client: TestClient, doubles: Doubles
) -> None:
    """`/recordings/compare` must beat `/recordings/{recording_id}`.

    The failure mode is a 422 complaining that "compare" is not a recording id,
    which is confusing rather than obviously broken — so it is pinned here.
    """
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner)

    response = client.get(COMPARE_URL, params={"left_id": left, "right_id": right})

    assert response.status_code == 200
    assert response.json()["comparable"] is True


def test_the_endpoint_returns_deltas_with_units(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(
        doubles, owner, stability=metrics().stability.model_copy(update={"in_tune_ratio": 0.5})
    )
    right = seed(
        doubles, owner, stability=metrics().stability.model_copy(update={"in_tune_ratio": 0.62})
    )

    body = client.get(COMPARE_URL, params={"left_id": left, "right_id": right}).json()
    in_tune = next(metric for metric in body["metrics"] if metric["key"] == "in_tune_ratio")

    assert in_tune["unit"] == "percentage_points"
    assert in_tune["delta"] == pytest.approx(12.0)
    assert in_tune["direction"] == "higher_is_nearer_the_note"


def test_the_endpoint_refuses_a_malformed_id(client: TestClient) -> None:
    response = client.get(COMPARE_URL, params={"left_id": "nope", "right_id": "0" * 32})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE recordings; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM owners --",
        "../../etc/passwd",
        "%27%20OR%20%271",
    ],
)
def test_injection_attempts_are_refused_at_the_edge(client: TestClient, payload: str) -> None:
    """Nothing shaped unlike a recording id reaches a query at all."""
    response = client.get(COMPARE_URL, params={"left_id": payload, "right_id": "0" * 32})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_the_endpoint_refuses_comparing_a_recording_with_itself(
    client: TestClient, doubles: Doubles
) -> None:
    recording_id = seed(doubles, doubles.owner.owner_id)

    response = client.get(COMPARE_URL, params={"left_id": recording_id, "right_id": recording_id})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_the_endpoint_requires_both_ids(client: TestClient) -> None:
    assert client.get(COMPARE_URL, params={"left_id": "0" * 32}).status_code == 422
    assert client.get(COMPARE_URL).status_code == 422


def test_another_owner_gets_not_found_through_the_endpoint(
    client: TestClient, doubles: Doubles, app_settings: Settings
) -> None:
    mine = seed(doubles, doubles.owner.owner_id)
    theirs = seed(doubles, other_owner(doubles))

    body = client.get(COMPARE_URL, params={"left_id": mine, "right_id": theirs}).json()

    assert body["comparable"] is False
    assert body["right"]["status"] == "not_found"
    assert body["right"]["original_filename"] is None


def test_the_endpoint_never_leaks_internals(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner)

    text = client.get(COMPARE_URL, params={"left_id": left, "right_id": right}).text

    for leak in (
        "owner_id",
        "audio_analysis_id",
        "storage_root",
        "/tmp",
        "provider",
        "is_mock",
        "document",
        "settings",
        "clarity_threshold",
    ):
        assert leak not in text, f"{leak} reached the comparison response"


def test_the_response_contains_no_overall_figure(client: TestClient, doubles: Doubles) -> None:
    """There is no score in this product, and 7N does not introduce the first."""
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner)

    text = client.get(COMPARE_URL, params={"left_id": left, "right_id": right}).text.lower()

    for word in ("score", "grade", "rating", "rank", "winner", "better", "worse"):
        assert word not in text


def test_a_null_metric_is_null_in_the_response_not_zero(
    client: TestClient, doubles: Doubles
) -> None:
    owner = doubles.owner.owner_id
    left = seed(doubles, owner)
    right = seed(doubles, owner, pitch=None)

    body = client.get(COMPARE_URL, params={"left_id": left, "right_id": right}).json()
    span = next(metric for metric in body["metrics"] if metric["key"] == "semitone_span")

    assert span["right"] is None
    assert span["delta"] is None
    assert span["availability"] == "left_only"
    assert body["right"]["lowest_note"] is None


def test_the_endpoint_is_documented_in_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/v1/recordings/compare" in schema["paths"]
    parameters = schema["paths"]["/api/v1/recordings/compare"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert {"left_id", "right_id", OWNER_HEADER} <= names


def test_comparison_works_end_to_end_from_a_real_upload(
    client: TestClient, doubles: Doubles, tmp_path: Path
) -> None:
    """Two genuinely uploaded and genuinely measured recordings.

    Slower than the seeded tests and worth it once: it proves the ids a client
    actually receives are the ids the comparison endpoint accepts.
    """
    ids = []
    for index, frequency in enumerate((440.0, 330.0)):
        source = write_wav(tmp_path / f"take-{index}.wav", seconds=1.0, frequency=frequency)
        upload = client.post(
            RECORDINGS_URL,
            files={"file": (source.name, source.read_bytes(), "audio/wav")},
        )
        assert upload.status_code == 201, upload.text
        recording_id = upload.json()["recording_id"]
        started = client.post(f"{RECORDINGS_URL}/{recording_id}/audio-analysis")
        assert started.status_code in (200, 202), started.text
        ids.append(recording_id)

    body = client.get(COMPARE_URL, params={"left_id": ids[0], "right_id": ids[1]}).json()

    assert body["comparable"] is True, body
    assert body["left"]["status"] == "ready"
    assert {metric["key"] for metric in body["metrics"]} == {
        "duration_seconds",
        "voiced_seconds",
        "voiced_ratio",
        "semitone_span",
        "in_tune_ratio",
        "mean_abs_cents_deviation",
        "cents_std",
    }
    assert body["notes"], "two measured recordings should break down into notes"
