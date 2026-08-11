"""Identity: what you have, and removing all of it.

Two endpoints and one guarantee each.

``GET /identity`` says what a lost key would cost, and says nothing else — no
owner id, no token, no recording content. It is the endpoint most likely to grow
a field it should not have, so what it must *not* contain is asserted as
carefully as what it must.

``DELETE /identity`` removes the rows **and the audio on disk**. Deleting only
the rows would report success while leaving every recording on the server, so
the file removal is tested through the real ``RecordingStorage`` rather than a
stub — an assertion that the files are gone is the only one that means anything
here.
"""

import uuid
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.audio.storage import RecordingStorage
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
    new_audio_analysis_id,
)
from app.services.owners.deletion import OwnerDeletionService
from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import new_owner
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories
from tests.fixtures import write_wav
from tests.test_comparison import metrics

IDENTITY_URL = "/api/v1/identity"
RECORDINGS_URL = "/api/v1/recordings"


def run(factory: Any) -> Any:
    return anyio.run(factory)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_root=tmp_path / "storage", environment="test")


@pytest.fixture
def client(settings: Settings, doubles: Doubles) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    return TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: doubles.token})


def seed(
    doubles: Doubles,
    owner_id: uuid.UUID,
    *,
    index: int = 0,
    analysed: bool = True,
    feedback: bool = False,
) -> str:
    recording = Recording.model_validate(
        {
            "recording_id": uuid.uuid4().hex,
            "original_filename": f"take-{index}.wav",
            "audio_format": "wav",
            "duration_seconds": 12.0,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 4096,
        }
    )
    run(lambda: doubles.recordings.create(recording, owner_id))

    if analysed:
        payload: dict[str, Any] = {
            "audio_analysis_id": new_audio_analysis_id(),
            "recording_id": recording.recording_id,
            "status": AudioAnalysisStatus.COMPLETED,
            "metrics": metrics().model_dump(),
        }
        if feedback:
            payload["feedback_status"] = AudioFeedbackStatus.COMPLETED
            payload["feedback"] = {
                "summary": "A stub interpretation.",
                "provenance": {"provider": "mock", "model": None, "is_mock": True},
            }
        run(lambda: doubles.audio_analyses.create(AudioAnalysis.model_validate(payload)))
    return recording.recording_id


def other_owner(doubles: Doubles) -> uuid.UUID:
    owner, token = new_owner()
    run(lambda: doubles.owners.create(owner, token))
    return owner.owner_id


# --- What you have ---------------------------------------------------------


def test_a_new_identity_has_nothing(client: TestClient) -> None:
    body = client.get(IDENTITY_URL).json()

    assert body["recordings"] == 0
    assert body["analysed_recordings"] == 0
    assert body["ai_feedback"] == 0
    assert body["anonymous"] is True


def test_the_summary_counts_what_the_identity_owns(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0, analysed=True, feedback=True)
    seed(doubles, owner, index=1, analysed=True)
    seed(doubles, owner, index=2, analysed=False)

    body = client.get(IDENTITY_URL).json()

    assert body["recordings"] == 3
    assert body["analysed_recordings"] == 2
    assert body["ai_feedback"] == 1


def test_generated_feedback_is_counted_separately(client: TestClient, doubles: Doubles) -> None:
    """It costs a provider call, so it is the part that cannot be recomputed."""
    seed(doubles, doubles.owner.owner_id, analysed=True, feedback=False)

    body = client.get(IDENTITY_URL).json()

    assert body["analysed_recordings"] == 1
    assert body["ai_feedback"] == 0


def test_the_summary_never_counts_another_owners_data(client: TestClient, doubles: Doubles) -> None:
    stranger = other_owner(doubles)
    for index in range(3):
        seed(doubles, stranger, index=index)

    body = client.get(IDENTITY_URL).json()

    assert body["recordings"] == 0


