"""Integration tests for the audio-analysis endpoints.

The real FastAPI application against a real temporary filesystem: recordings go
in through the real upload endpoint, the real decoder reads them, the real
detector measures them, and the real repository stores the result. Nothing is
mocked and nothing needs credentials — which is the point, since this pipeline
has no provider in it at all.

The full journey the specification asks for is
``test_the_whole_journey_from_upload_to_timeline``.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.doubles import Doubles, override_repositories
from tests.fixtures import (
    harmonic_samples,
    noise_samples,
    silence_samples,
    write_signal_wav,
)

RECORDINGS_URL = "/api/v1/recordings"
SAMPLE_RATE = 22050


def audio_url(recording_id: str) -> str:
    return f"{RECORDINGS_URL}/{recording_id}/audio-analysis"


def pitch_url(recording_id: str) -> str:
    return f"{audio_url(recording_id)}/pitch"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, storage_root=tmp_path / "storage", environment="test")


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


def upload(client: TestClient, path: Path) -> str:
    response = client.post(
        RECORDINGS_URL, files={"file": (path.name, path.read_bytes(), "audio/wav")}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["recording_id"])


@pytest.fixture
def recording_id(client: TestClient, tmp_path: Path) -> str:
    """A real upload of a held A4 with harmonics — a voice-shaped signal."""
    source = write_signal_wav(
        tmp_path / "take.wav",
        harmonic_samples(440.0, seconds=2.5, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    return upload(client, source)


# --- The whole journey -----------------------------------------------------


def test_the_whole_journey_from_upload_to_timeline(client: TestClient, recording_id: str) -> None:
    """Upload → analyse → persisted → GET result → GET pitch timeline."""
    started = client.post(audio_url(recording_id))
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "pending"

    result = client.get(audio_url(recording_id))
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "completed"
    assert body["error_code"] is None

    summary = body["summary"]
    assert summary["range"]["lowest_note"] == "A4"
    assert summary["range"]["highest_note"] == "A4"
    assert summary["range"]["semitone_span"] == 0
    assert summary["stability"]["voiced_ratio"] > 0.9
    assert summary["loudness"]["rms"] > 0
    assert summary["duration_seconds"] == pytest.approx(2.5, abs=0.1)

    timeline = client.get(pitch_url(recording_id))
    assert timeline.status_code == 200
    points = timeline.json()["points"]
    assert points
    assert all(point["note_name"] == "A4" for point in points)
    assert all(point["confidence"] > 0 for point in points)
    assert timeline.json()["recording_id"] == recording_id


def test_the_summary_does_not_carry_the_timeline(client: TestClient, recording_id: str) -> None:
    """Tens of thousands of points must not ride along with a range."""
    client.post(audio_url(recording_id))
    body = client.get(audio_url(recording_id)).json()

    assert "points" not in body
    assert "pitch_points" not in body
    assert body["pitch_point_count"] > 10


def test_the_response_publishes_the_settings_it_measured_with(
    client: TestClient, recording_id: str
) -> None:
    client.post(audio_url(recording_id))
    settings = client.get(audio_url(recording_id)).json()["summary"]["settings"]

    assert settings["sample_rate_hz"] == SAMPLE_RATE
    assert settings["frame_length_samples"] > settings["hop_length_samples"]
    assert 0 < settings["clarity_threshold"] <= 1


# --- Idempotency and status codes ------------------------------------------


def test_a_repeat_request_returns_the_finished_analysis_as_200(
    client: TestClient, recording_id: str
) -> None:
    first = client.post(audio_url(recording_id))
    assert first.status_code == 202

    second = client.post(audio_url(recording_id))
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert second.json()["audio_analysis_id"] == first.json()["audio_analysis_id"]


def test_the_post_returns_before_the_work_is_done(client: TestClient, recording_id: str) -> None:
    """`TestClient` runs background tasks before returning, so the *body* is the
    evidence that the route did not measure inline."""
    body = client.post(audio_url(recording_id)).json()
    assert body["status"] == "pending"
    assert body["summary"] is None
    assert client.get(audio_url(recording_id)).json()["status"] == "completed"


def test_asking_before_anything_ran_is_a_documented_404(
    client: TestClient, recording_id: str
) -> None:
    response = client.get(audio_url(recording_id))
    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_an_unknown_recording_is_a_documented_404(client: TestClient) -> None:
    response = client.post(audio_url("0" * 32))
    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"


@pytest.mark.parametrize("bad_id", ["not-hex", "../../etc/passwd", "8F14E45F"])
def test_a_malformed_id_is_refused_at_the_edge(client: TestClient, bad_id: str) -> None:
    """Nothing shaped like a path travels any further into the application."""
    response = client.get(f"{RECORDINGS_URL}/{bad_id}/audio-analysis")
    assert response.status_code in (404, 422)
    assert "passwd" not in response.text


# --- Honest failures -------------------------------------------------------


def test_silence_fails_with_insufficient_pitch_and_still_returns_200(
    client: TestClient, tmp_path: Path
) -> None:
    """A failed analysis is a successful response. It says what was missing."""
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)

    client.post(audio_url(recording_id))
    response = client.get(audio_url(recording_id))

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "INSUFFICIENT_PITCH_SIGNAL"
    assert response.json()["summary"] is None


def test_noise_fails_with_insufficient_pitch(client: TestClient, tmp_path: Path) -> None:
    source = write_signal_wav(
        tmp_path / "noise.wav",
        noise_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))
    assert client.get(audio_url(recording_id)).json()["error_code"] == "INSUFFICIENT_PITCH_SIGNAL"


def test_a_failed_analysis_has_no_timeline_to_fetch(client: TestClient, tmp_path: Path) -> None:
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))

    response = client.get(pitch_url(recording_id))
    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_a_failure_response_never_leaks_internals(client: TestClient, tmp_path: Path) -> None:
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))
    text = client.get(audio_url(recording_id)).text

    for leak in ("Traceback", "/tmp", "soundfile", "libsndfile", "numpy", str(tmp_path)):
        assert leak not in text


# --- The timeline ----------------------------------------------------------


def test_the_timeline_is_decimated_to_the_requested_size(
    client: TestClient, recording_id: str
) -> None:
    client.post(audio_url(recording_id))

    response = client.get(pitch_url(recording_id), params={"max_points": 10})
    body = response.json()

    assert body["returned_points"] <= 10
    assert body["total_points"] > body["returned_points"]
    assert body["decimation"] > 1


def test_asking_for_everything_returns_everything(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))
    body = client.get(pitch_url(recording_id), params={"max_points": 50000}).json()

    assert body["decimation"] == 1
    assert body["returned_points"] == body["total_points"]


def test_the_timeline_holds_only_voiced_frames(client: TestClient, tmp_path: Path) -> None:
    """The documented choice: unvoiced frames are omitted, never nulled."""
    samples = (
        harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
        + silence_samples(seconds=1.0, sample_rate=SAMPLE_RATE)
        + harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
    )
    recording_id = upload(
        client, write_signal_wav(tmp_path / "gap.wav", samples, sample_rate=SAMPLE_RATE)
    )
    client.post(audio_url(recording_id))

    body = client.get(pitch_url(recording_id), params={"max_points": 50000}).json()
    assert all(point["frequency_hz"] > 0 for point in body["points"])
    assert all(point["note_name"] for point in body["points"])

    times = [point["timestamp_seconds"] for point in body["points"]]
    assert max(b - a for a, b in zip(times, times[1:], strict=False)) > 0.5

    voiced_ratio = client.get(audio_url(recording_id)).json()["summary"]["stability"][
        "voiced_ratio"
    ]
    assert 0.4 < voiced_ratio < 0.85


@pytest.mark.parametrize("bad", [0, -1, 999999])
def test_an_out_of_range_max_points_is_refused(
    client: TestClient, recording_id: str, bad: int
) -> None:
    client.post(audio_url(recording_id))
    response = client.get(pitch_url(recording_id), params={"max_points": bad})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Independence from the speech pipeline ---------------------------------


def test_audio_analysis_needs_no_ai_provider(
    client: TestClient, settings: Settings, recording_id: str
) -> None:
    """A deployment with no credentials at all can still measure audio."""
    assert settings.deepgram_api_key is None
    assert settings.anthropic_api_key is None

    client.post(audio_url(recording_id))
    assert client.get(audio_url(recording_id)).json()["status"] == "completed"


def test_the_two_analyses_are_separate_resources(client: TestClient, recording_id: str) -> None:
    """Different paths, different records, different lifecycles, no shared score."""
    client.post(audio_url(recording_id))
    client.post(f"{RECORDINGS_URL}/{recording_id}/analysis")

    audio = client.get(audio_url(recording_id)).json()
    speech = client.get(f"{RECORDINGS_URL}/{recording_id}/analysis").json()

    assert audio["audio_analysis_id"] != speech["analysis_id"]
    assert "transcript" not in audio
    assert "metrics" not in audio  # the audio result calls its own block "summary"
    assert "summary" not in speech
    # Nothing anywhere combines them into one figure.
    for body in (audio, speech):
        for forbidden in ("score", "grade", "overall", "rating"):
            assert forbidden not in str(body).lower()


def test_a_failed_audio_analysis_does_not_affect_speech_analysis(
    client: TestClient, tmp_path: Path
) -> None:
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)

    client.post(audio_url(recording_id))
    assert client.get(audio_url(recording_id)).json()["status"] == "failed"

    client.post(f"{RECORDINGS_URL}/{recording_id}/analysis")
    assert client.get(f"{RECORDINGS_URL}/{recording_id}/analysis").json()["status"] == "completed"


# --- Note breakdown --------------------------------------------------------


def notes_url(recording_id: str) -> str:
    return f"{audio_url(recording_id)}/notes"


def test_a_held_note_breaks_down_to_that_one_note(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))
    response = client.get(notes_url(recording_id))

    assert response.status_code == 200
    body = response.json()
    assert [note["note_name"] for note in body["notes"]] == ["A4"]
    assert body["notes"][0]["percentage_of_voiced_time"] == pytest.approx(100.0)
    assert body["notes"][0]["in_tune_ratio"] == pytest.approx(1.0)
    assert body["in_tune_cents"] == 25.0


def test_a_two_note_recording_breaks_down_into_two_notes(
    client: TestClient, tmp_path: Path
) -> None:
    """The deterministic fixture the whole audio pipeline is checked against."""
    samples = harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE) + harmonic_samples(
        261.626, seconds=1.0, sample_rate=SAMPLE_RATE
    )
    recording_id = upload(
        client, write_signal_wav(tmp_path / "two.wav", samples, sample_rate=SAMPLE_RATE)
    )
    client.post(audio_url(recording_id))

    body = client.get(notes_url(recording_id)).json()
    names = [note["note_name"] for note in body["notes"]]

    assert names[:2] == ["A4", "C4"], f"got {names}"
    # Twice as long on A4 as on C4, within a frame or two either side.
    a4, c4 = body["notes"][0], body["notes"][1]
    assert a4["duration_seconds"] == pytest.approx(2 * c4["duration_seconds"], rel=0.1)
    assert a4["percentage_of_voiced_time"] > c4["percentage_of_voiced_time"]


def test_the_breakdown_covers_the_voiced_time_and_nothing_else(
    client: TestClient, recording_id: str
) -> None:
    client.post(audio_url(recording_id))
    breakdown = client.get(notes_url(recording_id)).json()
    summary = client.get(audio_url(recording_id)).json()["summary"]

    assert breakdown["total_frames"] == summary["stability"]["voiced_frames"]
    assert sum(note["percentage_of_voiced_time"] for note in breakdown["notes"]) == pytest.approx(
        100.0
    )
    # Voiced time is a share of the recording, not the whole of it.
    assert breakdown["voiced_seconds"] <= summary["duration_seconds"] + 0.1


def test_the_breakdown_does_not_redefine_the_range(client: TestClient, tmp_path: Path) -> None:
    """Aggregating by note must leave the authoritative range untouched."""
    samples = harmonic_samples(440.0, seconds=1.5, sample_rate=SAMPLE_RATE) + harmonic_samples(
        220.0, seconds=1.5, sample_rate=SAMPLE_RATE
    )
    recording_id = upload(
        client, write_signal_wav(tmp_path / "range.wav", samples, sample_rate=SAMPLE_RATE)
    )
    client.post(audio_url(recording_id))

    summary = client.get(audio_url(recording_id)).json()["summary"]
    assert summary["range"]["lowest_note"] == "A3"
    assert summary["range"]["highest_note"] == "A4"
    assert summary["range"]["semitone_span"] == 12


def test_the_notes_are_ordered_longest_first(client: TestClient, tmp_path: Path) -> None:
    samples = (
        harmonic_samples(440.0, seconds=0.6, sample_rate=SAMPLE_RATE)
        + harmonic_samples(329.628, seconds=1.8, sample_rate=SAMPLE_RATE)
        + harmonic_samples(261.626, seconds=1.2, sample_rate=SAMPLE_RATE)
    )
    recording_id = upload(
        client, write_signal_wav(tmp_path / "three.wav", samples, sample_rate=SAMPLE_RATE)
    )
    client.post(audio_url(recording_id))

    notes = client.get(notes_url(recording_id)).json()["notes"]
    durations = [note["duration_seconds"] for note in notes]
    assert durations == sorted(durations, reverse=True)
    assert notes[0]["note_name"] == "E4"


def test_silence_has_no_breakdown_to_fetch(client: TestClient, tmp_path: Path) -> None:
    """A failed analysis has nothing to break down — and says so with a code."""
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))

    response = client.get(notes_url(recording_id))
    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_asking_for_a_breakdown_before_the_analysis_ran_is_a_documented_404(
    client: TestClient, recording_id: str
) -> None:
    response = client.get(notes_url(recording_id))
    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_an_unknown_recording_has_no_breakdown(client: TestClient) -> None:
    response = client.get(notes_url("0" * 32))
    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"


def test_the_breakdown_never_leaks_internals(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))
    text = client.get(notes_url(recording_id)).text
    for leak in ("Traceback", "/tmp", "soundfile", "numpy"):
        assert leak not in text


def test_the_breakdown_is_a_short_response_whatever_the_timeline_length(
    client: TestClient, tmp_path: Path
) -> None:
    """The aggregation happens server-side; a client never downloads the points."""
    source = write_signal_wav(
        tmp_path / "long.wav",
        harmonic_samples(440.0, seconds=8.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))

    breakdown = client.get(notes_url(recording_id)).json()
    assert breakdown["total_frames"] > 300
    assert len(breakdown["notes"]) <= 3


# --- Audio feedback --------------------------------------------------------


def feedback_url(recording_id: str) -> str:
    return f"{audio_url(recording_id)}/feedback"


def test_feedback_is_generated_only_when_asked(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))

    before = client.get(feedback_url(recording_id))
    assert before.status_code == 200
    assert before.json()["status"] == "not_requested"
    assert before.json()["feedback"] is None

    started = client.post(feedback_url(recording_id))
    assert started.status_code == 202
    assert client.get(feedback_url(recording_id)).json()["status"] == "completed"


def test_completed_feedback_renders_every_section_it_produced(
    client: TestClient, recording_id: str
) -> None:
    client.post(audio_url(recording_id))
    client.post(feedback_url(recording_id))

    feedback = client.get(feedback_url(recording_id)).json()["feedback"]
    assert feedback["summary"]
    assert feedback["pitch_observations"]
    assert feedback["range_observations"]
    assert feedback["provenance"]["is_mock"] is True


def test_feedback_never_states_a_measurement_it_was_not_given(
    client: TestClient, recording_id: str
) -> None:
    """The mock is the executable version of the prompt's first rule."""
    client.post(audio_url(recording_id))
    client.post(feedback_url(recording_id))

    body = client.get(feedback_url(recording_id)).json()
    text = str(body["feedback"]).lower()

    for forbidden in ("score", "grade", "rating", "singing ability"):
        assert forbidden not in text
    for label in ("bright", "dark", "breathy", "nasal", "healthy"):
        assert label not in text


