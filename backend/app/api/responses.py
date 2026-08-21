"""Reusable OpenAPI documentation for error responses.

Routes declare which failures they can produce; the envelope itself is
described in one place so it cannot drift between endpoints.
"""

from typing import Any

from app.core.errors import STATUS_BY_CODE, ErrorCode
from app.schemas.error import ErrorResponse

_DESCRIPTIONS: dict[ErrorCode, str] = {
    ErrorCode.INVALID_FILENAME: "The file name was missing or unusable.",
    ErrorCode.UNSUPPORTED_FORMAT: "The file type is not supported.",
    ErrorCode.FORMAT_MISMATCH: "The file extension and its actual contents disagree.",
    ErrorCode.FILE_TOO_LARGE: "The file exceeds the configured size limit.",
    ErrorCode.AUDIO_TOO_SHORT: "The recording is too short to work with.",
    ErrorCode.AUDIO_TOO_LONG: "The recording exceeds the configured duration limit.",
    ErrorCode.CORRUPTED_AUDIO: "The file could not be read as audio.",
    ErrorCode.RECORDING_NOT_FOUND: "No recording exists with that identifier.",
    ErrorCode.ANALYSIS_NOT_FOUND: "That recording has not been analysed.",
    ErrorCode.REFERENCE_NOT_FOUND: "No song reference exists with that identifier.",
    ErrorCode.VALIDATION_ERROR: "The request was not valid.",
    ErrorCode.INTERNAL_ERROR: "An unexpected error occurred.",
}


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """Build an OpenAPI ``responses`` mapping for the given error codes.

    Codes sharing a status are merged into one entry, since OpenAPI is keyed by
    status code rather than by our vocabulary.
    """
    grouped: dict[int, list[ErrorCode]] = {}
    for code in codes:
        grouped.setdefault(STATUS_BY_CODE[code], []).append(code)

    responses: dict[int | str, dict[str, Any]] = {}
    for status, status_codes in sorted(grouped.items()):
        description = " ".join(
            f"`{code.value}` — {_DESCRIPTIONS.get(code, 'Request failed.')}"
            for code in status_codes
        )
        responses[status] = {"model": ErrorResponse, "description": description}
    return responses
