"""Credentials: more than one way in to the identity you already have.

The suite exists to hold one line, and everything here is an attempt to break
it: **a credential resolves an identity; it never changes which owner owns
anything.** So the tests do not merely check that a new key works — they check
that the recordings, the analyses, the history, the comparison, the progress and
the identity summary reached through the new key are the *same* ones, belonging
to the *same* owner id, as before it existed.

The second theme is what must never come back. A key is returned exactly once,
at creation. No listing, no error, no log line and no other response may contain
a key or a hash of one, and the tests assert on whole response bodies rather
than on the fields somebody remembered to check.
"""

import logging
import uuid
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
    new_audio_analysis_id,
)
from app.services.owners.credentials import MAX_LABEL_LENGTH, clean_label, new_credential
from app.services.owners.models import new_owner, new_owner_token
from app.services.recordings.models import Recording
from tests.doubles import Doubles, override_repositories
from tests.test_comparison import metrics

IDENTITY_URL = "/api/v1/identity"
CREDENTIALS_URL = "/api/v1/identity/credentials"
RECORDINGS_URL = "/api/v1/recordings"
PROGRESS_URL = "/api/v1/recordings/progress"
COMPARE_URL = "/api/v1/recordings/compare"


def run(factory: Any) -> Any:
    return anyio.run(factory)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_root=tmp_path / "storage", environment="test")


@pytest.fixture
def app_for(settings: Settings, doubles: Doubles) -> Any:
    def build() -> Any:
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        override_repositories(app, doubles)
        return app

    return build


@pytest.fixture
def client(app_for: Any, doubles: Doubles) -> TestClient:
    return TestClient(
        app_for(), raise_server_exceptions=False, headers={OWNER_HEADER: doubles.token}
    )


def client_with(app_for: Any, key: str | None) -> TestClient:
    headers = {} if key is None else {OWNER_HEADER: key}
    return TestClient(app_for(), raise_server_exceptions=False, headers=headers)


def seed(doubles: Doubles, owner_id: uuid.UUID, *, index: int = 0) -> str:
    """One recording with a completed analysis, so every read has something to find."""
    recording = Recording.model_validate(
        {
            "recording_id": uuid.uuid4().hex,
            "original_filename": f"take-{index}.wav",
            "audio_format": "wav",
            "duration_seconds": 12.0 + index,
            "sample_rate": 22050,
            "channels": 1,
            "size_bytes": 4096,
        }
    )
    run(lambda: doubles.recordings.create(recording, owner_id))
    run(
        lambda: doubles.audio_analyses.create(
            AudioAnalysis.model_validate(
                {
                    "audio_analysis_id": new_audio_analysis_id(),
                    "recording_id": recording.recording_id,
                    "status": AudioAnalysisStatus.COMPLETED,
                    "metrics": metrics().model_dump(),
                    "feedback_status": AudioFeedbackStatus.COMPLETED,
                    "feedback": {
                        "summary": "A stub interpretation.",
                        "provenance": {"provider": "mock", "model": None, "is_mock": True},
                    },
                }
            )
        )
    )
    return recording.recording_id


def add_key(client: TestClient, label: str | None = "Phone") -> dict[str, Any]:
    response = client.post(CREDENTIALS_URL, json={"label": label})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- Adding a way in -------------------------------------------------------