def test_repeating_the_request_returns_the_existing_feedback(
    client: TestClient, recording_id: str
) -> None:
    client.post(audio_url(recording_id))
    first = client.post(feedback_url(recording_id))
    second = client.post(feedback_url(recording_id))

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "completed"


def test_silence_is_refused_rather_than_interpreted(client: TestClient, tmp_path: Path) -> None:
    """A recording with no reliable pitch must not produce a vocal assessment."""
    source = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))

    response = client.post(feedback_url(recording_id))
    assert response.status_code == 422
    assert response.json()["error_code"] == "INSUFFICIENT_PITCH_SIGNAL"
    assert "not enough reliable pitch" in response.json()["message"]


def test_an_unanalysed_recording_is_refused(client: TestClient, recording_id: str) -> None:
    response = client.post(feedback_url(recording_id))
    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_feedback_does_not_disturb_the_measurements(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))
    before = client.get(audio_url(recording_id)).json()
    client.post(feedback_url(recording_id))
    after = client.get(audio_url(recording_id)).json()

    assert before["summary"] == after["summary"]
    assert after["status"] == "completed"
    assert client.get(notes_url(recording_id)).json()["notes"]


def test_the_feedback_response_never_leaks_internals(client: TestClient, recording_id: str) -> None:
    client.post(audio_url(recording_id))
    client.post(feedback_url(recording_id))
    text = client.get(feedback_url(recording_id)).text

    for leak in ("Traceback", "/tmp", "api_key", "sk-ant", "anthropic", "system", "prompt"):
        assert leak not in text


