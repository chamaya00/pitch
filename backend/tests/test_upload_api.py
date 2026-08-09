"""Integration tests for POST /api/v1/recordings.

These drive the real FastAPI app against a real temporary filesystem. Mocks are
used only to provoke a storage failure, which cannot be triggered reliably
otherwise.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.recordings.repository import JsonFileRecordingRepository
from tests.fixtures import (
    ELF_BYTES,
    FLAC_SIGNATURE,
    M4A_SIGNATURE,
    SHELL_SCRIPT_BYTES,
    deterministic_bytes,
    mp3_bytes,
    write_mp3,
    write_wav,
    write_wav_of_exact_size,
)

UPLOAD_URL = "/api/v1/recordings"
ONE_MB = 1024 * 1024


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Small limits keep boundary tests fast without changing any logic."""
    return Settings(
        _env_file=None,
        storage_root=tmp_path / "storage",
        max_audio_size_mb=1,
        max_audio_duration_seconds=300,
        environment="test",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


def upload(client: TestClient, name: str, content: bytes, content_type: str = "audio/wav"):
    return client.post(UPLOAD_URL, files={"file": (name, content, content_type)})


def stored_files(settings: Settings) -> list[Path]:
    directory = settings.storage_root / "recordings"
    return sorted(directory.iterdir()) if directory.exists() else []


def temp_files(settings: Settings) -> list[Path]:
    directory = settings.storage_root / "tmp"
    return sorted(directory.iterdir()) if directory.exists() else []


def assert_nothing_stored(settings: Settings) -> None:
    assert stored_files(settings) == []
    assert temp_files(settings) == []


def wav_bytes(tmp_path: Path, **kwargs: Any) -> bytes:
    return write_wav(tmp_path / "src.wav", **kwargs).read_bytes()


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_valid_wav_upload_returns_201_with_metadata(
    client: TestClient, tmp_path: Path, settings: Settings
):
    content = wav_bytes(tmp_path, seconds=1.5, sample_rate=8000, channels=1)

    response = upload(client, "practice take.wav", content)

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "practice take.wav"
    assert body["format"] == "wav"
    assert body["duration_seconds"] == pytest.approx(1.5, abs=0.01)
    assert body["sample_rate"] == 8000
    assert body["channels"] == 1
    assert body["size_bytes"] == len(content)
    assert body["bits_per_sample"] == 16
    assert len(stored_files(settings)) == 1


def test_valid_mp3_upload_returns_201_with_metadata(
    client: TestClient, tmp_path: Path, settings: Settings
):
    content = write_mp3(tmp_path / "src.mp3", frames=46).read_bytes()

    response = upload(client, "song.mp3", content, content_type="audio/mpeg")

    assert response.status_code == 201
    body = response.json()
    assert body["format"] == "mp3"
    assert body["sample_rate"] == 44100
    assert body["duration_seconds"] > 0
    assert body["bits_per_sample"] is None
    assert len(stored_files(settings)) == 1


def test_response_matches_the_documented_schema(client: TestClient, tmp_path: Path):
    response = upload(client, "take.wav", wav_bytes(tmp_path))

    body = response.json()
    assert set(body) == {
        "recording_id",
        "original_filename",
        "format",
        "duration_seconds",
        "sample_rate",
        "channels",
        "size_bytes",
        "bits_per_sample",
        "created_at",
    }
    assert isinstance(body["recording_id"], str)
    assert len(body["recording_id"]) == 32


def test_stored_bytes_match_what_was_uploaded(
    client: TestClient, tmp_path: Path, settings: Settings
):
    content = wav_bytes(tmp_path, seconds=2.0)

    body = upload(client, "take.wav", content).json()

    stored = stored_files(settings)
    assert len(stored) == 1
    assert stored[0].name == f"{body['recording_id']}.wav"
    assert stored[0].read_bytes() == content


def test_metadata_record_is_persisted(client: TestClient, tmp_path: Path, settings: Settings):
    body = upload(client, "take.wav", wav_bytes(tmp_path)).json()

    repository = JsonFileRecordingRepository(settings.storage_root)
    recording = repository.get(body["recording_id"])

    assert recording is not None
    assert recording.original_filename == "take.wav"
    assert recording.audio_format.value == "wav"


def test_two_uploads_get_distinct_ids(client: TestClient, tmp_path: Path, settings: Settings):
    content = wav_bytes(tmp_path)

    first = upload(client, "take.wav", content).json()
    second = upload(client, "take.wav", content).json()

    assert first["recording_id"] != second["recording_id"]
    assert len(stored_files(settings)) == 2


def test_no_temporary_file_survives_a_successful_upload(
    client: TestClient, tmp_path: Path, settings: Settings
):
    upload(client, "take.wav", wav_bytes(tmp_path))
    assert temp_files(settings) == []


# --------------------------------------------------------------------------
# Filename handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("録音.wav", "録音.wav"),
        ("тест.wav", "тест.wav"),
        ("café take.wav", "café take.wav"),
        ("تسجيل.wav", "تسجيل.wav"),
    ],
)
def test_unicode_filenames_are_preserved_for_display(
    client: TestClient, tmp_path: Path, sent: str, expected: str
):
    response = upload(client, sent, wav_bytes(tmp_path))

    assert response.status_code == 201
    assert response.json()["original_filename"] == expected


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("../../etc/passwd.wav", "passwd.wav"),
        ("..\\..\\take.wav", "take.wav"),
        ("/absolute/take.wav", "take.wav"),
        (".hidden.wav", "hidden.wav"),
    ],
)
def test_path_traversal_filenames_are_reduced_to_a_display_name(
    client: TestClient, tmp_path: Path, settings: Settings, sent: str, expected: str
):
    response = upload(client, sent, wav_bytes(tmp_path))

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == expected
    stored = stored_files(settings)
    assert stored[0].name == f"{body['recording_id']}.wav"


