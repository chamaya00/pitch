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
def client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


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
