"""Structured API error handling.

Every failure leaving this API — raised deliberately, produced by request
validation, or entirely unexpected — is rendered as the same envelope:

    {"status": "failed", "error_code": "AUDIO_TOO_LONG", "message": "..."}

Clients branch on ``error_code``; ``message`` is for humans and may be reworded.
Analysis failing is an expected outcome, not a crash, so the handlers below are
what keep a bad audio file from ever surfacing as a 500.
"""

from enum import StrEnum
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.schemas.error import ErrorResponse

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    """The API's error vocabulary.

    Codes are documented in ``docs/api.md``. A code is added here when the
    phase that raises it is designed, so the frontend and the docs can agree on
    the contract before the implementation lands.
    """

    # Generic
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Audio upload (Phase 1)
    INVALID_FILENAME = "INVALID_FILENAME"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    AUDIO_TOO_SHORT = "AUDIO_TOO_SHORT"
    AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
    CORRUPTED_AUDIO = "CORRUPTED_AUDIO"
    RECORDING_NOT_FOUND = "RECORDING_NOT_FOUND"

    # Analysis (Phase 2)
    INSUFFICIENT_PITCH_SIGNAL = "INSUFFICIENT_PITCH_SIGNAL"

    # AI feedback (Phase 6)
    AI_UNAVAILABLE = "AI_UNAVAILABLE"

    # Speech analysis (Phase 7)
    #: No provider is configured, so nothing can be transcribed. This is a
    #: deployment state, not a failure of the recording.
    ANALYSIS_NOT_CONFIGURED = "ANALYSIS_NOT_CONFIGURED"
    #: A configured provider could not be reached at all.
    ANALYSIS_PROVIDER_UNAVAILABLE = "ANALYSIS_PROVIDER_UNAVAILABLE"
    #: A provider was reached but did not answer within the allowed time.
    ANALYSIS_PROVIDER_TIMEOUT = "ANALYSIS_PROVIDER_TIMEOUT"
    #: A provider refused the request because of its own rate limits.
    ANALYSIS_RATE_LIMITED = "ANALYSIS_RATE_LIMITED"
    #: A provider answered, but with an error or an unusable response.
    ANALYSIS_PROVIDER_ERROR = "ANALYSIS_PROVIDER_ERROR"
    #: Transcription succeeded and found no speech. Not an error on our side.
    TRANSCRIPT_EMPTY = "TRANSCRIPT_EMPTY"


#: Default HTTP status for each code. Every member of ``ErrorCode`` must appear
#: here; ``test_errors.py`` enforces that so a new code cannot be added without
#: deciding what it means over HTTP.
STATUS_BY_CODE: Final[dict[ErrorCode, int]] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.HTTP_ERROR: 400,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.INVALID_FILENAME: 400,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.FORMAT_MISMATCH: 400,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.AUDIO_TOO_SHORT: 422,
    ErrorCode.AUDIO_TOO_LONG: 422,
    ErrorCode.CORRUPTED_AUDIO: 422,
    ErrorCode.RECORDING_NOT_FOUND: 404,
    ErrorCode.INSUFFICIENT_PITCH_SIGNAL: 422,
    ErrorCode.AI_UNAVAILABLE: 503,
    ErrorCode.ANALYSIS_NOT_CONFIGURED: 503,
    ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE: 503,
    ErrorCode.ANALYSIS_PROVIDER_TIMEOUT: 504,
    ErrorCode.ANALYSIS_RATE_LIMITED: 429,
    ErrorCode.ANALYSIS_PROVIDER_ERROR: 502,
    ErrorCode.TRANSCRIPT_EMPTY: 422,
}

_STATUS_FALLBACK: Final[dict[int, ErrorCode]] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}

#: Returned instead of the real exception text, which may contain internals.
_INTERNAL_MESSAGE: Final = "Something went wrong on our side. Please try again."


class ApiError(Exception):
    """A failure that should be reported to the client as a known error code.

    Raise this from routes and services rather than ``HTTPException`` so the
    status code stays a property of the error's meaning instead of being
    restated at every call site.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code if status_code is not None else STATUS_BY_CODE[code]

    def to_response_model(self) -> ErrorResponse:
        return ErrorResponse(error_code=self.code.value, message=self.message)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ApiError(code={self.code.value!r}, status_code={self.status_code})"


def _render(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error_code=code.value, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _log(request: Request, status_code: int, code: ErrorCode) -> None:
    """Record the failure without ever touching the request body.

    Uploaded audio and user data live in the body, so only the route, method and
    outcome are logged.
    """
    event = "api_error" if status_code < 500 else "api_error_unhandled"
    context = {
        "error_code": code.value,
        "status_code": status_code,
        "method": request.method,
        "path": request.url.path,
    }
    if status_code >= 500:
        logger.error(event, extra=context, exc_info=True)
    else:
        logger.warning(event, extra=context)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a deliberately raised :class:`ApiError`."""
    if not isinstance(exc, ApiError):  # pragma: no cover - registration guarantees the type
        raise exc
    _log(request, exc.status_code, exc.code)
    return _render(exc.status_code, exc.code, exc.message)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI request-validation failures in the standard envelope.

    FastAPI's default payload is a ``detail`` list shaped for API developers.
    The per-field detail is dropped here on purpose: it can echo submitted
    values back to the client, and the frontend renders ``message`` anyway.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    code = ErrorCode.VALIDATION_ERROR
    _log(request, STATUS_BY_CODE[code], code)
    return _render(
        STATUS_BY_CODE[code],
        code,
        "The request was not valid. Please check the submitted fields and try again.",
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render framework-raised ``HTTPException``s (unknown route, bad method)."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    code = _STATUS_FALLBACK.get(exc.status_code, ErrorCode.HTTP_ERROR)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed."
    _log(request, exc.status_code, code)
    return _render(exc.status_code, code, message)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: never leak internals, never return an unstructured 500."""
    code = ErrorCode.INTERNAL_ERROR
    _log(request, STATUS_BY_CODE[code], code)
    return _render(STATUS_BY_CODE[code], code, _INTERNAL_MESSAGE)


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler on the application."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
