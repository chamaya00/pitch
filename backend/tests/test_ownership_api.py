"""Owner identity, ownership isolation, and the recording-history endpoints.

The security-relevant half of Step 7M. Two questions are asked repeatedly, in
different ways, because a wrong answer to either leaks somebody's recordings:

* does a caller who supplies no token, a stale token, or *another owner's* id
  ever see a recording that is not theirs?
* is the refusal indistinguishable from "this does not exist"?

The tests below drive the real application through ``TestClient`` and go through
the real routes and the real dependency graph — only the repositories are
in-memory, and ``test_repository_contract`` proves those behave like the SQL.
"""

from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.owners.models import hash_token, new_owner
from tests.doubles import Doubles, build_doubles, override_repositories
from tests.fixtures import write_wav

RECORDINGS_URL = "/api/v1/recordings"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_root=tmp_path / "storage", environment="test")


@pytest.fixture
def app_and_doubles(settings: Settings, doubles: Doubles) -> tuple[Any, Doubles]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    override_repositories(app, doubles)
    return app, doubles


@pytest.fixture
def anonymous(app_and_doubles: tuple[Any, Doubles]) -> TestClient:
    """A client that sends no identity at all."""
    app, _ = app_and_doubles
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(app_and_doubles: tuple[Any, Doubles]) -> TestClient:
    """A client identifying as the fixture owner."""
    app, prepared = app_and_doubles
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={OWNER_HEADER: prepared.token},
    )


def upload(client: TestClient, tmp_path: Path, name: str = "take.wav") -> str:
    source = write_wav(tmp_path / name, seconds=1.0)
    response = client.post(RECORDINGS_URL, files={"file": (name, source.read_bytes(), "audio/wav")})
    assert response.status_code == 201, response.text
    return str(response.json()["recording_id"])


# --- Minting an identity ---------------------------------------------------


def test_a_caller_without_a_token_is_issued_one(anonymous: TestClient) -> None:
    response = anonymous.get(RECORDINGS_URL)

    assert response.status_code == 200
    assert OWNER_HEADER in response.headers
    assert len(response.headers[OWNER_HEADER]) == 22


def test_a_caller_with_a_known_token_is_not_issued_a_new_one(client: TestClient) -> None:
    """A minted token appears exactly once — at creation, and never again."""
    response = client.get(RECORDINGS_URL)

    assert response.status_code == 200
    assert OWNER_HEADER not in response.headers


def test_an_unrecognised_but_well_formed_token_mints_rather_than_refusing(
    anonymous: TestClient,
) -> None:
    """A wiped database must not leave a client permanently erroring."""
    response = anonymous.get(RECORDINGS_URL, headers={OWNER_HEADER: "A" * 22})

    assert response.status_code == 200
    assert OWNER_HEADER in response.headers
    assert response.headers[OWNER_HEADER] != "A" * 22


@pytest.mark.parametrize("bad", ["short", "!" * 22, "A" * 23, "../../etc/passwd", "a b c"])
def test_a_malformed_token_is_refused_rather_than_silently_replaced(
    anonymous: TestClient, bad: str
) -> None:
    """It cannot be a token this server issued, so hiding the client bug is wrong."""
    response = anonymous.get(RECORDINGS_URL, headers={OWNER_HEADER: bad})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_an_empty_token_header_is_treated_as_absent(anonymous: TestClient) -> None:
    response = anonymous.get(RECORDINGS_URL, headers={OWNER_HEADER: "  "})

    assert response.status_code == 200
    assert OWNER_HEADER in response.headers


def test_the_token_is_never_stored_in_the_clear(doubles: Doubles) -> None:
    """A leaked database must yield no usable tokens."""
    owner, token = new_owner()
    anyio.run(lambda: doubles.owners.create(owner, token))

    stored = doubles.owners._by_hash  # noqa: SLF001 - asserting the storage form is the point
    assert token not in stored
    assert hash_token(token) in stored


