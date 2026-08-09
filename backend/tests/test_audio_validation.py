import contextlib
import os
from pathlib import Path

import pytest

from app.core.errors import ApiError, ErrorCode
from app.services.audio import validation
from app.services.audio.validation import (
    MAX_FILENAME_LENGTH,
    extension_of,
    sanitize_filename,
    validate_extension,
    validate_size,
    validate_upload,
)

ONE_MB = 1024 * 1024


def assert_api_error(excinfo: pytest.ExceptionInfo[ApiError], code: ErrorCode) -> None:
    assert excinfo.value.code is code
    assert excinfo.value.message, "every rejection must carry a message for the user"


# --------------------------------------------------------------------------
# Filename sanitization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("../../../../etc/shadow", "shadow"),
        ("/etc/passwd", "passwd"),
        ("..\\..\\win.ini", "win.ini"),
        ("C:\\Users\\bob\\take.wav", "take.wav"),
        ("\\\\server\\share\\take.wav", "take.wav"),
        ("folder/subfolder/take.wav", "take.wav"),
    ],
)
def test_path_traversal_collapses_to_a_bare_name(raw: str, expected: str):
    result = sanitize_filename(raw)
    assert result == expected
    assert "/" not in result and "\\" not in result


def test_null_bytes_are_removed_not_truncated_at():
    """A NUL must not shorten the name a later C library would see."""
    assert sanitize_filename("take\x00.wav") == "take.wav"
    assert sanitize_filename("shell.exe\x00.mp3") == "shell.exe.mp3"


@pytest.mark.parametrize(
    "raw",
    [
        "ta\x01ke.wav",
        "ta\x1fke.wav",
        "ta\x7fke.wav",  # DEL
        "ta\x9bke.wav",  # C1 control
        "ta\tke.wav",  # tab: removed as a control char, not folded to a space
        "ta\x0bke.wav",
    ],
)
def test_control_characters_are_stripped(raw: str):
    assert sanitize_filename(raw) == "take.wav"


def test_interior_whitespace_is_kept_but_runs_collapse():
    assert sanitize_filename("take one.wav") == "take one.wav"
    assert sanitize_filename("take  two.wav") == "take two.wav"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (".hidden.mp3", "hidden.mp3"),
        ("...take.wav", "take.wav"),
        ("  .take.wav  ", "take.wav"),
        ("take.wav.", "take.wav"),
        ("take.wav   ", "take.wav"),
    ],
)
def test_leading_and_trailing_dots_and_spaces_are_trimmed(raw: str, expected: str):
    assert sanitize_filename(raw) == expected


def test_very_long_names_are_truncated_but_keep_the_extension():
    result = sanitize_filename("a" * 500 + ".wav")
    assert len(result) == MAX_FILENAME_LENGTH
    assert result.endswith(".wav")


def test_very_long_name_without_a_usable_extension_is_still_capped():
    result = sanitize_filename("b" * 400)
    assert len(result) == MAX_FILENAME_LENGTH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("тест.mp3", "тест.mp3"),
        ("録音.wav", "録音.wav"),
        ("café-take.wav", "café-take.wav"),
        ("تسجيل.mp3", "تسجيل.mp3"),
        ("הקלטה.wav", "הקלטה.wav"),
        ("🎤 take one.wav", "🎤 take one.wav"),
    ],
)
def test_unicode_and_rtl_scripts_are_preserved(raw: str, expected: str):
    """Non-Latin names are legitimate and must survive intact."""
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("invoice\u202egpj.exe", "invoicegpj.exe"),
        ("take\u200f.wav", "take.wav"),
        ("take\u202a\u202b.mp3", "take.mp3"),
        ("take\u2066\u2069.wav", "take.wav"),
        ("\ufefftake.wav", "take.wav"),
    ],
)
def test_rtl_and_bidi_control_characters_are_stripped(raw: str, expected: str):
    """Bidi overrides disguise extensions; the visible name must match the real one."""
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("take:one.wav", "take_one.wav"),
        ('ta"ke.wav', "ta_ke.wav"),
        ("take<>|?*.wav", "take_____.wav"),
    ],
)
def test_windows_reserved_characters_are_replaced(raw: str, expected: str):
    assert sanitize_filename(raw) == expected