def test_each_identity_sees_only_its_own_totals(client: TestClient, doubles: Doubles) -> None:
    """Guards the guard: the isolation above must not be a broken fixture."""
    stranger = other_owner(doubles)
    seed(doubles, doubles.owner.owner_id, index=0)
    seed(doubles, stranger, index=1)
    seed(doubles, stranger, index=2)

    mine = run(lambda: doubles.owners.data_summary(doubles.owner.owner_id))
    theirs = run(lambda: doubles.owners.data_summary(stranger))

    assert mine == OwnerDataSummary(recordings=1, analysed_recordings=1, ai_feedback=0)
    assert theirs.recordings == 2


def test_the_identity_response_carries_no_owner_id_or_token(
    client: TestClient, doubles: Doubles
) -> None:
    """Two handles on the same person is one more than the client needs."""
    seed(doubles, doubles.owner.owner_id)

    text = client.get(IDENTITY_URL).text

    assert str(doubles.owner.owner_id) not in text
    assert doubles.token not in text
    for leak in ("owner_id", "token", "token_hash", "storage_root", "/tmp", "document"):
        assert leak not in text


def test_the_identity_response_carries_no_recording_content(
    client: TestClient, doubles: Doubles
) -> None:
    seed(doubles, doubles.owner.owner_id)

    body = client.get(IDENTITY_URL).json()

    assert set(body) == {
        "created_at",
        "anonymous",
        "recordings",
        "analysed_recordings",
        "ai_feedback",
        "credentials",
    }
    # The added list is a whitelist too: labels and ids, never hash or key.
    for credential in body["credentials"]:
        assert set(credential) == {"credential_id", "label", "created_at", "current"}


def test_a_caller_with_no_key_is_issued_one_and_sees_an_empty_identity(
    settings: Settings, doubles: Doubles
) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    anonymous = TestClient(app, raise_server_exceptions=False)

    response = anonymous.get(IDENTITY_URL)

    assert response.status_code == 200
    assert OWNER_HEADER in response.headers
    assert response.json()["recordings"] == 0


# --- Deleting everything ---------------------------------------------------


def test_deleting_removes_the_rows(client: TestClient, doubles: Doubles) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1)

    report = client.delete(IDENTITY_URL).json()

    assert report["recordings"] == 2
    assert run(lambda: doubles.recordings.list_for_owner(owner, 10)) == []
    assert run(lambda: doubles.owners.get(owner)) is None


def test_deleting_removes_the_stored_audio_from_disk(
    client: TestClient, tmp_path: Path, settings: Settings
) -> None:
    """The assertion that matters: rows without files is not deletion.

    Goes through the real upload endpoint and the real ``RecordingStorage``, so
    the files under test are the ones a user would actually have.
    """
    storage = RecordingStorage(settings.storage_root)
    ids = []
    for index in range(2):
        source = write_wav(tmp_path / f"take-{index}.wav", seconds=1.0)
        upload = client.post(
            RECORDINGS_URL, files={"file": (source.name, source.read_bytes(), "audio/wav")}
        )
        assert upload.status_code == 201, upload.text
        ids.append(upload.json()["recording_id"])

    assert all(storage.exists(recording_id) for recording_id in ids), "fixture precondition"

    report = client.delete(IDENTITY_URL).json()

    assert report["audio_files_deleted"] == 2
    assert report["audio_files_failed"] == 0
    assert not any(storage.exists(recording_id) for recording_id in ids)


def test_deleting_reports_a_file_it_could_not_remove(doubles: Doubles, tmp_path: Path) -> None:
    """Counted, not swallowed — and the rows still go, so nothing stays reachable."""
    owner = doubles.owner.owner_id
    seed(doubles, owner)

    class RefusingStorage:
        def delete(self, recording_id: str) -> bool:
            from app.services.audio.storage import StorageError

            raise StorageError("permission denied")

    service = OwnerDeletionService(owners=doubles.owners, storage=RefusingStorage())  # type: ignore[arg-type]
    report = run(lambda: service.delete_everything(owner))

    assert report.audio_files_failed == 1
    assert report.audio_files_deleted == 0
    assert report.owner_deleted is True
    assert run(lambda: doubles.recordings.list_for_owner(owner, 10)) == []


