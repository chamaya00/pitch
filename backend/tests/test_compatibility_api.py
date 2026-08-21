"""Song references and the compatibility endpoint, through the real application.

Two halves, following ``test_comparison_api.py``. The first drives
``CompatibilityService`` directly, to pin down every way a comparison can be
refused — each refusal names a distinct situation the reader can act on, and a
refusal is a successful response rather than an error. The second drives the
application through ``TestClient``, so routing, validation and the response
shape are exercised as a client meets them.

**The ownership tests are the ones that matter most.** This feature takes *two*
ids from the client — one in the path and one in the query — which doubles the
surface for asking about somebody else's row. Both are tested separately, and
both refusals are checked to be indistinguishable from an id that was never
real.
"""

import uuid
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.owner import OWNER_HEADER
from app.core.errors import ApiError, ErrorCode
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    new_audio_analysis_id,
)
from app.services.compatibility.models import (
    CompatibilityCaveat,
    RangeSource,
    RecordingSideStatus,
)
from app.services.compatibility.service import CompatibilityService
from app.services.owners.models import new_owner
from tests.doubles import Doubles
from tests.test_comparison import metrics
from tests.test_comparison_api import a_recording, points

REFERENCES_URL = "/api/v1/references"
RECORDINGS_URL = "/api/v1/recordings"


def run(factory: Any) -> Any:
    return anyio.run(factory)


def compatibility_url(recording_id: str, reference_id: str) -> str:
    return f"{RECORDINGS_URL}/{recording_id}/compatibility?reference_id={reference_id}"


# --- Seeding ----------------------------------------------------------------