def test_filename_without_an_extension_survives_sanitization():
    """Sanitization only cleans the name; rejecting it is the extension gate's job."""
    assert sanitize_filename("recording") == "recording"


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "...", "/", "\\", "../", "..\\", "\x00", "\u202e", ". . .", "///"],
)
def test_names_that_cannot_be_salvaged_are_rejected(raw: str | None):
    with pytest.raises(ApiError) as excinfo:
        sanitize_filename(raw)
    assert_api_error(excinfo, ErrorCode.INVALID_FILENAME)


def test_double_extension_is_preserved_verbatim_for_display():
    """Sanitization does not rewrite the name; the extension gate decides."""
    assert sanitize_filename("song.mp3.exe") == "song.mp3.exe"


def test_sanitized_name_is_never_a_usable_path():
    hostile = [
        "../../etc/passwd",
        "..\\..\\win.ini",
        "/absolute/take.wav",
        "~/take.wav",
        "take\x00.wav",
        "C:\\take.wav",
        "song.mp3.exe",
    ]
    for raw in hostile:
        result = sanitize_filename(raw)
        assert os.sep not in result
        assert "/" not in result and "\\" not in result
        assert not os.path.isabs(result)
        assert result not in {".", ".."}
        # Path treats it as a single component, i.e. it cannot redirect a write.
        assert Path(result).name == result
        assert len(Path(result).parts) == 1


# --------------------------------------------------------------------------
# Extension validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("take.wav", ".wav"),
        ("take.WAV", ".wav"),
        ("take.Wav", ".wav"),
        ("take.mp3", ".mp3"),
        ("take.MP3", ".mp3"),
        ("my.song.take.mp3", ".mp3"),
    ],
)
def test_allowed_extensions_are_accepted_case_insensitively(filename: str, expected: str):
    assert validate_extension(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "take.m4a",  # deferred to Phase 2 with the decoder decision
        "take.M4A",
        "take.exe",
        "song.mp3.exe",
        "song.wav.sh",
        "take.flac",
        "take.ogg",
        "take.wave",
        "take.mp4",
        "recording",  # no extension at all
        ".mp3",  # extension with no stem
        "take.",
    ],
)
def test_disallowed_extensions_are_rejected(filename: str):
    with pytest.raises(ApiError) as excinfo:
        validate_extension(filename)
    assert_api_error(excinfo, ErrorCode.UNSUPPORTED_FORMAT)


def test_m4a_rejection_message_does_not_promise_future_support():
    with pytest.raises(ApiError) as excinfo:
        validate_extension("take.m4a")
    assert "MP3" in excinfo.value.message and "WAV" in excinfo.value.message


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("take.wav", ".wav"),
        ("song.mp3.exe", ".exe"),
        ("recording", ""),
        (".mp3", ""),
        ("take.", ""),
        ("TAKE.WAV", ".wav"),
    ],
)
def test_extension_of_reads_only_the_final_suffix(filename: str, expected: str):
    assert extension_of(filename) == expected


# --------------------------------------------------------------------------
# Size validation
# --------------------------------------------------------------------------


def test_size_below_the_limit_is_accepted():
    validate_size(49 * ONE_MB, max_size_bytes=50 * ONE_MB)


def test_size_exactly_at_the_limit_is_accepted():
    validate_size(50 * ONE_MB, max_size_bytes=50 * ONE_MB)


def test_size_one_byte_over_the_limit_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        validate_size(50 * ONE_MB + 1, max_size_bytes=50 * ONE_MB)
    assert_api_error(excinfo, ErrorCode.FILE_TOO_LARGE)
    assert "50 MB" in excinfo.value.message


def test_zero_bytes_passes_the_size_gate():
    """Emptiness is a content problem, rejected later by the metadata step."""
    validate_size(0, max_size_bytes=50 * ONE_MB)


def test_size_limit_comes_from_the_caller_not_a_hardcoded_value():
    validate_size(2 * ONE_MB, max_size_bytes=2 * ONE_MB)
    with pytest.raises(ApiError):
        validate_size(2 * ONE_MB + 1, max_size_bytes=2 * ONE_MB)