def test_traversal_filename_cannot_write_outside_storage(
    client: TestClient, tmp_path: Path, settings: Settings
):
    sentinel = tmp_path / "storage" / "recordings"
    upload(client, "../../../../escaped.wav", wav_bytes(tmp_path))

    assert len(list(sentinel.iterdir())) == 1
    assert not (tmp_path / "escaped.wav").exists()


@pytest.mark.parametrize("sent", ["", "   ", "...", "/", "‮"])
def test_unusable_filenames_are_rejected(
    client: TestClient, tmp_path: Path, settings: Settings, sent: str
):
    response = upload(client, sent, wav_bytes(tmp_path))

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_FILENAME"
    assert_nothing_stored(settings)


@pytest.mark.parametrize("name", ["take.m4a", "take.exe", "take.flac", "take", "song.wav.exe"])
def test_unsupported_extensions_are_rejected(
    client: TestClient, tmp_path: Path, settings: Settings, name: str
):
    response = upload(client, name, wav_bytes(tmp_path))

    assert response.status_code == 415
    assert response.json()["error_code"] == "UNSUPPORTED_FORMAT"
    assert_nothing_stored(settings)


@pytest.mark.parametrize("name", ["TAKE.WAV", "Take.Wav"])
def test_uppercase_extensions_are_accepted(client: TestClient, tmp_path: Path, name: str):
    response = upload(client, name, wav_bytes(tmp_path))

    assert response.status_code == 201
    assert response.json()["original_filename"] == name