def test_deleting_an_empty_identity_is_harmless(client: TestClient) -> None:
    report = client.delete(IDENTITY_URL).json()

    assert report == {"recordings": 0, "audio_files_deleted": 0, "audio_files_failed": 0}


def test_repeating_the_deletion_is_safe(client: TestClient, doubles: Doubles) -> None:
    """The second call runs against a fresh identity that owns nothing."""
    seed(doubles, doubles.owner.owner_id)

    first = client.delete(IDENTITY_URL).json()
    second = client.delete(IDENTITY_URL).json()

    assert first["recordings"] == 1
    assert second["recordings"] == 0


def test_deleting_never_touches_another_owners_data(client: TestClient, doubles: Doubles) -> None:
    """The endpoint takes no identifier, so there is nothing to point elsewhere."""
    stranger = other_owner(doubles)
    seed(doubles, stranger, index=0)
    seed(doubles, stranger, index=1)
    seed(doubles, doubles.owner.owner_id, index=2)

    report = client.delete(IDENTITY_URL).json()

    assert report["recordings"] == 1
    assert len(run(lambda: doubles.recordings.list_for_owner(stranger, 10))) == 2
    assert run(lambda: doubles.owners.get(stranger)) is not None


def test_a_deleted_identity_can_no_longer_reach_its_recordings(
    client: TestClient, doubles: Doubles
) -> None:
    recording_id = seed(doubles, doubles.owner.owner_id)

    client.delete(IDENTITY_URL)

    # The same key now resolves to a *new* empty identity, so the old recording
    # is unreachable — and gone.
    assert client.get(f"{RECORDINGS_URL}/{recording_id}").status_code == 404
    assert client.get(RECORDINGS_URL).json()["items"] == []


def test_deleting_clears_the_progress_and_comparison_history(
    client: TestClient, doubles: Doubles
) -> None:
    owner = doubles.owner.owner_id
    seed(doubles, owner, index=0)
    seed(doubles, owner, index=1)

    client.delete(IDENTITY_URL)

    assert client.get(f"{RECORDINGS_URL}/progress").json()["depth"] == "empty"
    assert client.get(RECORDINGS_URL).json()["count"] == 0


# --- The endpoint contract -------------------------------------------------


def test_the_identity_routes_are_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/v1/identity" in schema["paths"]
    operations = schema["paths"]["/api/v1/identity"]
    assert {"get", "delete"} <= set(operations)
    names = {p["name"] for p in operations["get"]["parameters"]}
    assert OWNER_HEADER in names


def test_the_identity_routes_take_no_identifier(client: TestClient) -> None:
    """No parameter through which a caller could name somebody else."""
    schema = client.get("/openapi.json").json()
    operations = schema["paths"]["/api/v1/identity"]

    for method in ("get", "delete"):
        names = {p["name"] for p in operations[method].get("parameters", [])}
        assert names <= {OWNER_HEADER}, names


@pytest.mark.parametrize("bad", ["short", "!" * 22, "../../etc/passwd", "' OR 1=1 --", "a" * 23])
def test_a_malformed_key_is_refused_on_both_routes(
    settings: Settings, doubles: Doubles, bad: str
) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    client = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: bad})

    assert client.get(IDENTITY_URL).status_code == 422
    assert client.delete(IDENTITY_URL).status_code == 422


def test_no_route_returns_a_key(client: TestClient) -> None:
    """The server only stores a hash, and must not pretend otherwise."""
    schema = client.get("/openapi.json").json()
    identity_schema = schema["components"]["schemas"]["IdentityResponse"]

    for field in identity_schema["properties"]:
        assert "token" not in field
        assert "key" not in field
