"""Metadata extraction tests.

These run against the real mutagen parser on real generated files. Mocks appear
only where a genuine failure cannot be produced otherwise (a parser raising an
unexpected non-mutagen exception).
"""

import hashlib
import os
from pathlib import Path

import pytest

from app.core.errors import ApiError, ErrorCode
from app.services.audio.metadata import AudioFormat, AudioMetadata, extract_metadata
from tests.fixtures import (
    AIFF_SIGNATURE,
    ELF_BYTES,
    FLAC_SIGNATURE,
    M4A_SIGNATURE,
    OGG_SIGNATURE,
    PE_BYTES,
    SHELL_SCRIPT_BYTES,
    deterministic_bytes,
    mp3_bytes,
    mp3_expected_duration,
    write_mp3,
    write_wav,
)

# --------------------------------------------------------------------------
# Valid files
# --------------------------------------------------------------------------


def test_valid_wav_is_accepted(tmp_path: Path):
    path = write_wav(tmp_path / "voice.wav", seconds=1.5, sample_rate=8000, channels=1)

    metadata = extract_metadata(path)

    assert metadata.format is AudioFormat.WAV
    assert metadata.duration_seconds == pytest.approx(1.5, abs=0.01)
    assert metadata.sample_rate == 8000
    assert metadata.channels == 1
    assert metadata.bits_per_sample == 16


def test_valid_mp3_is_accepted(tmp_path: Path):
    path = write_mp3(tmp_path / "song.mp3", frames=46)

    metadata = extract_metadata(path)

    assert metadata.format is AudioFormat.MP3
    # mutagen derives MP3 length from size and bitrate, so allow a small margin.
    assert metadata.duration_seconds == pytest.approx(mp3_expected_duration(46), rel=0.02)
    assert metadata.sample_rate == 44100
    assert metadata.channels == 1
    assert metadata.bits_per_sample is None


@pytest.mark.parametrize(
    ("seconds", "sample_rate", "channels"),
    [
        (0.5, 8000, 1),
        (2.0, 16000, 1),
        (1.0, 22050, 2),
        (1.0, 44100, 2),
        (3.0, 48000, 1),
    ],
)
def test_wav_properties_are_reported_accurately(
    tmp_path: Path, seconds: float, sample_rate: int, channels: int
):
    path = write_wav(
        tmp_path / "take.wav", seconds=seconds, sample_rate=sample_rate, channels=channels
    )

    metadata = extract_metadata(path)

    assert metadata.duration_seconds == pytest.approx(seconds, abs=0.01)
    assert metadata.sample_rate == sample_rate
    assert metadata.channels == channels


@pytest.mark.parametrize("channels", [1, 2])
def test_mp3_channel_mode_is_detected(tmp_path: Path, channels: int):
    path = write_mp3(tmp_path / "song.mp3", frames=20, channels=channels)
    assert extract_metadata(path).channels == channels


@pytest.mark.parametrize("frames", [5, 46, 200])
def test_mp3_duration_scales_with_length(tmp_path: Path, frames: int):
    path = write_mp3(tmp_path / "song.mp3", frames=frames)
    metadata = extract_metadata(path)
    assert metadata.duration_seconds == pytest.approx(mp3_expected_duration(frames), rel=0.02)
    assert metadata.duration_seconds > 0


def test_metadata_is_immutable(tmp_path: Path):
    metadata = extract_metadata(write_wav(tmp_path / "take.wav"))
    assert isinstance(metadata, AudioMetadata)
    with pytest.raises(AttributeError):
        metadata.duration_seconds = 99.0  # type: ignore[misc]


# --------------------------------------------------------------------------
# Content decides, not the filename
# --------------------------------------------------------------------------


def test_a_real_wav_named_mp3_is_reported_as_wav(tmp_path: Path):
    """The extension is ignored entirely; the caller reconciles any mismatch."""
    path = write_wav(tmp_path / "voice.mp3", seconds=1.0, sample_rate=8000)
    assert extract_metadata(path).format is AudioFormat.WAV


def test_a_real_mp3_named_wav_is_reported_as_mp3(tmp_path: Path):
    path = tmp_path / "song.wav"
    path.write_bytes(mp3_bytes(frames=30))
    assert extract_metadata(path).format is AudioFormat.MP3


def test_a_file_with_no_extension_is_parsed_normally(tmp_path: Path):
    path = write_wav(tmp_path / "recording", seconds=1.0)
    assert extract_metadata(path).format is AudioFormat.WAV