def test_settings_supply_the_limit_used_by_validation():
    """The limit the endpoint will pass is the configured one, not a constant."""
    from app.core.config import Settings

    settings = Settings(_env_file=None, max_audio_size_mb=3)
    validate_size(3 * ONE_MB, max_size_bytes=settings.max_audio_size_bytes)
    with pytest.raises(ApiError):
        validate_size(3 * ONE_MB + 1, max_size_bytes=settings.max_audio_size_bytes)


# --------------------------------------------------------------------------
# Composed upload validation
# --------------------------------------------------------------------------


def test_validate_upload_returns_the_sanitized_metadata():
    result = validate_upload(
        raw_filename="../../My Take.WAV",
        size_bytes=1024,
        max_size_bytes=50 * ONE_MB,
        declared_content_type="audio/wav",
    )
    assert result.filename == "My Take.WAV"
    assert result.extension == ".wav"
    assert result.size_bytes == 1024
    assert result.declared_content_type == "audio/wav"


def test_validate_upload_is_frozen():
    result = validate_upload(raw_filename="take.wav", size_bytes=1, max_size_bytes=ONE_MB)
    with pytest.raises(AttributeError):
        result.filename = "other.wav"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("content_type", "filename"),
    [
        ("application/x-msdownload", "take.wav"),
        ("text/plain", "take.mp3"),
        (None, "take.wav"),
        ("audio/wav", "take.wav"),
    ],
)
def test_declared_content_type_never_decides_the_outcome(content_type: str | None, filename: str):
    """Content-Type is advisory: a lying header neither rejects nor rescues."""
    result = validate_upload(
        raw_filename=filename,
        size_bytes=10,
        max_size_bytes=ONE_MB,
        declared_content_type=content_type,
    )
    assert result.declared_content_type == content_type


def test_content_type_alone_cannot_authorise_a_bad_extension():
    with pytest.raises(ApiError) as excinfo:
        validate_upload(
            raw_filename="take.exe",
            size_bytes=10,
            max_size_bytes=ONE_MB,
            declared_content_type="audio/wav",
        )
    assert_api_error(excinfo, ErrorCode.UNSUPPORTED_FORMAT)


def test_the_three_rejection_cases_are_distinguishable():
    """Missing name, bad extension and oversize must not collapse into one code."""
    cases = [
        (("", 10), ErrorCode.INVALID_FILENAME),
        (("take.exe", 10), ErrorCode.UNSUPPORTED_FORMAT),
        (("take.wav", ONE_MB + 1), ErrorCode.FILE_TOO_LARGE),
    ]
    seen = set()
    for (raw_filename, size), expected in cases:
        with pytest.raises(ApiError) as excinfo:
            validate_upload(raw_filename=raw_filename, size_bytes=size, max_size_bytes=ONE_MB)
        assert excinfo.value.code is expected
        seen.add(excinfo.value.code)
    assert len(seen) == 3


def test_filename_is_checked_before_size():
    """An unusable name is reported as such even when the file is also too big."""
    with pytest.raises(ApiError) as excinfo:
        validate_upload(raw_filename="take.exe", size_bytes=ONE_MB * 999, max_size_bytes=ONE_MB)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_FORMAT


# --------------------------------------------------------------------------
# Purity: validation touches no filesystem
# --------------------------------------------------------------------------


def test_validation_module_imports_no_filesystem_helpers():
    """A structural guard: this module has no way to reach the filesystem."""
    assert not hasattr(validation, "os")
    assert not hasattr(validation, "Path")
    assert not hasattr(validation, "open")
    assert not hasattr(validation, "shutil")
    assert not hasattr(validation, "tempfile")


def test_validation_never_creates_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the hostile corpus with the cwd inside an empty dir; it stays empty."""
    monkeypatch.chdir(tmp_path)

    def blow_up(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation must not open files")

    monkeypatch.setattr("builtins.open", blow_up)

    hostile = [
        "../../etc/passwd",
        "..\\..\\win.ini",
        "take\x00.wav",
        "\u202einvoice.exe",
        "a" * 500 + ".wav",
        "録音.wav",
        "song.mp3.exe",
        "take.wav",
        "",
        None,
    ]
    for raw in hostile:
        with contextlib.suppress(ApiError):
            validate_upload(raw_filename=raw, size_bytes=10, max_size_bytes=ONE_MB)

    assert list(tmp_path.iterdir()) == []
