"""Storage tests.

These run against a real filesystem in ``tmp_path``. Mocks appear only for
failures that cannot be provoked reliably otherwise (a write error partway
through, an unreadable file when running as root).
"""

import os
import re
import stat
from pathlib import Path

import pytest

from app.services.audio.storage import (
    InvalidRecordingIdError,
    RecordingNotFoundError,
    RecordingStorage,
    SourceUnavailableError,
    StorageError,
    StorageWriteError,
    StoredRecording,
)
from tests.fixtures import mp3_bytes, write_mp3, write_wav

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")


@pytest.fixture
def storage(tmp_path: Path) -> RecordingStorage:
    return RecordingStorage(tmp_path / "storage")


def _wav(tmp_path: Path) -> Path:
    """A validated-looking source file, standing in for the upload temp file."""
    source_dir = tmp_path / "incoming"
    source_dir.mkdir(exist_ok=True)
    return write_wav(source_dir / "take.wav", seconds=1.0)


@pytest.fixture
def wav_source(tmp_path: Path) -> Path:
    return _wav(tmp_path)


# --------------------------------------------------------------------------
# Save
# --------------------------------------------------------------------------


def test_saves_a_wav(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")

    assert isinstance(stored, StoredRecording)
    assert stored.extension == ".wav"
    assert stored.size_bytes == wav_source.stat().st_size
    assert storage.exists(stored.recording_id)


def test_saves_an_mp3(storage: RecordingStorage, tmp_path: Path):
    source = write_mp3(tmp_path / "song.mp3", frames=30)

    stored = storage.save(source, extension=".mp3")

    assert stored.extension == ".mp3"
    assert storage.path_for(stored.recording_id).suffix == ".mp3"


def test_generated_id_is_a_uuid4_hex(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")
    assert re.fullmatch(r"[0-9a-f]{32}", stored.recording_id)


def test_stored_bytes_match_the_source_exactly(storage: RecordingStorage, wav_source: Path):
    expected = wav_source.read_bytes()

    stored = storage.save(wav_source, extension=".wav")

    assert storage.path_for(stored.recording_id).read_bytes() == expected


def test_stored_filename_is_the_id_plus_extension(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")
    path = storage.path_for(stored.recording_id)
    assert path.name == f"{stored.recording_id}.wav"


def test_extension_is_normalised_to_lower_case(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".WAV")
    assert stored.extension == ".wav"
    assert storage.path_for(stored.recording_id).name.endswith(".wav")


def test_source_file_is_left_in_place_after_saving(storage: RecordingStorage, wav_source: Path):
    """The caller keeps ownership: storage copies, it does not move."""
    before = wav_source.read_bytes()

    storage.save(wav_source, extension=".wav")

    assert wav_source.exists()
    assert wav_source.read_bytes() == before


def test_large_source_is_copied_faithfully(storage: RecordingStorage, tmp_path: Path):
    """Bigger than the 1 MiB copy chunk, so the streaming loop really iterates."""
    source = tmp_path / "big.mp3"
    source.write_bytes(mp3_bytes(frames=8000))
    assert source.stat().st_size > 3 * 1024 * 1024

    stored = storage.save(source, extension=".mp3")

    assert stored.size_bytes == source.stat().st_size
    assert storage.path_for(stored.recording_id).read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "extension",
    [".", "", "wav", ".w/av", "../wav", ".wav/../..", ".a" * 9, ".wav\x00", ". wav", ".WAV."],
)
def test_unusable_extensions_are_refused_by_shape(
    storage: RecordingStorage, wav_source: Path, extension: str
):
    """Anything that could alter where the file lands is refused."""
    with pytest.raises(StorageWriteError):
        storage.save(wav_source, extension=extension)


def test_a_well_formed_non_audio_extension_is_still_stored(
    storage: RecordingStorage, wav_source: Path
):
    """Storage checks shape, not audio suitability — that is validation's job.

    Documents the layer boundary deliberately: storage must not quietly become
    a second, divergent copy of the format allowlist.
    """
    stored = storage.save(wav_source, extension=".bin")
    assert storage.path_for(stored.recording_id).suffix == ".bin"


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


def test_two_saves_get_different_ids_and_files(storage: RecordingStorage, wav_source: Path):
    first = storage.save(wav_source, extension=".wav")
    second = storage.save(wav_source, extension=".wav")

    assert first.recording_id != second.recording_id
    assert storage.path_for(first.recording_id) != storage.path_for(second.recording_id)
    assert len(list(storage.recordings_dir.iterdir())) == 2


@pytest.mark.parametrize(
    "hostile_name",
    [
        "../../etc/passwd",
        "..\\..\\win.ini",
        "/etc/shadow",
        "~/.ssh/authorized_keys",
        "take.wav",
        "\x00evil.wav",
    ],
)
def test_client_filename_cannot_influence_the_storage_path(
    storage: RecordingStorage, tmp_path: Path, hostile_name: str
):
    """The storage API takes no client name at all — the id is server-made."""
    source = _wav(tmp_path)

    stored = storage.save(source, extension=".wav")
    path = storage.path_for(stored.recording_id)

    assert path.parent == storage.recordings_dir.resolve()
    assert Path(hostile_name).name not in path.name
    assert path.name == f"{stored.recording_id}.wav"


def test_every_stored_file_stays_under_the_configured_root(
    storage: RecordingStorage, tmp_path: Path
):
    source = _wav(tmp_path)
    root = storage.root.resolve()

    for _ in range(5):
        stored = storage.save(source, extension=".wav")
        assert root in storage.path_for(stored.recording_id).parents


def test_partial_writes_live_outside_the_recordings_directory(
    storage: RecordingStorage, wav_source: Path
):
    storage.save(wav_source, extension=".wav")

    assert storage.temp_dir.exists()
    assert list(storage.temp_dir.iterdir()) == []
    assert all(p.suffix == ".wav" for p in storage.recordings_dir.iterdir())


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_id",
    [
        "../../../etc/passwd",
        "..",
        ".",
        "/etc/passwd",
        "a" * 32 + "/../../etc/passwd",
        "0123456789abcdef0123456789abcde",  # 31 chars
        "0123456789abcdef0123456789abcdefa",  # 33 chars
        "0123456789ABCDEF0123456789ABCDEF",  # upper case
        "0123456789abcdef0123456789abcdeZ",
        "",
        "*",
        "?" * 32,
        "\x00" * 32,
    ],
)
def test_malformed_ids_are_refused(storage: RecordingStorage, hostile_id: str):
    with pytest.raises(InvalidRecordingIdError):
        storage.path_for(hostile_id)
    assert storage.exists(hostile_id) is False


def test_path_traversal_cannot_read_a_file_outside_the_root(
    storage: RecordingStorage, tmp_path: Path
):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"classified")

    for attempt in ("../../secret.txt", "../secret.txt", str(secret)):
        with pytest.raises(StorageError):
            storage.open_recording(attempt)