def test_a_new_key_is_returned_once_and_resolves_to_the_same_owner(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    """The success criterion for the whole step, in one test."""
    seed(doubles, doubles.owner.owner_id)
    before = client.get(IDENTITY_URL).json()

    created = add_key(client, "Laptop")

    second = client_with(app_for, created["key"])
    after = second.get(IDENTITY_URL).json()
    assert after["created_at"] == before["created_at"]
    assert after["recordings"] == before["recordings"] == 1
    assert after["ai_feedback"] == before["ai_feedback"] == 1


def test_the_new_key_sees_the_same_recordings(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    recording_id = seed(doubles, doubles.owner.owner_id)

    second = client_with(app_for, add_key(client)["key"])

    listed = second.get(RECORDINGS_URL).json()
    assert [entry["recording"]["recording_id"] for entry in listed["items"]] == [recording_id]
    assert second.get(f"{RECORDINGS_URL}/{recording_id}").status_code == 200


def test_the_new_key_reaches_every_owner_scoped_read(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    """Nothing in the product is reachable by only one of an owner's keys."""
    first = seed(doubles, doubles.owner.owner_id, index=0)
    second_recording = seed(doubles, doubles.owner.owner_id, index=1)

    second = client_with(app_for, add_key(client)["key"])

    assert second.get(f"{RECORDINGS_URL}/{first}/audio-analysis").status_code == 200
    assert second.get(f"{RECORDINGS_URL}/{first}/audio-analysis/feedback").status_code == 200
    assert second.get(PROGRESS_URL).status_code == 200
    compared = second.get(COMPARE_URL, params={"left_id": first, "right_id": second_recording})
    assert compared.status_code == 200


def test_adding_a_key_creates_no_second_identity(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    """No new owner row, and no recording changes hands."""
    seed(doubles, doubles.owner.owner_id)
    owners_before = set(doubles.owners._by_id)  # noqa: SLF001 - counting rows is the point

    created = add_key(client)

    assert set(doubles.owners._by_id) == owners_before  # noqa: SLF001
    assert run(lambda: doubles.credentials.owner_for_key(created["key"])) == doubles.owner.owner_id
    assert all(owner == doubles.owner.owner_id for owner, _ in doubles.recordings.records())


def test_the_response_to_a_second_request_never_carries_the_key(
    client: TestClient, app_for: Any
) -> None:
    """Shown once. There is no listing, error or summary that can show it again."""
    created = add_key(client)
    key = created["key"]

    assert key not in client.get(IDENTITY_URL).text
    assert key not in client_with(app_for, key).get(IDENTITY_URL).text
    assert key not in client.get(f"{RECORDINGS_URL}/{uuid.uuid4().hex}").text


def test_a_created_key_is_never_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        created = add_key(client)

    assert created["key"] not in caplog.text


def test_a_blank_label_gets_a_default(client: TestClient) -> None:
    assert client.post(CREDENTIALS_URL, json={"label": "   "}).json()["label"] == "New key"


def test_a_missing_body_is_accepted(client: TestClient) -> None:
    response = client.post(CREDENTIALS_URL)

    assert response.status_code == 201
    assert response.json()["label"] == "New key"


def test_an_overlong_label_is_refused(client: TestClient) -> None:
    response = client.post(CREDENTIALS_URL, json={"label": "x" * (MAX_LABEL_LENGTH + 1)})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "New key"), ("", "New key"), ("  Phone  ", "Phone"), ("x" * 200, "x" * 60)],
)
def test_label_cleaning(given: str | None, expected: str) -> None:
    assert clean_label(given) == expected


# --- Listing ---------------------------------------------------------------


def test_the_identity_lists_every_way_in_oldest_first(client: TestClient) -> None:
    add_key(client, "Phone")
    add_key(client, "Laptop")

    listed = client.get(IDENTITY_URL).json()["credentials"]

    assert [entry["label"] for entry in listed] == ["Original key", "Phone", "Laptop"]


def test_the_listing_marks_the_key_in_use(client: TestClient, app_for: Any) -> None:
    created = add_key(client, "Phone")

    from_second = client_with(app_for, created["key"]).get(IDENTITY_URL).json()["credentials"]

    current = [entry["label"] for entry in from_second if entry["current"]]
    assert current == ["Phone"]
    from_first = client.get(IDENTITY_URL).json()["credentials"]
    assert [entry["label"] for entry in from_first if entry["current"]] == ["Original key"]


def test_the_listing_never_carries_a_hash(client: TestClient, doubles: Doubles) -> None:
    add_key(client)
    stored = list(doubles.owners.credentials._by_hash)  # noqa: SLF001 - the hashes are the point

    text = client.get(IDENTITY_URL).text

    assert stored
    for credential_hash in stored:
        assert credential_hash not in text


def test_a_listing_never_shows_another_owners_key(client: TestClient, doubles: Doubles) -> None:
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    credential, key = new_credential(stranger.owner_id, "Theirs")
    run(lambda: doubles.credentials.create(credential, key))

    listed = client.get(IDENTITY_URL).json()["credentials"]

    assert "Theirs" not in [entry["label"] for entry in listed]
    assert str(credential.credential_id) not in client.get(IDENTITY_URL).text


# --- Revoking --------------------------------------------------------------


def test_revoking_a_key_stops_it_working(client: TestClient, app_for: Any) -> None:
    created = add_key(client)

    remaining = client.delete(f"{CREDENTIALS_URL}/{created['credential_id']}")

    assert remaining.status_code == 200
    assert [entry["label"] for entry in remaining.json()] == ["Original key"]
    # The revoked key is now unrecognised, which mints a fresh empty identity
    # rather than failing — the same answer an unknown key has always got.
    orphan = client_with(app_for, created["key"]).get(IDENTITY_URL).json()
    assert orphan["recordings"] == 0


def test_revoking_a_key_changes_no_ownership(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    recording_id = seed(doubles, doubles.owner.owner_id)
    created = add_key(client)

    client.delete(f"{CREDENTIALS_URL}/{created['credential_id']}")

    assert client.get(f"{RECORDINGS_URL}/{recording_id}").status_code == 200
    assert client.get(IDENTITY_URL).json()["recordings"] == 1


def test_the_last_key_cannot_be_revoked(client: TestClient, doubles: Doubles) -> None:
    """Revoking it would leave owned recordings nobody could ever reach."""
    seed(doubles, doubles.owner.owner_id)
    only = client.get(IDENTITY_URL).json()["credentials"][0]

    response = client.delete(f"{CREDENTIALS_URL}/{only['credential_id']}")

    assert response.status_code == 409
    assert response.json()["error_code"] == "LAST_CREDENTIAL"
    assert client.get(IDENTITY_URL).json()["recordings"] == 1
    assert client.get(RECORDINGS_URL).status_code == 200


def test_revoking_the_key_you_are_holding_is_allowed_when_others_remain(
    client: TestClient, app_for: Any
) -> None:
    created = add_key(client, "Phone")
    holder = client_with(app_for, created["key"])
    current = [c for c in holder.get(IDENTITY_URL).json()["credentials"] if c["current"]][0]

    response = holder.delete(f"{CREDENTIALS_URL}/{current['credential_id']}")

    assert response.status_code == 200
    assert [entry["label"] for entry in response.json()] == ["Original key"]
    assert all(entry["current"] is False for entry in response.json())


def test_another_owners_key_cannot_be_revoked(client: TestClient, doubles: Doubles) -> None:
    """And is reported as absent, so this cannot be used to probe for real ids."""
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    credential, key = new_credential(stranger.owner_id, "Theirs")
    run(lambda: doubles.credentials.create(credential, key))

    response = client.delete(f"{CREDENTIALS_URL}/{credential.credential_id}")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CREDENTIAL_NOT_FOUND"
    assert run(lambda: doubles.credentials.owner_for_key(key)) == stranger.owner_id


def test_another_owners_only_key_is_reported_absent_not_refused(
    client: TestClient, doubles: Doubles
) -> None:
    """A different status would say "that identity is down to one key"."""
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    only = run(lambda: doubles.credentials.list_for_owner(stranger.owner_id))[0]

    response = client.delete(f"{CREDENTIALS_URL}/{only.credential_id}")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


def test_an_unknown_credential_id_is_not_found(client: TestClient) -> None:
    add_key(client)

    response = client.delete(f"{CREDENTIALS_URL}/{uuid.uuid4()}")

    assert response.status_code == 404


def test_a_malformed_credential_id_is_refused(client: TestClient) -> None:
    response = client.delete(f"{CREDENTIALS_URL}/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Failing safely --------------------------------------------------------


@pytest.mark.parametrize("bad", ["short", "!" * 22, "x" * 23, "with space here 12345x"])
def test_a_malformed_key_is_refused_rather_than_minting(app_for: Any, bad: str) -> None:
    """It cannot be a key this server issued, so treating it as absent would
    hide a client bug behind a silently changing identity."""
    response = client_with(app_for, bad).post(CREDENTIALS_URL, json={"label": "Phone"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_a_refusal_never_echoes_the_key(app_for: Any) -> None:
    bad = "!" * 22

    response = client_with(app_for, bad).get(IDENTITY_URL)

    assert bad not in response.text


def test_an_unrecognised_key_reveals_nothing_about_who_exists(
    client: TestClient, app_for: Any, doubles: Doubles
) -> None:
    """Unknown and absent get the same answer: a fresh, empty identity."""
    seed(doubles, doubles.owner.owner_id)

    unknown = client_with(app_for, new_owner_token()).get(IDENTITY_URL)
    absent = client_with(app_for, None).get(IDENTITY_URL)

    assert unknown.status_code == absent.status_code == 200
    assert unknown.json()["recordings"] == absent.json()["recordings"] == 0
    assert len(unknown.json()["credentials"]) == len(absent.json()["credentials"]) == 1


def test_a_fresh_identity_starts_with_exactly_one_way_in(app_for: Any) -> None:
    body = client_with(app_for, None).get(IDENTITY_URL).json()

    assert [entry["label"] for entry in body["credentials"]] == ["Original key"]


def test_no_response_carries_an_owner_id(client: TestClient, doubles: Doubles) -> None:
    created = add_key(client)
    owner_id = str(doubles.owner.owner_id)

    assert owner_id not in client.get(IDENTITY_URL).text
    assert owner_id not in client.post(CREDENTIALS_URL, json={"label": "Another"}).text
    assert owner_id not in client.delete(f"{CREDENTIALS_URL}/{created['credential_id']}").text


def test_a_credential_cannot_be_attached_to_a_named_owner(client: TestClient) -> None:
    """There is no field for it; a client that invents one is ignored, not obeyed."""
    stranger = uuid.uuid4()

    response = client.post(
        CREDENTIALS_URL, json={"label": "Phone", "owner_id": str(stranger), "current": True}
    )

    assert response.status_code == 201
    assert str(stranger) not in response.text
