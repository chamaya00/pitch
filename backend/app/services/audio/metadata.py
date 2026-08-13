"""Audio metadata extraction and content validation.

This layer answers one question: *is this file parseable as supported audio,
and what are its basic properties?* It knows nothing about HTTP, uploads,
storage or analysis, and it deliberately does not decode audio — headers only.

**Content decides, not the filename.** The extension check in ``validation.py``
is a cheap first filter; this module is the authoritative one. A file named
``song.mp3`` containing an executable is rejected here, and a real WAV named
``voice.mp3`` is reported as WAV. Reconciling a mismatch between the two is the
caller's job.

**Product limits live in the caller.** The extractor reports the duration it
found and rejects only values that are physically impossible (missing,
non-finite, zero or negative). Whether 6 minutes is too long is a product
decision, applied by the endpoint against ``Settings`` — that keeps this
service reusable by Phase 2's analysis pipeline.

Hostile input is the norm here, so only two parsers are exposed to it (MP3 and
WAVE), both pure Python. Nothing is decoded, executed, written or modified.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import mutagen
from mutagen import MutagenError
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


class AudioFormat(StrEnum):
    """Container formats supported in Phase 1."""

    WAV = "wav"
    MP3 = "mp3"


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    """Basic properties read from an audio file's headers.

    ``duration_seconds`` is what the container reports. For MP3 it is derived
    from bitrate and file size, so it is accurate to well under a percent but
    not exact; for WAV it is taken from the declared data-chunk size. Neither
    is a decoded measurement — Phase 2 supersedes this when it reads samples.

    File size is intentionally absent: the caller already counted the bytes it
    wrote, and reading it again here would duplicate filesystem logic for no
    gain.
    """

    duration_seconds: float
    sample_rate: int
    channels: int
    format: AudioFormat
    #: Present for WAV (PCM), absent for MP3, which has no fixed sample width.
    bits_per_sample: int | None = None


#: The only parsers hostile input is allowed to reach. ``mutagen.File`` would
#: otherwise try every format it knows; restricting it keeps the attack surface
#: to the two containers Phase 1 actually accepts.
_PARSERS: Final = [MP3, WAVE]

#: Signatures used *only* to write a helpful rejection message. No parser is
#: involved, and nothing here decides whether a file is accepted — a file that
#: reaches this point has already failed to parse as WAV or MP3.
_KNOWN_CONTAINERS: Final[tuple[tuple[int, bytes, str], ...]] = (
    (0, b"fLaC", "FLAC"),
    (0, b"OggS", "Ogg"),
    (4, b"ftyp", "M4A/MP4"),
    (0, b"\x1aE\xdf\xa3", "Matroska or WebM"),
    (0, b"FORM", "AIFF"),
    (0, b"\xff\xf1", "AAC (ADTS)"),
    (0, b"\xff\xf9", "AAC (ADTS)"),
)

#: Enough to cover every signature above, and small enough that a hostile file
#: cannot make us allocate anything meaningful.
_SIGNATURE_BYTES: Final = 16

_UNREADABLE_MESSAGE: Final = (
    "This file could not be read as WAV or MP3 audio. "
    "It may be corrupted, incomplete, or not an audio file."
)


def _read_signature(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(_SIGNATURE_BYTES)


def _identify_container(path: Path) -> str | None:
    """Name a well-known container, for messaging only. ``None`` if unrecognised."""
    try:
        header = _read_signature(path)
    except OSError:  # pragma: no cover - the caller just wrote this file
        return None
    return next(
        (
            label
            for offset, magic, label in _KNOWN_CONTAINERS
            if header[offset : offset + len(magic)] == magic
        ),
        None,
    )


def _rejected(path: Path, *, reason: str) -> ApiError:
    """Build the client-facing rejection for a file that would not parse.

    Neither the filesystem path nor the parser's own exception text reaches the
    client; ``reason`` is for logs only.
    """
    container = _identify_container(path)
    logger.info(
        "audio_metadata_rejected",
        extra={"reason": reason, "detected_container": container or "unknown"},
    )
    if container is not None:
        return ApiError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"{container} files are not supported yet. Please upload a WAV or MP3 file.",
        )
    return ApiError(ErrorCode.CORRUPTED_AUDIO, _UNREADABLE_MESSAGE)


def _parse(path: Path) -> Any:
    """Run the restricted parser set, converting every failure mode to ApiError."""
    try:
        parsed = mutagen.File(path, options=_PARSERS)
    except MutagenError as exc:
        raise _rejected(path, reason=type(exc).__name__) from exc
    except Exception as exc:
        # A parser bug must not surface as a 500. Logged with a traceback so it
        # stays visible to us while the client still gets a useful answer.
        logger.warning(
            "audio_metadata_parser_error",
            extra={"reason": type(exc).__name__},
            exc_info=True,
        )
        raise _rejected(path, reason=f"unexpected:{type(exc).__name__}") from exc

    if parsed is None:
        # No parser recognised the file — mutagen reports this by returning None
        # rather than raising.
        raise _rejected(path, reason="unrecognised")
    return parsed


def _positive_float(value: object) -> float | None:
    """Return a usable positive, finite float, or ``None`` if the value is unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number)


def _format_of(parsed: object) -> AudioFormat:
    if isinstance(parsed, MP3):
        return AudioFormat.MP3
    if isinstance(parsed, WAVE):
        return AudioFormat.WAV
    # Unreachable while _PARSERS holds exactly these two.
    raise ApiError(ErrorCode.CORRUPTED_AUDIO, _UNREADABLE_MESSAGE)  # pragma: no cover


def extract_metadata(path: Path) -> AudioMetadata:
    """Read basic audio properties from ``path``.

    The file is opened read-only and never modified, executed or fully loaded.

    Raises:
        ApiError: ``UNSUPPORTED_FORMAT`` when the file is a recognisable but
            unsupported container, ``CORRUPTED_AUDIO`` when it cannot be parsed
            as WAV or MP3 or its declared properties are impossible.
        OSError: when ``path`` is not a readable regular file. That is an
            internal fault rather than a bad upload, so it is deliberately not
            an ``ApiError``.
    """
    if not path.is_file():
        # Also keeps a FIFO out: reading one would block forever.
        raise OSError(f"not a regular file: {path}")

    parsed = _parse(path)
    info = parsed.info

    duration = _positive_float(getattr(info, "length", None))
    sample_rate = _positive_int(getattr(info, "sample_rate", None))
    channels = _positive_int(getattr(info, "channels", None))

    if duration is None or sample_rate is None or channels is None:
        missing = [
            name
            for name, value in (
                ("duration", duration),
                ("sample_rate", sample_rate),
                ("channels", channels),
            )
            if value is None
        ]
        # The container parsed but describes something that cannot exist —
        # a forged or damaged header. That is a malformed file, so it shares
        # CORRUPTED_AUDIO rather than earning a near-duplicate code.
        raise _rejected(path, reason=f"invalid_properties:{','.join(missing)}")

    return AudioMetadata(
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        format=_format_of(parsed),
        bits_per_sample=_positive_int(getattr(info, "bits_per_sample", None)),
    )