# --------------------------------------------------------------------------
# Invalid content
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["song.mp3", "voice.wav", "take.WAV", "clip"])
def test_arbitrary_bytes_are_rejected_whatever_they_are_called(tmp_path: Path, name: str):
    path = tmp_path / name
    path.write_bytes(deterministic_bytes(4096))

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("elf", ELF_BYTES + deterministic_bytes(2048)),
        ("pe", PE_BYTES + deterministic_bytes(2048)),
        ("shell", SHELL_SCRIPT_BYTES),
    ],
)
def test_executable_content_renamed_to_audio_is_rejected(
    tmp_path: Path, label: str, content: bytes
):
    path = tmp_path / "song.mp3"
    path.write_bytes(content)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_empty_file_is_rejected(tmp_path: Path):
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_truncated_mp3_is_rejected(tmp_path: Path):
    """Too few bytes to hold a frame header — the parser detects this reliably."""
    path = tmp_path / "song.mp3"
    path.write_bytes(mp3_bytes(frames=46)[:40])

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_wav_with_a_forged_header_is_rejected(tmp_path: Path):
    """RIFF/WAVE magic with no usable format chunk describes no real audio."""
    path = tmp_path / "voice.wav"
    path.write_bytes(b"RIFF" + (2**31).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 64)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_wav_header_without_any_audio_data_is_rejected(tmp_path: Path):
    """A zero-length data chunk parses but describes zero-duration audio."""
    path = write_wav(tmp_path / "silent.wav", seconds=0.0, sample_rate=8000)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_truncated_wav_keeps_its_declared_duration(tmp_path: Path):
    """A documented limitation, asserted so a future change is noticed.

    mutagen reads WAV duration from the declared data-chunk size, so a file
    whose samples were cut off still reports its original length. Detecting
    this needs a decoder, which arrives in Phase 2.
    """
    source = write_wav(tmp_path / "full.wav", seconds=3.0, sample_rate=8000)
    full = source.read_bytes()
    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(full[: len(full) // 3])

    metadata = extract_metadata(truncated)

    assert metadata.duration_seconds == pytest.approx(3.0, abs=0.01)
    assert truncated.stat().st_size < source.stat().st_size


# --------------------------------------------------------------------------
# Recognisable but unsupported containers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected_label"),
    [
        (FLAC_SIGNATURE, "FLAC"),
        (OGG_SIGNATURE, "Ogg"),
        (M4A_SIGNATURE, "M4A"),
        (AIFF_SIGNATURE, "AIFF"),
    ],
)
def test_known_unsupported_containers_are_named_in_the_rejection(
    tmp_path: Path, content: bytes, expected_label: str
):
    path = tmp_path / "song.mp3"
    path.write_bytes(content)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_FORMAT
    assert expected_label in excinfo.value.message


def test_m4a_rejection_points_at_the_supported_formats(tmp_path: Path):
    """M4A is deferred to Phase 2; the message must not imply it works today."""
    path = tmp_path / "voice.m4a"
    path.write_bytes(M4A_SIGNATURE)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert "WAV or MP3" in excinfo.value.message


# --------------------------------------------------------------------------
# Impossible metadata
# --------------------------------------------------------------------------


class _FakeInfo:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _FakeParsed:
    def __init__(self, info: _FakeInfo) -> None:
        self.info = info


@pytest.mark.parametrize(
    "info",
    [
        _FakeInfo(length=None, sample_rate=44100, channels=1),
        _FakeInfo(length=float("nan"), sample_rate=44100, channels=1),
        _FakeInfo(length=float("inf"), sample_rate=44100, channels=1),
        _FakeInfo(length=-1.0, sample_rate=44100, channels=1),
        _FakeInfo(length=0.0, sample_rate=44100, channels=1),
        _FakeInfo(length=1.0, sample_rate=0, channels=1),
        _FakeInfo(length=1.0, sample_rate=-44100, channels=1),
        _FakeInfo(length=1.0, sample_rate=None, channels=1),
        _FakeInfo(length=1.0, sample_rate=44100, channels=0),
        _FakeInfo(length=1.0, sample_rate=44100, channels=-2),
        _FakeInfo(length=1.0, sample_rate=44100),  # attribute missing entirely
        _FakeInfo(),
    ],
)
def test_impossible_properties_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, info: _FakeInfo
):
    """Values no real recording can have mean a forged or damaged header.

    Mocked deliberately: mutagen will not emit these from a genuine file, and
    the guard still has to hold if a parser ever does.
    """
    path = write_wav(tmp_path / "take.wav")
    monkeypatch.setattr("app.services.audio.metadata._parse", lambda _path: _FakeParsed(info))

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO


