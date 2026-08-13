"""Upload validation: filenames, extensions and size.

Everything here is a pure function — no filesystem access, no settings import,
no I/O of any kind. Limits are passed in by the caller (from ``Settings``) so
these functions stay trivially testable and can never drift from configuration.

Two rules govern this module:

**The client filename is display metadata only.** It is sanitized so it can be
shown back to a user, and it is *never* used to build a storage path. Storage
(Phase 1, step 4) names files from a server-generated UUID, so nothing a client
sends can influence where bytes land.

**The extension is a cheap first filter, not proof.** It rejects the obviously
wrong before a single byte is read. The authoritative check is content
sniffing, which happens in the metadata step — a file called ``song.mp3`` that
contains something else is caught there, not here.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from app.core.errors import ApiError, ErrorCode

#: Formats accepted in Phase 1. This is a capability of the code, not a
#: deployment knob: M4A/AAC stays out until Phase 2 chooses a decoding strategy,
#: and making it env-configurable would let a deployment promise support that
#: does not exist. Extensions are compared lower-cased.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".wav", ".mp3"})

#: Upper bound for the *display* name. Well under the 255-byte limit common to
#: filesystems, and long enough to stay recognisable to the person who uploaded.
MAX_FILENAME_LENGTH: Final = 120

#: Longest suffix still treated as an extension when truncating a long name.
_MAX_EXTENSION_LENGTH: Final = 10

_PATH_SEPARATORS: Final = re.compile(r"[\\/]")
_WINDOWS_RESERVED: Final = re.compile(r'[:*?"<>|]')
_WHITESPACE_RUN: Final = re.compile(r"\s+")

#: Unicode general categories stripped from filenames.
#:
#: ``Cc`` covers NUL and the other control characters. ``Cf`` covers the bidi
#: overrides (U+202E and friends) used to disguise an extension — the classic
#: "invoice‮gpj.exe" that renders as "invoice exe.jpg". ``Cs`` and ``Co``
#: are surrogates and private-use code points, which have no business in a name
#: being displayed back to a user.
_DISCARDED_CATEGORIES: Final[frozenset[str]] = frozenset({"Cc", "Cf", "Cs", "Co"})

#: Stripped from both ends: leading dots hide files on POSIX, trailing dots and
#: spaces are silently dropped by Windows, which makes them a rename trick.
_TRIMMED_EDGE_CHARS: Final = ". "


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """The result of validating an upload's metadata.

    ``filename`` is safe to display and to store as a text field. It is not a
    path and must never be treated as one.
    """

    filename: str
    extension: str
    size_bytes: int
    #: What the client claimed the type was. Advisory only — recorded for logs
    #: and never used to accept or reject anything.
    declared_content_type: str | None = None


def _is_discardable(char: str) -> bool:
    return unicodedata.category(char) in _DISCARDED_CATEGORIES


def _truncate(name: str) -> str:
    """Shorten an over-long name, keeping a short trailing extension intact."""
    if len(name) <= MAX_FILENAME_LENGTH:
        return name

    stem, dot, extension = name.rpartition(".")
    if dot and stem and 0 < len(extension) <= _MAX_EXTENSION_LENGTH:
        keep = MAX_FILENAME_LENGTH - len(extension) - 1
        if keep > 0:
            return f"{stem[:keep]}.{extension}"
    return name[:MAX_FILENAME_LENGTH]


def sanitize_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to a safe, human-readable display name.

    Raises :class:`ApiError` with ``INVALID_FILENAME`` when nothing usable
    survives — an empty result is a rejected upload, never a generated
    substitute name. Inventing one would hide from the user that we could not
    read what they sent.
    """
    if raw is None:
        raise ApiError(ErrorCode.INVALID_FILENAME, "The upload did not include a file name.")

    # NFC keeps legitimate scripts intact (unlike NFKC, which folds characters
    # and would mangle non-Latin names). Safe here because the result never
    # becomes a path.
    name = unicodedata.normalize("NFC", raw)

    # Keep only the last path segment, so "../../etc/passwd" and "..\..\win.ini"
    # both collapse to a bare name. Separators cannot survive this step.
    name = _PATH_SEPARATORS.split(name)[-1]

    # Control and format characters go before anything else looks at the name:
    # a NUL is removed rather than truncating at it, so a C library downstream
    # can never see a shorter string than we validated.
    name = "".join(char for char in name if not _is_discardable(char))

    name = _WINDOWS_RESERVED.sub("_", name)
    name = _WHITESPACE_RUN.sub(" ", name)
    name = name.strip(_TRIMMED_EDGE_CHARS)
    name = _truncate(name)

    if not name:
        raise ApiError(
            ErrorCode.INVALID_FILENAME,
            "That file name could not be read. Please rename the file and try again.",
        )
    return name


def extension_of(filename: str) -> str:
    """Return the lower-cased extension including its dot, or ``""`` if absent.

    Only the final suffix counts, so ``song.mp3.exe`` yields ``.exe``. A name
    that is only an extension (``.mp3``) has no stem and yields ``""``.
    """
    stem, dot, extension = filename.rpartition(".")
    if not dot or not stem or not extension:
        return ""
    return f".{extension.lower()}"


def validate_extension(filename: str) -> str:
    """Check the extension against the allowlist and return it, normalised.

    Case-insensitive: ``.WAV`` and ``.wav`` are the same thing.
    """
    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        supported = ", ".join(sorted(ext.lstrip(".").upper() for ext in ALLOWED_EXTENSIONS))
        raise ApiError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Only {supported} files are supported at the moment.",
        )
    return extension


def validate_size(size_bytes: int, *, max_size_bytes: int) -> None:
    """Reject anything over the limit. A file exactly at the limit is accepted.

    Zero bytes passes here — an empty file is within *any* size limit. It is
    rejected by content validation in the metadata step, which is where
    "this isn't really audio" belongs.
    """
    if size_bytes > max_size_bytes:
        limit_mb = max_size_bytes / (1024 * 1024)
        raise ApiError(
            ErrorCode.FILE_TOO_LARGE,
            f"Files must be {limit_mb:.0f} MB or smaller.",
        )


def validate_upload(
    *,
    raw_filename: str | None,
    size_bytes: int,
    max_size_bytes: int,
    declared_content_type: str | None = None,
) -> ValidatedUpload:
    """Validate an upload's metadata in one call.

    The endpoint checks the name and extension *before* reading the body, then
    enforces the size cap while streaming, so it calls the individual functions
    rather than this composition. Both paths apply identical rules.

    ``declared_content_type`` is carried through untouched; clients lie about
    it, so it never influences the outcome.
    """
    filename = sanitize_filename(raw_filename)
    extension = validate_extension(filename)
    validate_size(size_bytes, max_size_bytes=max_size_bytes)
    return ValidatedUpload(
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        declared_content_type=declared_content_type,
    )