def test_a_symlink_pointing_outside_the_root_is_refused(
    storage: RecordingStorage, tmp_path: Path, wav_source: Path
):
    """resolve() follows the link, so containment rejects it before any read."""
    storage.save(wav_source, extension=".wav")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"classified")

    planted_id = "f" * 32
    link = storage.recordings_dir / f"{planted_id}.wav"
    link.symlink_to(secret)

    with pytest.raises(InvalidRecordingIdError):
        storage.path_for(planted_id)
    with pytest.raises(StorageError):
        storage.open_recording(planted_id)


def test_ids_are_not_predictable_across_saves(storage: RecordingStorage, wav_source: Path):
    ids = {storage.save(wav_source, extension=".wav").recording_id for _ in range(20)}
    assert len(ids) == 20


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


@POSIX_ONLY
def test_stored_file_is_owner_read_write_only(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")
    mode = stat.S_IMODE(storage.path_for(stored.recording_id).stat().st_mode)
    assert mode == 0o600


@POSIX_ONLY
def test_stored_file_is_not_executable(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")
    mode = storage.path_for(stored.recording_id).stat().st_mode
    assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@POSIX_ONLY
def test_storage_directories_are_owner_only(storage: RecordingStorage, wav_source: Path):
    storage.save(wav_source, extension=".wav")
    for directory in (storage.recordings_dir, storage.temp_dir):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


def test_missing_source_raises_source_unavailable(storage: RecordingStorage, tmp_path: Path):
    with pytest.raises(SourceUnavailableError):
        storage.save(tmp_path / "nope.wav", extension=".wav")


def test_directory_as_source_raises_source_unavailable(storage: RecordingStorage, tmp_path: Path):
    with pytest.raises(SourceUnavailableError):
        storage.save(tmp_path, extension=".wav")


@POSIX_ONLY
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root bypasses file permissions")
def test_unreadable_source_raises_source_unavailable(storage: RecordingStorage, wav_source: Path):
    wav_source.chmod(0o000)
    try:
        with pytest.raises(SourceUnavailableError):
            storage.save(wav_source, extension=".wav")
    finally:
        wav_source.chmod(0o600)


@POSIX_ONLY
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root bypasses directory permissions")
def test_unwritable_destination_raises_storage_write_error(
    storage: RecordingStorage, wav_source: Path
):
    storage.root.mkdir(parents=True)
    storage.root.chmod(0o500)
    try:
        with pytest.raises(StorageWriteError):
            storage.save(wav_source, extension=".wav")
    finally:
        storage.root.chmod(0o700)


def test_unreadable_source_raises_source_unavailable_when_permissions_are_bypassed(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same path as the chmod test above, reachable when running as root.

    CI often runs as root, where chmod(0o000) is ignored, so the branch would
    otherwise go untested exactly where it matters most.
    """

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", denied)

    with pytest.raises(SourceUnavailableError):
        storage.save(wav_source, extension=".wav")


def test_directory_creation_failure_raises_storage_write_error(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """Destination unavailable, independent of filesystem permissions."""

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", denied)

    with pytest.raises(StorageWriteError):
        storage.save(wav_source, extension=".wav")


def test_a_failed_write_leaves_no_recording_and_no_temp_file(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """Mocked: a mid-copy I/O error is not reproducible on a real filesystem."""

    def failing_copy(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("app.services.audio.storage.shutil.copyfileobj", failing_copy)

    with pytest.raises(StorageWriteError):
        storage.save(wav_source, extension=".wav")

    assert list(storage.recordings_dir.iterdir()) == []
    assert list(storage.temp_dir.iterdir()) == []


def test_a_failed_rename_leaves_no_partial_recording(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    def failing_replace(*args: object, **kwargs: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr("app.services.audio.storage.os.replace", failing_replace)

    with pytest.raises(StorageWriteError):
        storage.save(wav_source, extension=".wav")

    assert list(storage.recordings_dir.iterdir()) == []
    assert list(storage.temp_dir.iterdir()) == []


def test_an_interrupted_write_leaves_no_partial_recording(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """Even a non-OSError abort (cancellation, KeyboardInterrupt) must not litter."""

    def cancelled(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("app.services.audio.storage.shutil.copyfileobj", cancelled)

    with pytest.raises(KeyboardInterrupt):
        storage.save(wav_source, extension=".wav")

    assert list(storage.recordings_dir.iterdir()) == []
    assert list(storage.temp_dir.iterdir()) == []


def test_storage_errors_never_contain_a_filesystem_path(storage: RecordingStorage, tmp_path: Path):
    missing = tmp_path / "very-distinctive-dirname" / "nope.wav"

    with pytest.raises(SourceUnavailableError) as excinfo:
        storage.save(missing, extension=".wav")

    message = str(excinfo.value)
    assert "very-distinctive-dirname" not in message
    assert str(tmp_path) not in message
    assert "nope.wav" not in message


# --------------------------------------------------------------------------
# Atomicity
# --------------------------------------------------------------------------


def test_recording_appears_only_after_the_write_completes(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """Observe the recordings directory from inside the copy, before the rename."""
    observed: list[list[str]] = []
    real_copy = __import__("shutil").copyfileobj

    def observing_copy(source: object, target: object, *args: object, **kwargs: object) -> None:
        observed.append([p.name for p in storage.recordings_dir.iterdir()])
        real_copy(source, target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.services.audio.storage.shutil.copyfileobj", observing_copy)

    stored = storage.save(wav_source, extension=".wav")

    assert observed == [[]], "recordings/ must still be empty while bytes are in flight"
    assert [p.name for p in storage.recordings_dir.iterdir()] == [f"{stored.recording_id}.wav"]


def test_the_temp_file_is_not_visible_as_a_recording(
    storage: RecordingStorage, wav_source: Path, monkeypatch: pytest.MonkeyPatch
):
    """While writing, the id must not resolve — nothing half-written is findable."""
    seen: list[bool] = []
    real_copy = __import__("shutil").copyfileobj

    def observing_copy(source: object, target: object, *args: object, **kwargs: object) -> None:
        seen.append(any(storage.recordings_dir.glob("*")))
        real_copy(source, target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.services.audio.storage.shutil.copyfileobj", observing_copy)

    storage.save(wav_source, extension=".wav")

    assert seen == [False]


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


def test_open_recording_returns_the_stored_bytes(storage: RecordingStorage, wav_source: Path):
    expected = wav_source.read_bytes()
    stored = storage.save(wav_source, extension=".wav")

    with storage.open_recording(stored.recording_id) as handle:
        assert handle.read() == expected


def test_open_recording_for_a_missing_id_raises_not_found(storage: RecordingStorage):
    with pytest.raises(RecordingNotFoundError):
        storage.open_recording("0" * 32)


def test_path_for_a_missing_id_raises_not_found(storage: RecordingStorage):
    with pytest.raises(RecordingNotFoundError):
        storage.path_for("0" * 32)


def test_exists_reports_presence(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")
    assert storage.exists(stored.recording_id) is True
    assert storage.exists("0" * 32) is False


def test_exists_is_false_before_anything_is_saved(storage: RecordingStorage):
    assert storage.exists("0" * 32) is False
    assert not storage.root.exists(), "reads must not create the storage tree"


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def test_delete_removes_a_stored_recording(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")

    assert storage.delete(stored.recording_id) is True
    assert storage.exists(stored.recording_id) is False
    assert list(storage.recordings_dir.iterdir()) == []


def test_deleting_a_missing_recording_reports_false(storage: RecordingStorage):
    assert storage.delete("0" * 32) is False


def test_delete_is_idempotent(storage: RecordingStorage, wav_source: Path):
    stored = storage.save(wav_source, extension=".wav")

    assert storage.delete(stored.recording_id) is True
    assert storage.delete(stored.recording_id) is False


def test_delete_refuses_a_malformed_id(storage: RecordingStorage):
    with pytest.raises(InvalidRecordingIdError):
        storage.delete("../../etc/passwd")


def test_delete_does_not_touch_other_recordings(storage: RecordingStorage, wav_source: Path):
    keep = storage.save(wav_source, extension=".wav")
    drop = storage.save(wav_source, extension=".wav")

    storage.delete(drop.recording_id)

    assert storage.exists(keep.recording_id) is True


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_storage_root_comes_from_settings(tmp_path: Path):
    from app.core.config import Settings

    settings = Settings(_env_file=None, storage_root=tmp_path / "configured")
    storage = RecordingStorage(settings.storage_root)

    stored = storage.save(_wav(tmp_path), extension=".wav")

    assert (tmp_path / "configured").resolve() in storage.path_for(stored.recording_id).parents


def test_default_storage_root_is_relative_and_not_absolute():
    """No hardcoded absolute path: deployments override it, dev just works."""
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert not settings.storage_root.is_absolute()
    assert settings.storage_root == Path("storage")