def test_a_missing_file_field_is_a_validation_error(client: TestClient, settings: Settings):
    response = client.post(UPLOAD_URL, data={"not_a_file": "x"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert_nothing_stored(settings)


# --------------------------------------------------------------------------
# Size limit
# --------------------------------------------------------------------------


def test_a_file_just_below_the_limit_is_accepted(
    client: TestClient, tmp_path: Path, settings: Settings
):
    content = write_wav_of_exact_size(tmp_path / "src.wav", ONE_MB - 1024).read_bytes()

    response = upload(client, "take.wav", content)

    assert response.status_code == 201
    assert response.json()["size_bytes"] == len(content)


def test_a_file_exactly_at_the_limit_is_accepted(
    client: TestClient, tmp_path: Path, settings: Settings
):
    content = write_wav_of_exact_size(tmp_path / "src.wav", ONE_MB).read_bytes()
    assert len(content) == settings.max_audio_size_bytes

    response = upload(client, "take.wav", content)

    assert response.status_code == 201
    assert response.json()["size_bytes"] == ONE_MB


def test_a_file_over_the_limit_is_rejected(client: TestClient, tmp_path: Path, settings: Settings):
    content = write_wav_of_exact_size(tmp_path / "src.wav", ONE_MB + 2).read_bytes()

    response = upload(client, "take.wav", content)

    assert response.status_code == 413
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    assert_nothing_stored(settings)


def test_a_dishonest_content_length_does_not_admit_an_oversized_body(
    client: TestClient, tmp_path: Path, settings: Settings
):
    """A client understating its size must still be caught by the running count."""
    oversized = write_wav_of_exact_size(tmp_path / "src.wav", ONE_MB * 2).read_bytes()
    body, headers = _multipart_body("take.wav", oversized)
    headers["content-length"] = "128"

    response = client.post(UPLOAD_URL, content=body, headers=headers)

    assert response.status_code in {400, 413}
    assert_nothing_stored(settings)


def test_a_streamed_body_without_content_length_is_still_capped(
    client: TestClient, tmp_path: Path, settings: Settings
):
    """Chunked uploads have no Content-Length at all; the counter still applies."""
    oversized = write_wav_of_exact_size(tmp_path / "src.wav", ONE_MB * 2).read_bytes()
    body, headers = _multipart_body("take.wav", oversized)
    headers.pop("content-length", None)

    def chunks():
        for index in range(0, len(body), 64 * 1024):
            yield body[index : index + 64 * 1024]

    response = client.post(UPLOAD_URL, content=chunks(), headers=headers)

    assert response.status_code == 413
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    assert_nothing_stored(settings)


def _multipart_body(filename: str, content: bytes) -> tuple[bytes, dict[str, str]]:
    boundary = "----vocallenstestboundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    headers = {
        "content-type": f"multipart/form-data; boundary={boundary}",
        "content-length": str(len(body)),
    }
    return body, headers


# --------------------------------------------------------------------------
# Content validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["take.wav", "song.mp3"])
def test_arbitrary_bytes_are_rejected(client: TestClient, settings: Settings, name: str):
    response = upload(client, name, deterministic_bytes(4096))

    assert response.status_code == 422
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"
    assert_nothing_stored(settings)


@pytest.mark.parametrize("content", [ELF_BYTES + deterministic_bytes(2048), SHELL_SCRIPT_BYTES])
def test_executable_content_is_rejected(client: TestClient, settings: Settings, content: bytes):
    response = upload(client, "take.wav", content)

    assert response.status_code == 422
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"
    assert_nothing_stored(settings)


def test_a_truncated_mp3_is_rejected(client: TestClient, settings: Settings):
    response = upload(client, "song.mp3", mp3_bytes(frames=46)[:40], "audio/mpeg")

    assert response.status_code == 422
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"
    assert_nothing_stored(settings)


def test_an_empty_file_is_rejected(client: TestClient, settings: Settings):
    response = upload(client, "take.wav", b"")

    assert response.status_code == 422
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"
    assert_nothing_stored(settings)


@pytest.mark.parametrize(
    ("content", "expected_label"), [(FLAC_SIGNATURE, "FLAC"), (M4A_SIGNATURE, "M4A")]
)
def test_recognisable_unsupported_containers_are_named(
    client: TestClient, settings: Settings, content: bytes, expected_label: str
):
    response = upload(client, "take.wav", content)

    assert response.status_code == 415
    body = response.json()
    assert body["error_code"] == "UNSUPPORTED_FORMAT"
    assert expected_label in body["message"]
    assert_nothing_stored(settings)


# --------------------------------------------------------------------------
# Extension / content agreement
# --------------------------------------------------------------------------


def test_wav_content_named_mp3_is_rejected(client: TestClient, tmp_path: Path, settings: Settings):
    response = upload(client, "song.mp3", wav_bytes(tmp_path), "audio/mpeg")

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "FORMAT_MISMATCH"
    assert "WAV" in body["message"]
    assert_nothing_stored(settings)


def test_mp3_content_named_wav_is_rejected(client: TestClient, settings: Settings):
    response = upload(client, "take.wav", mp3_bytes(frames=30))

    assert response.status_code == 400
    assert response.json()["error_code"] == "FORMAT_MISMATCH"
    assert_nothing_stored(settings)


def test_a_mismatched_file_is_not_silently_renamed(
    client: TestClient, tmp_path: Path, settings: Settings
):
    upload(client, "song.mp3", wav_bytes(tmp_path), "audio/mpeg")
    assert stored_files(settings) == []


# --------------------------------------------------------------------------
# Content-Type is advisory
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["audio/wav", "application/octet-stream", "text/plain", "application/x-msdownload"],
)
def test_a_valid_file_is_accepted_whatever_content_type_claims(
    client: TestClient, tmp_path: Path, content_type: str
):
    response = upload(client, "take.wav", wav_bytes(tmp_path), content_type)
    assert response.status_code == 201