# --- Musical key (Phase 8, Slice 3) ----------------------------------------
#
# The estimator is tested exhaustively in ``test_audio_key.py`` and the service
# seam in ``test_audio_analysis_orchestration.py``. What is tested here is the
# HTTP contract: the two absences stay apart, the evidence is published, and the
# response carries nothing it should not.


def key_url(recording_id: str) -> str:
    return f"{audio_url(recording_id)}/key"


def melody_wav(path: Path, *, semitones: int = 0, seconds_per_note: float = 0.6) -> Path:
    """A sung major phrase, optionally transposed: tonic and dominant carried.

    Real audio through the real decoder and the real detector, so this exercises
    the whole path rather than a constructed timeline. ``semitones`` shifts the
    whole phrase, which is what makes a hard-coded tonic anywhere in the route or
    the schema fail rather than pass.
    """
    #  C4    E4     G4     C4    F4     G4     A4     B4     G4     C4
    phrase = [
        261.626,
        329.628,
        391.995,
        261.626,
        349.228,
        391.995,
        440.0,
        493.883,
        391.995,
        261.626,
    ]
    shift = 2.0 ** (semitones / 12.0)
    samples: list[float] = []
    for frequency in phrase:
        samples += harmonic_samples(
            frequency * shift, seconds=seconds_per_note, sample_rate=SAMPLE_RATE
        )
    return write_signal_wav(path, samples, sample_rate=SAMPLE_RATE)