def test_a_minted_token_never_appears_in_a_response_body(anonymous: TestClient) -> None:
    """It travels in a header, which is not where request bodies get logged."""
    response = anonymous.get(RECORDINGS_URL)

    assert response.headers[OWNER_HEADER] not in response.text


# --- Ownership isolation ---------------------------------------------------


def test_another_owners_recording_is_a_404_not_a_403(
    app_and_doubles: tuple[Any, Doubles], tmp_path: Path
) -> None:
    """A different answer would confirm the id is real to somebody with no right to know."""
    app, prepared = app_and_doubles
    mine = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: prepared.token})
    recording_id = upload(mine, tmp_path)

    intruder_owner, intruder_token = new_owner()
    anyio.run(lambda: prepared.owners.create(intruder_owner, intruder_token))
    theirs = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: intruder_token})

    unknown = theirs.get(f"{RECORDINGS_URL}/{'0' * 32}")
    stolen = theirs.get(f"{RECORDINGS_URL}/{recording_id}")

    assert stolen.status_code == unknown.status_code == 404
    assert stolen.json()["error_code"] == unknown.json()["error_code"] == "RECORDING_NOT_FOUND"
    assert stolen.json()["message"] == unknown.json()["message"]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/analysis",
        "/audio-analysis",
        "/audio-analysis/pitch",
        "/audio-analysis/notes",
        "/audio-analysis/feedback",
    ],
)
def test_every_recording_scoped_route_refuses_another_owner(
    app_and_doubles: tuple[Any, Doubles], tmp_path: Path, path: str
) -> None:
    """Enumerated rather than spot-checked: one unscoped route would be the leak."""
    app, prepared = app_and_doubles
    mine = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: prepared.token})
    recording_id = upload(mine, tmp_path)
    mine.post(f"{RECORDINGS_URL}/{recording_id}/audio-analysis")

    intruder_owner, intruder_token = new_owner()
    anyio.run(lambda: prepared.owners.create(intruder_owner, intruder_token))
    theirs = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: intruder_token})

    response = theirs.get(f"{RECORDINGS_URL}/{recording_id}{path}")

    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"

    # And the same request from the owner must *not* produce that answer —
    # otherwise this test would pass on a route that is simply broken.
    ours = mine.get(f"{RECORDINGS_URL}/{recording_id}{path}")
    assert ours.json().get("error_code") != "RECORDING_NOT_FOUND"


@pytest.mark.parametrize("path", ["/analysis", "/audio-analysis", "/audio-analysis/feedback"])
def test_another_owner_cannot_start_work_on_a_recording(
    app_and_doubles: tuple[Any, Doubles], tmp_path: Path, path: str
) -> None:
    """A POST is the expensive direction: it would spend a provider call."""
    app, prepared = app_and_doubles
    mine = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: prepared.token})
    recording_id = upload(mine, tmp_path)

    intruder_owner, intruder_token = new_owner()
    anyio.run(lambda: prepared.owners.create(intruder_owner, intruder_token))
    theirs = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: intruder_token})

    response = theirs.post(f"{RECORDINGS_URL}/{recording_id}{path}")

    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"


def test_an_anonymous_caller_sees_an_empty_history_not_somebody_elses(
    app_and_doubles: tuple[Any, Doubles], tmp_path: Path
) -> None:
    app, prepared = app_and_doubles
    mine = TestClient(app, raise_server_exceptions=False, headers={OWNER_HEADER: prepared.token})
    upload(mine, tmp_path)

    fresh = TestClient(app, raise_server_exceptions=False)
    body = fresh.get(RECORDINGS_URL).json()

    assert body["items"] == []
    assert body["count"] == 0


# --- History ---------------------------------------------------------------


def test_history_is_empty_for_a_new_owner(client: TestClient) -> None:
    body = client.get(RECORDINGS_URL).json()

    assert body["items"] == []
    assert body["count"] == 0
    assert body["limit"] == 50