def test_a_correct_content_type_cannot_rescue_an_invalid_file(
    client: TestClient, settings: Settings
):
    response = upload(client, "take.wav", deterministic_bytes(2048), "audio/wav")

    assert response.status_code == 422
    assert response.json()["error_code"] == "CORRUPTED_AUDIO"
    assert_nothing_stored(settings)


# --------------------------------------------------------------------------
# Duration limit
# --------------------------------------------------------------------------


def test_a_recording_within_the_duration_limit_is_accepted(client: TestClient, tmp_path: Path):
    response = upload(client, "take.wav", wav_bytes(tmp_path, seconds=2.0))
    assert response.status_code == 201


def test_a_recording_over_the_duration_limit_is_rejected(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "storage",
        max_audio_size_mb=50,
        max_audio_duration_seconds=1,
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, raise_server_exceptions=False)
    content = write_wav(tmp_path / "long.wav", seconds=3.0).read_bytes()

    response = upload(client, "take.wav", content)

    assert response.status_code == 422
    assert response.json()["error_code"] == "AUDIO_TOO_LONG"
    assert_nothing_stored(settings)


def test_duration_comes_from_the_audio_not_the_filename(tmp_path: Path):
    """A short file named to suggest otherwise is judged on its contents."""
    settings = Settings(
        _env_file=None, storage_root=tmp_path / "storage", max_audio_duration_seconds=2
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, raise_server_exceptions=False)

    content = write_wav(tmp_path / "s.wav", seconds=1.0).read_bytes()
    response = upload(client, "ten-hour-epic.wav", content)

    assert response.status_code == 201


# --------------------------------------------------------------------------
# Storage failure and rollback
# --------------------------------------------------------------------------