@pytest.mark.parametrize(("semitones", "tonic"), [(0, "C"), (5, "F"), (7, "G"), (1, "C#")])
def test_a_sung_phrase_reports_the_key_it_fits(
    client: TestClient, tmp_path: Path, semitones: int, tonic: str
) -> None:
    """The same phrase in four keys.

    One key would not be enough: mutation showed that a tonic hard-coded in the
    schema's ``from_domain`` passes a single-key test, because the fixture and
    the fabrication agree. Transposing is what makes the mapping load-bearing.
    """
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav", semitones=semitones))
    client.post(audio_url(recording_id))

    response = client.get(key_url(recording_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] is not None
    assert body["key"]["tonic"] == tonic
    assert body["key"]["mode"] == "major"
    assert body["unmeasured_reason"] is None
    assert body["recording_id"] == recording_id


def test_the_evidence_behind_the_key_is_published(client: TestClient, tmp_path: Path) -> None:
    """A label with no evidence is a verdict. The twelve shares are the evidence."""
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    body = client.get(key_url(recording_id)).json()

    assert [share["pitch_class"] for share in body["pitch_classes"]] == list(range(12))
    assert sum(share["percentage_of_voiced_time"] for share in body["pitch_classes"]) == (
        pytest.approx(100.0)
    )
    assert body["distinct_pitch_classes"] >= 5
    assert body["voiced_seconds"] > 0
    assert body["method"]


def test_the_runner_up_is_returned_so_a_close_call_is_visible(
    client: TestClient, tmp_path: Path
) -> None:
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    key = client.get(key_url(recording_id)).json()["key"]

    assert key["alternative"] is not None
    assert (key["alternative"]["tonic"], key["alternative"]["mode"]) != (key["tonic"], key["mode"])


def test_a_held_note_analyses_successfully_and_establishes_no_key(
    client: TestClient, recording_id: str
) -> None:
    """The distinction this endpoint exists to keep.

    A held A4 analyses perfectly well — range, stability and loudness are all
    measured. It is simply one pitch class, which names no key. That is a `200`
    with a null key and a reason, never a `404` and never an error.
    """
    client.post(audio_url(recording_id))

    response = client.get(key_url(recording_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] is None
    assert body["unmeasured_reason"] == "TOO_FEW_PITCH_CLASSES"
    assert len(body["pitch_classes"]) == 12
    assert body["distinct_pitch_classes"] == 1


def test_a_recording_that_was_never_analysed_has_no_key_resource(
    client: TestClient, recording_id: str
) -> None:
    response = client.get(key_url(recording_id))

    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_a_recording_whose_analysis_found_no_pitch_has_no_key_resource(
    client: TestClient, tmp_path: Path
) -> None:
    """A *failed* analysis is not a null key — there is nothing to look at."""
    source = write_signal_wav(
        tmp_path / "noise.wav",
        noise_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    recording_id = upload(client, source)
    client.post(audio_url(recording_id))

    assert client.get(audio_url(recording_id)).json()["status"] == "failed"
    response = client.get(key_url(recording_id))

    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIO_ANALYSIS_NOT_FOUND"


def test_an_unknown_recording_has_no_key_resource(client: TestClient) -> None:
    response = client.get(key_url("0" * 32))

    assert response.status_code == 404
    assert response.json()["error_code"] == "RECORDING_NOT_FOUND"


def test_a_malformed_recording_id_never_reaches_the_service(client: TestClient) -> None:
    """Refused at the edge by the path constraint, like every sibling route."""
    for bad in ("../../etc/passwd", "not-hex", "0" * 31, "0" * 33):
        response = client.get(f"{RECORDINGS_URL}/{bad}/audio-analysis/key")
        assert response.status_code in (404, 422), (bad, response.status_code)


def test_the_key_response_carries_no_internal_identifiers(
    client: TestClient, tmp_path: Path
) -> None:
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    text = client.get(key_url(recording_id)).text

    for forbidden in ("owner_id", "owner", "token", "credential", "hash", "password"):
        assert forbidden not in text.lower(), forbidden


def test_asking_for_a_key_twice_returns_the_same_answer(client: TestClient, tmp_path: Path) -> None:
    """A derived measurement, so repeating the request cannot change it."""
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    first = client.get(key_url(recording_id)).json()
    second = client.get(key_url(recording_id)).json()

    assert first == second


def test_reading_a_key_is_never_rate_limited(client: TestClient, tmp_path: Path) -> None:
    """Reading is not charged, exactly as the note breakdown and timeline are not."""
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    statuses = {client.get(key_url(recording_id)).status_code for _ in range(30)}

    assert statuses == {200}


def test_the_key_endpoint_is_published_in_the_schema(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/recordings/{recording_id}/audio-analysis/key" in paths


def test_reading_a_key_loads_the_analysis_once_and_writes_nothing(
    client: TestClient, doubles: Doubles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint's cost, counted rather than reasoned about.

    The analysis document carries the pitch timeline — up to 12 931 points of
    JSONB at the accepted maximum — and loading it dominates this request by an
    order of magnitude over the ~1.4 ms of arithmetic that folds it. The route
    needs the record twice over: once for the ids in the response, once for the
    timeline to fold. It must get both from one load.

    Counting is also what keeps the two halves of the response consistent. Two
    loads can straddle a re-analysis and pair one record's ids with another
    record's key; one cannot.
    """
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    loads = 0
    original = doubles.audio_analyses.latest_for_recording

    async def counted(rid: str):  # type: ignore[no-untyped-def]
        nonlocal loads
        loads += 1
        return await original(rid)

    monkeypatch.setattr(doubles.audio_analyses, "latest_for_recording", counted)
    before = {
        analysis_id: analysis.model_dump()
        for analysis_id, analysis in doubles.audio_analyses._records.items()
    }

    assert client.get(key_url(recording_id)).status_code == 200

    assert loads == 1
    after = {
        analysis_id: analysis.model_dump()
        for analysis_id, analysis in doubles.audio_analyses._records.items()
    }
    assert after == before


def test_the_key_names_the_analysis_it_was_folded_from(client: TestClient, tmp_path: Path) -> None:
    """``audio_analysis_id`` is the record the timeline came from, not a spare id.

    It is what makes the key reproducible: a reader can fetch that analysis's
    pitch timeline and fold it themselves. The note breakdown, which is derived
    from the same timeline on the same terms, must name the same record.
    """
    recording_id = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording_id))

    key = client.get(key_url(recording_id)).json()
    summary = client.get(audio_url(recording_id)).json()
    notes = client.get(f"{audio_url(recording_id)}/notes").json()

    assert key["audio_analysis_id"] == summary["audio_analysis_id"]
    assert key["audio_analysis_id"] == notes["audio_analysis_id"]
    assert key["recording_id"] == recording_id


# --- What a read actually costs --------------------------------------------
#
# Every endpoint below resolves the recording's current analysis. In PostgreSQL
# that is one stored document, and the pitch timeline inside it is almost all of
# it: 1 596 kB against 1.2 kB for everything else at the longest recording this
# product accepts, which measured 87 ms and 19 MB of peak allocation per read
# versus 1.7 ms and 14 kB without the points. These tests count the reads that
# carry a timeline, so a route that starts paying for one it does not return
# fails here rather than in production.


def _count_timeline_loads(doubles: Doubles, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count reads that fetch a whole analysis document, timeline included."""
    loads = [0]
    latest = doubles.audio_analyses.latest_for_recording
    by_id = doubles.audio_analyses.get

    async def counted_latest(recording_id: str):  # type: ignore[no-untyped-def]
        loads[0] += 1
        return await latest(recording_id)

    async def counted_get(audio_analysis_id: str):  # type: ignore[no-untyped-def]
        loads[0] += 1
        return await by_id(audio_analysis_id)

    monkeypatch.setattr(doubles.audio_analyses, "latest_for_recording", counted_latest)
    monkeypatch.setattr(doubles.audio_analyses, "get", counted_get)
    return loads


def test_the_summary_endpoints_never_load_a_pitch_timeline(
    client: TestClient, doubles: Doubles, recording_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None of these returns a pitch point, so none of them may load one.

    The first is the read the browser **polls** while a measurement runs, which
    is what makes it the most expensive habit in the product rather than one
    slow request.
    """
    client.post(audio_url(recording_id))
    loads = _count_timeline_loads(doubles, monkeypatch)

    assert client.get(audio_url(recording_id)).status_code == 200
    assert client.get(feedback_url(recording_id)).status_code == 200
    # A repeated start, which walks every analysis this recording has ever had.
    assert client.post(audio_url(recording_id)).status_code == 200

    assert loads == [0]


def test_reading_the_feedback_state_loads_no_timeline(
    client: TestClient, doubles: Doubles, recording_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Polling for prose must not re-read the measurements it describes.

    The *claim* is timeline-free too, but a ``POST`` here also runs the
    background generation inside the test client's request cycle, and that run
    legitimately loads the points to build its note breakdown. The claim's own
    cost is asserted against the service in
    ``test_audio_analysis_orchestration.py``, where the two are separable.
    """
    client.post(audio_url(recording_id))
    client.post(feedback_url(recording_id))
    loads = _count_timeline_loads(doubles, monkeypatch)

    assert client.get(feedback_url(recording_id)).status_code == 200

    assert loads == [0]


def test_reading_the_note_breakdown_loads_the_analysis_once(
    client: TestClient, doubles: Doubles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property Phase 8 slice 5 established for the key.

    This route resolved the record for the ids in its response and then called
    `service.notes`, which resolved it again — two loads of the document whose
    timeline is the whole cost of the request. Two loads can also straddle a
    re-analysis and pair one record's ids with another record's notes.
    """
    recording = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording))
    loads = _count_timeline_loads(doubles, monkeypatch)

    assert client.get(notes_url(recording)).status_code == 200

    assert loads == [1]


def test_the_pitch_graph_reads_a_sample_and_never_the_whole_timeline(
    client: TestClient, doubles: Doubles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read this route makes must be the size of its response, not of the store.

    ``max_points`` has capped this response since Step 7I, and the route used to
    honour it by materialising every stored point and slicing the list. At the
    longest recording this product accepts that is 12 931 points built to return
    995 — 1 685 kB across the socket and ~50 ms of parsing and validation, on the
    event loop, where it stalls every other request in the process. Step 10.14
    measured that: while one client held a dashboard open, the poll the browser
    makes while a measurement runs went from 14.2 ms to 301.0 ms.

    So the sample is selected in the store, and this asserts the route asks for
    one. Counting is the only way to see it — a graph drawn from a sliced
    timeline and a graph drawn from a sampled read return the same bytes.
    """
    recording = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording))

    whole_loads = _count_timeline_loads(doubles, monkeypatch)
    sampled_loads = [0]
    decimated = doubles.audio_analyses.latest_decimated_for_recording

    async def counted(recording_id: str, *, max_points: int):  # type: ignore[no-untyped-def]
        sampled_loads[0] += 1
        return await decimated(recording_id, max_points=max_points)

    monkeypatch.setattr(doubles.audio_analyses, "latest_decimated_for_recording", counted)

    assert client.get(pitch_url(recording), params={"max_points": 10}).status_code == 200

    assert whole_loads == [0]
    assert sampled_loads == [1]


def test_the_graph_is_the_same_frames_it_always_was(client: TestClient, tmp_path: Path) -> None:
    """Where the decimation happens must not change which points come back.

    The store selects the sample now instead of Python slicing a list. That is a
    performance change and nothing else: the response must be point for point
    what asking for every point and taking every n-th of them gives.
    """
    recording = upload(client, melody_wav(tmp_path / "phrase.wav"))
    client.post(audio_url(recording))

    everything = client.get(pitch_url(recording), params={"max_points": 50000}).json()
    sampled = client.get(pitch_url(recording), params={"max_points": 5}).json()

    stride = sampled["decimation"]
    assert stride > 1, "the fixture must be long enough to be worth decimating"
    assert sampled["total_points"] == everything["total_points"]
    assert sampled["points"] == everything["points"][::stride]


def test_the_summary_still_says_how_many_points_were_measured(
    client: TestClient, recording_id: str
) -> None:
    """Dropping the timeline from a read must not drop the fact that it exists.

    A summary that reported no points would be indistinguishable from an
    analysis that measured nothing, which is the failure a stripped document
    invites and `pitch_point_count` prevents.
    """
    client.post(audio_url(recording_id))

    summary = client.get(audio_url(recording_id)).json()
    timeline = client.get(f"{pitch_url(recording_id)}?max_points=50000").json()

    assert summary["pitch_point_count"] > 10
    assert summary["pitch_point_count"] == timeline["total_points"]
    assert timeline["returned_points"] == summary["pitch_point_count"]
