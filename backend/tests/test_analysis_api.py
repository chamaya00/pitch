"""Integration tests for the analysis endpoints.

The real FastAPI application against a real temporary filesystem: recordings are
uploaded through the real upload endpoint, analyses are persisted by the real
repository, and the deterministic mock providers from Step 7B stand in for
Deepgram and Claude. Nothing here needs credentials.

Note how ``TestClient`` behaves with background tasks: it runs them before
``client.post`` returns, so the *response body* is the evidence that the route
did not do the work inline. A body that says ``pending`` while a follow-up
``GET`` says ``completed`` is exactly the split the API promises.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_analysis_service
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.ai.mock import MockFeedbackProvider, MockSpeechToTextProvider
from app.services.analysis.models import Analysis, AnalysisStatus, new_analysis_id
from app.services.audio.storage import RecordingStorage
from app.services.orchestration.analysis import AnalysisService
from tests.doubles import Doubles, InMemoryAnalysisRepository, override_repositories
from tests.fixtures import write_wav

RECORDINGS_URL = "/api/v1/recordings"


def analysis_url(recording_id: str) -> str:
    return f"{RECORDINGS_URL}/{recording_id}/analysis"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        storage_root=tmp_path / "storage",
        environment="test",
    )


@pytest.fixture
def analyses(doubles: Doubles) -> InMemoryAnalysisRepository:
    return doubles.analyses


@pytest.fixture
def client(settings: Settings, doubles: Doubles) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-VocalLens-Owner": doubles.token},
    )


@pytest.fixture
def recording_id(client: TestClient, tmp_path: Path) -> str:
    """A real recording, uploaded through the real endpoint."""
    source = write_wav(tmp_path / "take.wav", seconds=2.0)
    response = client.post(
        RECORDINGS_URL,
        files={"file": ("take.wav", source.read_bytes(), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["recording_id"])


def seed(analyses: InMemoryAnalysisRepository, recording_id: str, **overrides: object) -> Analysis:
    """Write an analysis straight into the repository the app is using."""
    payload: dict[str, object] = {
        "analysis_id": new_analysis_id(),
        "recording_id": recording_id,
    }
    payload.update(overrides)
    return anyio.run(analyses.create, Analysis.model_validate(payload))


class SchedulingSpy:
    """A service whose ``run`` records the call instead of doing the work.

    Lets a test see exactly what the route scheduled, without the background
    task racing ahead and finishing the analysis first.
    """

    def __init__(self, settings: Settings, doubles: Doubles) -> None:
        self.scheduled: list[str] = []
        self._analyses = doubles.analyses
        self._inner = AnalysisService(
            recordings=doubles.recordings,
            storage=RecordingStorage(settings.storage_root),
            analyses=self._analyses,
            speech_to_text=MockSpeechToTextProvider(),
            feedback=MockFeedbackProvider(),
            stale_after_seconds=settings.analysis_stale_after_seconds,
        )

    async def start(self, recording_id: str, owner_id: Any) -> Any:
        return await self._inner.start(recording_id, owner_id)

    async def current(self, recording_id: str, owner_id: Any) -> Any:
        return await self._inner.current(recording_id, owner_id)

    async def run(self, analysis_id: str) -> Any:
        self.scheduled.append(analysis_id)
        return await self._analyses.get(analysis_id)


@pytest.fixture
def spy(settings: Settings, doubles: Doubles) -> SchedulingSpy:
    return SchedulingSpy(settings, doubles)


@pytest.fixture
def spy_client(settings: Settings, spy: SchedulingSpy, doubles: Doubles) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    app.dependency_overrides[get_analysis_service] = lambda: spy
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-VocalLens-Owner": doubles.token},
    )


# --- POST: starting an analysis --------------------------------------------


def test_post_accepts_the_request_and_returns_the_new_analysis(
    client: TestClient, recording_id: str
):
    response = client.post(analysis_url(recording_id))

    assert response.status_code == 202
    body = response.json()
    assert body["recording_id"] == recording_id
    assert body["status"] == "pending"
    assert len(body["analysis_id"]) == 32


def test_post_does_not_run_the_providers_inside_the_request(client: TestClient, recording_id: str):
    """The response is built before the work starts — that is the whole design."""
    body = client.post(analysis_url(recording_id)).json()

    assert body["status"] == "pending"
    assert body["transcript"] is None
    assert body["metrics"] is None
    assert body["feedback"] is None
    assert body["started_at"] is None
    assert body["completed_at"] is None


def test_post_schedules_the_run_as_a_background_task(
    spy_client: TestClient, spy: SchedulingSpy, recording_id: str
):
    body = spy_client.post(analysis_url(recording_id)).json()

    assert spy.scheduled == [body["analysis_id"]]


def test_the_scheduled_work_actually_completes_the_analysis(client: TestClient, recording_id: str):
    started = client.post(analysis_url(recording_id)).json()
    assert started["status"] == "pending"

    fetched = client.get(analysis_url(recording_id)).json()
    assert fetched["analysis_id"] == started["analysis_id"]
    assert fetched["status"] == "completed"


def test_post_persists_the_analysis(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    body = client.post(analysis_url(recording_id)).json()
    assert anyio.run(analyses.get, body["analysis_id"]) is not None


# --- POST: idempotency -----------------------------------------------------


@pytest.mark.parametrize(
    "status", [AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING]
)
def test_post_returns_an_analysis_that_is_already_in_flight(
    spy_client: TestClient,
    spy: SchedulingSpy,
    analyses: InMemoryAnalysisRepository,
    recording_id: str,
    status: AnalysisStatus,
):
    existing = seed(analyses, recording_id, status=status, started_at=datetime.now(UTC))

    response = spy_client.post(analysis_url(recording_id))

    assert response.status_code == 202
    body = response.json()
    assert body["analysis_id"] == existing.analysis_id
    assert body["status"] == status.value
    # Nothing was scheduled, so no second provider call can happen.
    assert spy.scheduled == []


def test_post_returns_an_already_completed_analysis_without_re_running_it(
    spy_client: TestClient, spy: SchedulingSpy, client: TestClient, recording_id: str
):
    first = client.post(analysis_url(recording_id)).json()
    assert client.get(analysis_url(recording_id)).json()["status"] == "completed"

    response = spy_client.post(analysis_url(recording_id))

    assert response.status_code == 200  # Nothing was accepted for processing.
    assert response.json()["analysis_id"] == first["analysis_id"]
    assert response.json()["status"] == "completed"
    assert spy.scheduled == []


def test_repeating_the_request_creates_only_one_analysis(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    ids = {client.post(analysis_url(recording_id)).json()["analysis_id"] for _ in range(3)}

    assert len(ids) == 1
    assert len(anyio.run(analyses.list_for_recording, recording_id)) == 1


def test_post_after_a_failure_starts_a_new_analysis(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    failed = seed(
        analyses,
        recording_id,
        status=AnalysisStatus.FAILED,
        error_code="ANALYSIS_PROVIDER_TIMEOUT",
        completed_at=datetime.now(UTC),
    )

    response = client.post(analysis_url(recording_id))

    assert response.status_code == 202
    assert response.json()["analysis_id"] != failed.analysis_id
    # The failed record is still there to look at.
    assert anyio.run(analyses.get, failed.analysis_id) == failed


# --- POST: failures --------------------------------------------------------


def test_post_for_an_unknown_recording_is_a_structured_404(
    client: TestClient, analyses: InMemoryAnalysisRepository
):
    unknown = new_analysis_id()
    response = client.post(analysis_url(unknown))

    assert response.status_code == 404
    assert response.json() == {
        "status": "failed",
        "error_code": "RECORDING_NOT_FOUND",
        "message": "That recording could not be found.",
    }
    assert anyio.run(analyses.list_for_recording, unknown) == []


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-hex-id", "8F14E45FCEEA167A5A36DEDD4BEA2543", "abc", "z" * 32, "8f14e45f" * 5],
)
def test_a_malformed_recording_id_is_refused_before_any_service_runs(
    client: TestClient, analyses: InMemoryAnalysisRepository, bad_id: str
):
    response = client.post(analysis_url(bad_id))

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["status"] == "failed"
    assert "detail" not in body
    assert anyio.run(analyses.list_for_recording, bad_id) == []


@pytest.mark.parametrize(
    "traversal", ["../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "%2e%2e%2f%2e%2e%2fetc"]
)
def test_a_traversal_attempt_never_reaches_the_handler(
    client: TestClient, settings: Settings, traversal: str
):
    """Nothing shaped like a path can address this route at all."""
    response = client.post(analysis_url(traversal))

    assert response.status_code in {404, 422}
    assert response.json()["error_code"] in {"NOT_FOUND", "VALIDATION_ERROR"}
    # And nothing was written anywhere outside the storage root.
    assert not (settings.storage_root.parent / "etc").exists()


# --- GET: reading an analysis ----------------------------------------------


def test_get_without_an_analysis_is_a_structured_404(client: TestClient, recording_id: str):
    response = client.get(analysis_url(recording_id))

    assert response.status_code == 404
    assert response.json() == {
        "status": "failed",
        "error_code": "ANALYSIS_NOT_FOUND",
        "message": "That recording has not been analysed yet.",
    }


def test_get_for_an_unknown_recording_says_so(client: TestClient):
    response = client.get(analysis_url(new_analysis_id()))

    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"


@pytest.mark.parametrize(
    "status", [AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING]
)
def test_get_reports_progress_while_the_analysis_runs(
    client: TestClient,
    analyses: InMemoryAnalysisRepository,
    recording_id: str,
    status: AnalysisStatus,
):
    existing = seed(analyses, recording_id, status=status, started_at=datetime.now(UTC))

    response = client.get(analysis_url(recording_id))

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] == existing.analysis_id
    assert body["recording_id"] == recording_id
    assert body["status"] == status.value
    assert body["error_code"] is None
    assert body["feedback"] is None


def test_get_returns_the_complete_result_once_finished(client: TestClient, recording_id: str):
    client.post(analysis_url(recording_id))
    response = client.get(analysis_url(recording_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
    assert body["error_code"] is None

    assert body["transcript"]["text"]
    assert len(body["transcript"]["words"]) > 0
    assert body["transcript"]["words"][0]["start_seconds"] is not None

    metrics = body["metrics"]
    assert metrics["word_count"] > 0
    assert metrics["words_per_minute"] is not None
    assert metrics["pause_count"] is not None
    assert metrics["pause_threshold_seconds"] is not None

    feedback = body["feedback"]
    assert feedback["summary"]
    assert feedback["next_action"]


def test_the_response_keeps_measurements_and_generated_prose_apart(
    client: TestClient, recording_id: str
):
    client.post(analysis_url(recording_id))
    body = client.get(analysis_url(recording_id)).json()

    # Two separate objects, each with its own provenance — never merged, and
    # with no composite score anywhere.
    assert set(body["metrics"]) & set(body["feedback"]) == set()
    assert body["provenance"]["transcription"]["provider"] == "mock"
    assert body["provenance"]["feedback"]["provider"] == "mock"
    for forbidden in ("score", "grade", "rating", "pronunciation", "accent"):
        assert forbidden not in str(body).lower()


def test_mock_provenance_is_visible_in_the_response(client: TestClient, recording_id: str):
    client.post(analysis_url(recording_id))
    body = client.get(analysis_url(recording_id)).json()

    assert body["provenance"]["is_mock"] is True
    assert body["provenance"]["transcription"]["is_mock"] is True
    assert body["provenance"]["feedback"]["is_mock"] is True


def test_a_completed_analysis_without_feedback_is_still_a_success(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    """A feedback outage leaves numbers without prose, not an error."""
    client.post(analysis_url(recording_id))
    completed = anyio.run(analyses.list_for_recording, recording_id)[0]
    anyio.run(
        lambda: analyses.update(
            Analysis.model_validate(
                {**completed.model_dump(exclude={"provenance"}), "feedback": None}
            ),
            expect_status=completed.status,
        )
    )

    body = client.get(analysis_url(recording_id)).json()

    assert body["status"] == "completed"
    assert body["feedback"] is None
    assert body["provenance"]["feedback"] is None
    assert body["metrics"]["word_count"] > 0
    assert body["error_code"] is None


@pytest.mark.parametrize(
    "error_code",
    [
        "ANALYSIS_NOT_CONFIGURED",
        "ANALYSIS_PROVIDER_UNAVAILABLE",
        "ANALYSIS_PROVIDER_TIMEOUT",
        "ANALYSIS_RATE_LIMITED",
        "ANALYSIS_PROVIDER_ERROR",
        "TRANSCRIPT_EMPTY",
    ],
)
def test_a_failed_analysis_is_a_successful_request(
    client: TestClient,
    analyses: InMemoryAnalysisRepository,
    recording_id: str,
    error_code: str,
):
    """The request worked; the analysis did not. That is a 200."""
    seed(
        analyses,
        recording_id,
        status=AnalysisStatus.FAILED,
        error_code=error_code,
        completed_at=datetime.now(UTC),
    )

    response = client.get(analysis_url(recording_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == error_code
    assert body["transcript"] is None
    assert body["feedback"] is None


def test_get_returns_the_most_recent_analysis_after_a_retry(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    seed(
        analyses,
        recording_id,
        status=AnalysisStatus.FAILED,
        error_code="ANALYSIS_PROVIDER_TIMEOUT",
        created_at=datetime.now(UTC) - timedelta(hours=1),
        completed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    retried = client.post(analysis_url(recording_id)).json()

    body = client.get(analysis_url(recording_id)).json()
    assert body["analysis_id"] == retried["analysis_id"]
    assert body["status"] == "completed"


@pytest.mark.parametrize("bad_id", ["not-a-hex-id", "z" * 32, "abc"])
def test_get_refuses_a_malformed_recording_id(client: TestClient, bad_id: str):
    response = client.get(analysis_url(bad_id))

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Nothing leaks ---------------------------------------------------------


def test_no_paths_credentials_or_audio_appear_in_a_response(
    client: TestClient, settings: Settings, recording_id: str
):
    client.post(analysis_url(recording_id))
    body = client.get(analysis_url(recording_id)).text

    assert str(settings.storage_root) not in body
    assert "/storage" not in body
    assert ".wav" not in body
    assert "RIFF" not in body
    for secret in ("api_key", "deepgram_api_key", "anthropic_api_key", "sk-ant", "Bearer "):
        assert secret not in body


def test_provenance_exposes_only_its_documented_fields(client: TestClient, recording_id: str):
    client.post(analysis_url(recording_id))
    provenance = client.get(analysis_url(recording_id)).json()["provenance"]

    assert set(provenance) == {"transcription", "feedback", "is_mock"}
    assert set(provenance["transcription"]) == {"provider", "model", "is_mock"}


def test_a_provider_failure_reaches_the_client_as_a_code_not_a_vendor_message(
    client: TestClient, analyses: InMemoryAnalysisRepository, recording_id: str
):
    seed(
        analyses,
        recording_id,
        status=AnalysisStatus.FAILED,
        error_code="ANALYSIS_PROVIDER_ERROR",
        completed_at=datetime.now(UTC),
    )

    body = client.get(analysis_url(recording_id)).text

    assert "ANALYSIS_PROVIDER_ERROR" in body
    for vendor in ("deepgram", "anthropic", "ApiError", "httpx", "Traceback"):
        assert vendor.lower() not in body.lower()


# --- OpenAPI ---------------------------------------------------------------


@pytest.fixture
def schema(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return dict(response.json())


def test_both_routes_are_documented(schema: dict[str, Any]):
    path = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"]
    assert set(path) == {"post", "get"}


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        # 429 on POST only: starting an analysis is charged against the owner's
        # costly-request allowance (Step 10.3); reading one never is.
        ("post", {"200", "202", "404", "422", "429", "500"}),
        ("get", {"200", "404", "422", "500"}),
    ],
)
def test_the_documented_responses_match_the_real_ones(
    schema: dict[str, Any], method: str, expected: set[str]
):
    operation = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"][method]
    assert set(operation["responses"]) == expected


def test_error_responses_use_the_shared_envelope(schema: dict[str, Any]):
    operation = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"]["get"]
    for status_code in ("404", "422", "500"):
        content = operation["responses"][status_code]["content"]["application/json"]
        assert content["schema"]["$ref"].endswith("/ErrorResponse")


def test_both_analysis_error_codes_are_documented_on_the_get(schema: dict[str, Any]):
    operation = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"]["get"]
    description = operation["responses"]["404"]["description"]
    assert "ANALYSIS_NOT_FOUND" in description
    assert "RECORDING_NOT_FOUND" in description


def test_the_success_schema_is_the_explicit_response_model(schema: dict[str, Any]):
    operation = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"]["get"]
    content = operation["responses"]["200"]["content"]["application/json"]
    assert content["schema"]["$ref"].endswith("/AnalysisResponse")

    properties = schema["components"]["schemas"]["AnalysisResponse"]["properties"]
    assert set(properties) == {
        "analysis_id",
        "recording_id",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "error_code",
        "transcript",
        "metrics",
        "feedback",
        "provenance",
    }


def test_the_recording_id_is_documented_as_a_constrained_identifier(schema: dict[str, Any]):
    operation = schema["paths"]["/api/v1/recordings/{recording_id}/analysis"]["get"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "recording_id")
    assert parameter["schema"]["pattern"] == r"^[0-9a-f]{32}$"