def test_storage_failure_returns_a_safe_internal_error(
    client: TestClient, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    from app.services.audio import storage as storage_module

    def failing_save(*args: object, **kwargs: object) -> None:
        raise storage_module.StorageWriteError("disk exploded at /srv/secret/path")

    monkeypatch.setattr(storage_module.RecordingStorage, "save", failing_save)

    response = upload(client, "take.wav", wav_bytes(tmp_path))

    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "/srv/secret/path" not in body["message"]
    assert "StorageWriteError" not in body["message"]
    assert_nothing_stored(settings)


def test_a_failed_metadata_record_rolls_back_the_stored_bytes(
    client: TestClient, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    """Bytes are stored before the record is written; a failure must undo them."""
    from app.services.recordings import repository as repository_module

    def failing_create(*args: object, **kwargs: object) -> None:
        raise repository_module.RepositoryError("record write failed")

    monkeypatch.setattr(repository_module.JsonFileRecordingRepository, "create", failing_create)

    response = upload(client, "take.wav", wav_bytes(tmp_path))

    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert stored_files(settings) == [], "stored bytes must be rolled back"
    assert temp_files(settings) == []


# --------------------------------------------------------------------------
# Cleanup on every rejection path
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("", b"x"),
        ("take.exe", b"x"),
        ("take.wav", b""),
        ("take.wav", b"not audio"),
        ("take.wav", ELF_BYTES),
        ("song.mp3", b"not audio"),
    ],
)
def test_every_rejection_leaves_no_files_behind(
    client: TestClient, settings: Settings, name: str, content: bytes
):
    response = upload(client, name, content)

    assert response.status_code >= 400
    assert_nothing_stored(settings)
    metadata_dir = settings.storage_root / "metadata"
    assert not metadata_dir.exists() or list(metadata_dir.iterdir()) == []


def test_system_temp_directory_is_not_left_littered(
    client: TestClient, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    """The pipeline's own temp file must be removed on success and on failure."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    import tempfile as tempfile_module

    monkeypatch.setattr(tempfile_module, "tempdir", None)

    upload(client, "take.wav", wav_bytes(tmp_path))
    upload(client, "take.wav", b"not audio")
    upload(client, "take.exe", b"x")

    leftovers = [p for p in scratch.iterdir() if p.name.startswith("vocallens-upload-")]
    assert leftovers == []


# --------------------------------------------------------------------------
# Response safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("take.wav", b""),
        ("take.exe", b"x"),
        ("", b"x"),
        ("song.mp3", b"not audio"),
        ("take.wav", FLAC_SIGNATURE),
    ],
)
def test_error_responses_never_leak_internals(
    client: TestClient, tmp_path: Path, settings: Settings, name: str, content: bytes
):
    response = upload(client, name, content)
    raw = response.text

    assert str(tmp_path) not in raw
    assert str(settings.storage_root) not in raw
    assert "/tmp" not in raw
    assert "vocallens-upload-" not in raw
    assert "Traceback" not in raw
    assert "Error(" not in raw
    for leaked in ("MutagenError", "StorageWriteError", "ApiError", "HeaderNotFoundError"):
        assert leaked not in raw


def test_success_response_never_exposes_a_path(
    client: TestClient, tmp_path: Path, settings: Settings
):
    response = upload(client, "take.wav", wav_bytes(tmp_path))
    body = json.loads(response.text)

    assert str(settings.storage_root) not in response.text
    assert "path" not in {key.lower() for key in body}
    assert "/" not in body["recording_id"]


# --------------------------------------------------------------------------
# OpenAPI
# --------------------------------------------------------------------------


def test_openapi_documents_the_upload_endpoint(client: TestClient):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/recordings"]["post"]

    assert "multipart/form-data" in operation["requestBody"]["content"]
    assert operation["responses"]["201"]
    for status_code in ("400", "413", "415", "422", "500"):
        assert status_code in operation["responses"]
    description = operation["description"]
    assert "WAV" in description and "MP3" in description


def test_openapi_error_responses_reference_the_shared_envelope(client: TestClient):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/recordings"]["post"]
    ref = operation["responses"]["413"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("/ErrorResponse")
    envelope = schema["components"]["schemas"]["ErrorResponse"]
    assert set(envelope["properties"]) == {"status", "error_code", "message"}