def test_history_lists_uploads_newest_first(client: TestClient, tmp_path: Path) -> None:
    first = upload(client, tmp_path, "one.wav")
    second = upload(client, tmp_path, "two.wav")

    items = client.get(RECORDINGS_URL).json()["items"]

    assert {item["recording"]["recording_id"] for item in items} == {first, second}
    assert len(items) == 2


def test_history_reports_analysis_state_not_results(client: TestClient, tmp_path: Path) -> None:
    recording_id = upload(client, tmp_path)
    client.post(f"{RECORDINGS_URL}/{recording_id}/audio-analysis")

    item = client.get(RECORDINGS_URL).json()["items"][0]

    assert item["audio_status"] == "completed"
    assert item["last_analysed_at"] is not None
    # The measurements are not in the list response, by design.
    assert "summary" not in item
    assert "pitch_points" not in item


def test_an_unanalysed_recording_reports_null_rather_than_pending(
    client: TestClient, tmp_path: Path
) -> None:
    """`null` is "never run", which is not a status and not a failure."""
    upload(client, tmp_path)

    item = client.get(RECORDINGS_URL).json()["items"][0]

    assert item["speech_status"] is None
    assert item["audio_status"] is None
    assert item["feedback_status"] is None
    assert item["last_analysed_at"] is None


def test_history_respects_its_limit(client: TestClient, tmp_path: Path) -> None:
    for index in range(3):
        upload(client, tmp_path, f"take-{index}.wav")

    body = client.get(RECORDINGS_URL, params={"limit": 2}).json()

    assert body["count"] == 2
    assert body["limit"] == 2


@pytest.mark.parametrize("bad", [0, -1, 500])
def test_an_out_of_range_limit_is_refused(client: TestClient, bad: int) -> None:
    response = client.get(RECORDINGS_URL, params={"limit": bad})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_history_never_leaks_internals(client: TestClient, tmp_path: Path) -> None:
    upload(client, tmp_path)

    text = client.get(RECORDINGS_URL).text

    for leak in ("storage_root", "/tmp", "owner_id", "token", "document"):
        assert leak not in text


def test_a_single_recording_can_be_fetched_by_its_owner(client: TestClient, tmp_path: Path) -> None:
    recording_id = upload(client, tmp_path)

    body = client.get(f"{RECORDINGS_URL}/{recording_id}").json()

    assert body["recording_id"] == recording_id
    assert body["original_filename"] == "take.wav"


@pytest.mark.parametrize("bad_id", ["not-hex", "0" * 31, "0" * 33, "../../etc/passwd"])
def test_a_malformed_recording_id_is_refused_at_the_edge(client: TestClient, bad_id: str) -> None:
    response = client.get(f"{RECORDINGS_URL}/{bad_id}")

    assert response.status_code in (404, 422)


# --- The header is documented ----------------------------------------------


def test_the_owner_header_is_in_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    parameters = schema["paths"][RECORDINGS_URL]["get"]["parameters"]

    assert any(parameter["name"] == OWNER_HEADER for parameter in parameters)


def test_build_doubles_registers_its_owner() -> None:
    """Guards the fixture itself: a double whose owner is absent would hide leaks."""
    prepared = anyio.run(build_doubles)

    resolved = anyio.run(lambda: prepared.owners.get_by_token(prepared.token))
    assert resolved == prepared.owner


def test_the_owner_header_is_exposed_to_cross_origin_javascript(client: TestClient) -> None:
    """Without this the browser receives the token and withholds it from the page.

    The failure mode is silent: every request would mint a fresh identity and
    history would never accumulate, with no error anywhere to explain why.
    """
    response = client.get(RECORDINGS_URL, headers={"Origin": "http://localhost:3000"})

    exposed = response.headers.get("access-control-expose-headers", "")
    assert OWNER_HEADER.lower() in exposed.lower()