def seed_recording(
    doubles: Doubles,
    owner_id: uuid.UUID,
    *,
    status: AudioAnalysisStatus | None = AudioAnalysisStatus.COMPLETED,
    error_code: ErrorCode | None = None,
    **metric_overrides: Any,
) -> str:
    """A recording owned by ``owner_id``. ``status=None`` means nobody measured it.

    The default metrics give a detected range of G2–C5, which is what the
    arithmetic in these tests is written against.
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
        payload["pitch_points"] = points((0.1, 69, "A4"))
    run(lambda: doubles.audio_analyses.create(AudioAnalysis.model_validate(payload)))
    return recording.recording_id


def create_reference(
    client: TestClient,
    headers: dict[str, str],
    *,
    lowest: str = "C4",
    highest: str = "C5",
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "A song",
        "artist": "Somebody",
        "lowest_note": lowest,
        "highest_note": highest,
    }
    body.update(overrides)
    response = client.post(REFERENCES_URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def service_for(doubles: Doubles) -> CompatibilityService:
    return CompatibilityService(
        recordings=doubles.recordings,
        references=doubles.references,
        analyses=doubles.audio_analyses,
    )


# --- The reference collection -----------------------------------------------


def test_a_reference_is_created_and_says_its_numbers_were_asserted(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    created = create_reference(client, owner_headers, lowest="E3", highest="A4")

    assert created["lowest_note"] == "E3"
    assert created["highest_note"] == "A4"
    assert created["source"] == "asserted"
    assert len(created["reference_id"]) == 32


def test_a_reference_may_carry_a_key_and_may_not(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    with_key = create_reference(client, owner_headers, key={"tonic": "D", "mode": "major"})
    without = create_reference(client, owner_headers)

    assert with_key["key"] == {"tonic": "D", "mode": "major"}
    assert without["key"] is None


def test_a_reference_with_a_flat_is_refused_rather_than_rewritten(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    """Handing back a different name than the caller sent is worse than saying no."""
    response = client.post(
        REFERENCES_URL,
        json={"title": "A song", "lowest_note": "Db4", "highest_note": "C5"},
        headers=owner_headers,
    )
    assert response.status_code == 422


def test_a_reference_whose_notes_are_the_wrong_way_round_is_refused(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    response = client.post(
        REFERENCES_URL,
        json={"title": "A song", "lowest_note": "C5", "highest_note": "C4"},
        headers=owner_headers,
    )
    assert response.status_code == 422


def test_a_reference_of_one_note_is_allowed(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    created = create_reference(client, owner_headers, lowest="A4", highest="A4")
    assert created["lowest_note"] == created["highest_note"] == "A4"


def test_references_are_listed_newest_first_and_only_the_callers(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    create_reference(client, owner_headers, title="First")
    create_reference(client, owner_headers, title="Second")

    listing = client.get(REFERENCES_URL, headers=owner_headers).json()

    assert listing["count"] == 2
    assert [one["title"] for one in listing["references"]] == ["Second", "First"]


def test_a_reference_can_be_read_back_and_deleted(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    created = create_reference(client, owner_headers)
    url = f"{REFERENCES_URL}/{created['reference_id']}"

    assert client.get(url, headers=owner_headers).status_code == 200
    removed = client.delete(url, headers=owner_headers)
    assert removed.status_code == 200
    # The remaining collection, so a client that just changed it needs no
    # second round trip to learn what is left.
    assert removed.json() == {"count": 0, "references": []}
    assert client.get(url, headers=owner_headers).status_code == 404


def test_deleting_a_reference_twice_reports_the_second_rather_than_pretending(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    """ "Already gone" and "removed just now" are different answers."""
    created = create_reference(client, owner_headers)
    url = f"{REFERENCES_URL}/{created['reference_id']}"

    assert client.delete(url, headers=owner_headers).status_code == 200
    second = client.delete(url, headers=owner_headers)
    assert second.status_code == 404
    assert second.json()["error_code"] == ErrorCode.REFERENCE_NOT_FOUND


def test_another_owners_reference_is_not_found_identically_to_one_that_never_existed(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    created = create_reference(client, owner_headers)
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    stranger_headers = {OWNER_HEADER: stranger_token}

    theirs = client.get(f"{REFERENCES_URL}/{created['reference_id']}", headers=stranger_headers)
    invented = client.get(f"{REFERENCES_URL}/{uuid.uuid4().hex}", headers=stranger_headers)

    assert theirs.status_code == invented.status_code == 404
    assert theirs.json()["error_code"] == invented.json()["error_code"]
    # And it is still there for the owner who created it.
    assert (
        client.get(f"{REFERENCES_URL}/{created['reference_id']}", headers=owner_headers).status_code
        == 200
    )


def test_another_owner_cannot_delete_a_reference(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    created = create_reference(client, owner_headers)
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))

    refused = client.delete(
        f"{REFERENCES_URL}/{created['reference_id']}", headers={OWNER_HEADER: stranger_token}
    )

    assert refused.status_code == 404
    assert (
        client.get(f"{REFERENCES_URL}/{created['reference_id']}", headers=owner_headers).status_code
        == 200
    )


def test_a_listing_shows_nothing_of_another_owners(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    create_reference(client, owner_headers)
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))

    listing = client.get(REFERENCES_URL, headers={OWNER_HEADER: stranger_token}).json()

    assert listing["count"] == 0
    assert listing["references"] == []


# --- Refusals, through the service ------------------------------------------


def refuse_with(doubles: Doubles, **seed_kwargs: Any) -> Any:
    owner = doubles.owner.owner_id
    recording_id = seed_recording(doubles, owner, **seed_kwargs)
    reference = run(
        lambda: doubles.references.create(_a_reference(), owner),
    )
    return run(
        lambda: service_for(doubles).compatibility(recording_id, reference.reference_id, owner)
    )


def _a_reference() -> Any:
    from app.services.compatibility.models import SongReference, new_reference_id

    return SongReference(
        reference_id=new_reference_id(),
        title="A song",
        lowest_note="C4",
        highest_note="C5",
    )


def test_a_recording_nobody_measured_is_refused_and_says_so(doubles: Doubles) -> None:
    result = refuse_with(doubles, status=None)
    assert result.comparable is False
    assert result.recording_status is RecordingSideStatus.ANALYSIS_MISSING
    assert result.fit is None and result.transposition is None


def test_a_recording_still_being_measured_is_refused_and_says_so(doubles: Doubles) -> None:
    result = refuse_with(doubles, status=AudioAnalysisStatus.ANALYZING)
    assert result.recording_status is RecordingSideStatus.ANALYSIS_IN_PROGRESS


def test_a_failed_analysis_is_refused_and_says_so(doubles: Doubles) -> None:
    result = refuse_with(
        doubles,
        status=AudioAnalysisStatus.FAILED,
        error_code=ErrorCode.AUDIO_UNSUPPORTED,
    )
    assert result.recording_status is RecordingSideStatus.ANALYSIS_FAILED


def test_a_recording_with_no_reliable_pitch_is_refused_and_says_so(doubles: Doubles) -> None:
    result = refuse_with(doubles, pitch=None)
    assert result.recording_status is RecordingSideStatus.INSUFFICIENT_PITCH_SIGNAL


def test_a_recording_that_is_not_the_callers_is_an_error_not_a_refusal(
    doubles: Doubles,
) -> None:
    """The path segment behaves like every other ``/recordings/{id}/…`` route."""
    owner = doubles.owner.owner_id
    reference = run(lambda: doubles.references.create(_a_reference(), owner))

    with pytest.raises(ApiError) as caught:
        run(
            lambda: service_for(doubles).compatibility(
                uuid.uuid4().hex, reference.reference_id, owner
            )
        )

    assert caught.value.code is ErrorCode.RECORDING_NOT_FOUND


def test_a_reference_that_is_not_the_callers_is_an_error_not_a_refusal(
    doubles: Doubles,
) -> None:
    owner = doubles.owner.owner_id
    recording_id = seed_recording(doubles, owner)

    with pytest.raises(ApiError) as caught:
        run(lambda: service_for(doubles).compatibility(recording_id, uuid.uuid4().hex, owner))

    assert caught.value.code is ErrorCode.REFERENCE_NOT_FOUND


def test_the_service_reaches_no_provider(doubles: Doubles) -> None:
    """The guarantee is the constructor, not a promise.

    A model cannot produce a compatibility number because there is no object in
    this service's graph through which one could be reached — the same technique
    the comparison and progress services rely on.
    """
    import inspect

    parameters = inspect.signature(CompatibilityService.__init__).parameters
    assert set(parameters) == {"self", "recordings", "references", "analyses"}


# --- The endpoint -----------------------------------------------------------


def test_a_song_inside_the_range_reports_a_complete_fit_and_no_shift(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers, lowest="C4", highest="C5")

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert body["comparable"] is True
    assert body["recording_status"] == "ready"
    assert body["fit"]["overlap_note_count"] == 13
    assert body["fit"]["percent_of_reference_range"] == pytest.approx(100.0)
    assert body["fit"]["semitones_above_top_note"] == 0
    assert body["transposition"]["possible"] is True
    assert body["transposition"]["semitones"] == 0


def test_a_song_above_the_range_reports_the_gap_and_the_shift_that_closes_it(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    """G2–C5 detected, against a song running C5–C6."""
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers, lowest="C5", highest="C6")

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert body["fit"]["semitones_above_top_note"] == 12
    assert body["fit"]["overlap_note_count"] == 1
    assert body["transposition"]["semitones"] == -12
    assert body["transposition"]["resulting_lowest_note"] == "C4"
    assert body["transposition"]["resulting_highest_note"] == "C5"


def test_a_song_wider_than_the_range_reports_a_shortfall_and_offers_nothing(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers, lowest="C1", highest="C7")

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert body["comparable"] is True
    assert body["transposition"]["possible"] is False
    assert body["transposition"]["shortfall_semitones"] == 43
    assert body["transposition"]["semitones"] is None
    assert body["transposition"]["resulting_lowest_note"] is None


def test_a_transposed_key_is_named_when_the_reference_carried_one(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(
        client, owner_headers, lowest="D5", highest="D6", key={"tonic": "D", "mode": "major"}
    )

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert body["transposition"]["semitones"] == -14
    assert body["transposition"]["resulting_key"] == {"tonic": "C", "mode": "major"}


def test_both_ranges_say_where_their_numbers_came_from(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers)

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert body["recording_range"]["source"] == RangeSource.MEASURED
    assert body["reference_range"]["source"] == RangeSource.ASSERTED


def test_every_response_carries_the_three_standing_caveats(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    """§10 of the specification asks for them on screen, so they are in the payload."""
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers)

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert CompatibilityCaveat.REFERENCE_RANGE_ASSERTED in body["caveats"]
    assert CompatibilityCaveat.DETECTED_RANGE_IS_THIS_RECORDING in body["caveats"]
    assert CompatibilityCaveat.NOT_A_STATEMENT_OF_ABILITY in body["caveats"]


def test_a_refusal_is_a_two_hundred(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id, status=None)
    reference = create_reference(client, owner_headers)

    response = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    )

    assert response.status_code == 200
    assert response.json()["comparable"] is False
    assert response.json()["recording_status"] == "analysis_missing"


def test_asking_about_another_owners_recording_is_a_404(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    reference = create_reference(client, owner_headers)
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    theirs = seed_recording(doubles, stranger.owner_id)

    response = client.get(
        compatibility_url(theirs, reference["reference_id"]), headers=owner_headers
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.RECORDING_NOT_FOUND


def test_asking_with_another_owners_reference_is_a_404(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))
    theirs = create_reference(client, {OWNER_HEADER: stranger_token})

    response = client.get(
        compatibility_url(recording_id, theirs["reference_id"]), headers=owner_headers
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.REFERENCE_NOT_FOUND


def test_a_malformed_reference_id_never_reaches_a_query(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)

    response = client.get(
        f"{RECORDINGS_URL}/{recording_id}/compatibility?reference_id=not-an-id",
        headers=owner_headers,
    )

    assert response.status_code == 422


def test_the_compatibility_response_has_no_field_that_could_hold_a_score(
    client: TestClient, owner_headers: dict[str, str], doubles: Doubles
) -> None:
    recording_id = seed_recording(doubles, doubles.owner.owner_id)
    reference = create_reference(client, owner_headers)

    body = client.get(
        compatibility_url(recording_id, reference["reference_id"]), headers=owner_headers
    ).json()

    assert "score" not in body
    assert "overall" not in body
    assert not any(key in body["fit"] for key in ("score", "rating", "overall"))


def test_a_reference_is_never_visible_to_another_owner_through_compatibility(
    doubles: Doubles,
) -> None:
    """Both ids checked against the same owner, never one checked and one trusted."""
    owner = doubles.owner.owner_id
    stranger, stranger_token = new_owner()
    run(lambda: doubles.owners.create(stranger, stranger_token))

    recording_id = seed_recording(doubles, owner)
    theirs = run(lambda: doubles.references.create(_a_reference(), stranger.owner_id))

    with pytest.raises(ApiError) as caught:
        run(lambda: service_for(doubles).compatibility(recording_id, theirs.reference_id, owner))

    assert caught.value.code is ErrorCode.REFERENCE_NOT_FOUND


def test_creating_a_reference_is_not_charged_against_the_costly_allowance(
    client: TestClient, owner_headers: dict[str, str]
) -> None:
    """It writes about two hundred bytes and decodes nothing.

    The costly-request allowance exists for requests that consume disk, CPU or
    provider budget. Asserted by creating more references than that allowance
    permits and finding none refused.
    """
    from app.core.config import get_settings

    allowance = get_settings().rate_limit_costly_requests
    for index in range(allowance + 2):
        response = client.post(
            REFERENCES_URL,
            json={"title": f"Song {index}", "lowest_note": "C4", "highest_note": "C5"},
            headers=owner_headers,
        )
        assert response.status_code == 201, response.text