def test_unexpected_parser_exception_becomes_a_safe_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A parser bug must not reach the client as a 500."""
    path = write_wav(tmp_path / "take.wav")

    def explode(*args: object, **kwargs: object) -> None:
        raise ValueError("internal parser detail /var/secret/path")

    monkeypatch.setattr("app.services.audio.metadata.mutagen.File", explode)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)
    assert excinfo.value.code is ErrorCode.CORRUPTED_AUDIO
    assert "/var/secret/path" not in excinfo.value.message


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------


def test_extraction_does_not_modify_the_source_file(tmp_path: Path):
    path = write_wav(tmp_path / "take.wav", seconds=1.0)
    before_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    before_stat = path.stat()

    extract_metadata(path)

    after_stat = path.stat()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_digest
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns


def test_extraction_works_on_a_read_only_file(tmp_path: Path):
    """Proof there is no write path: the file is not writable at all."""
    path = write_wav(tmp_path / "take.wav", seconds=1.0)
    path.chmod(0o400)
    try:
        assert extract_metadata(path).duration_seconds == pytest.approx(1.0, abs=0.01)
    finally:
        path.chmod(0o600)


def test_extraction_creates_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source_dir = tmp_path / "audio"
    source_dir.mkdir()
    work_dir = tmp_path / "cwd"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    valid = write_wav(source_dir / "take.wav", seconds=1.0)
    hostile = source_dir / "bad.mp3"
    hostile.write_bytes(deterministic_bytes(2048))

    extract_metadata(valid)
    with pytest.raises(ApiError):
        extract_metadata(hostile)

    assert list(work_dir.iterdir()) == []
    assert sorted(p.name for p in source_dir.iterdir()) == ["bad.mp3", "take.wav"]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("bad.mp3", b""),
        ("bad.wav", b"not audio at all"),
        ("bad.mp3", FLAC_SIGNATURE),
    ],
)
def test_errors_never_expose_the_filesystem_path(tmp_path: Path, name: str, content: bytes):
    secret_dir = tmp_path / "srv" / "uploads" / "tmp_a1b2c3"
    secret_dir.mkdir(parents=True)
    path = secret_dir / name
    path.write_bytes(content)

    with pytest.raises(ApiError) as excinfo:
        extract_metadata(path)

    message = excinfo.value.message
    assert str(path) not in message
    assert str(tmp_path) not in message
    assert "tmp_a1b2c3" not in message
    assert name not in message
    assert "Traceback" not in message


def test_a_missing_file_is_an_internal_fault_not_a_bad_upload(tmp_path: Path):
    """Our own bug must not be reported to a user as corrupted audio."""
    with pytest.raises(OSError):
        extract_metadata(tmp_path / "does-not-exist.wav")


def test_a_directory_is_rejected_without_parsing(tmp_path: Path):
    with pytest.raises(OSError):
        extract_metadata(tmp_path)


def test_a_fifo_is_refused_rather_than_read(tmp_path: Path):
    """Reading a FIFO would block forever; is_file() keeps us out of it."""
    fifo = tmp_path / "pipe.wav"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        extract_metadata(fifo)


def test_only_the_two_supported_parsers_are_exposed_to_input():
    """Hostile bytes must not reach mutagen's full parser set."""
    from mutagen.mp3 import MP3
    from mutagen.wave import WAVE

    from app.services.audio.metadata import _PARSERS

    assert _PARSERS == [MP3, WAVE]


def test_signature_probe_reads_only_the_header_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Our own container probe reads a header, never the whole file.

    Scope note: this instruments ``Path.open``, which is how *this module*
    reads. mutagen opens the file itself and is not covered here — its
    header-only parsing is a property of the library, asserted indirectly by
    the 5 MB file below completing at all.
    """
    path = tmp_path / "big.mp3"
    path.write_bytes(deterministic_bytes(64) + b"\x00" * (5 * 1024 * 1024))

    reads: list[int | None] = []
    real_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        handle = real_open(self, *args, **kwargs)  # type: ignore[arg-type]
        original_read = handle.read

        def read(size: int | None = None, /):  # type: ignore[no-untyped-def]
            reads.append(size)
            return original_read(size)  # type: ignore[arg-type]

        handle.read = read  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", tracking_open)

    with pytest.raises(ApiError):
        extract_metadata(path)

    assert reads, "the signature probe should have opened the file"
    assert all(size is not None and size <= 16 for size in reads)
