"""Failures raised by AI providers.

Providers fail in ways that are none of the user's business: an expired key, a
vendor's 502, a socket timeout, a rate limit. This module is the translation
layer. Every provider adapter raises one of these, and the orchestration layer
turns it into the matching :class:`~app.core.errors.ErrorCode`.

The split is deliberate:

* ``str(exc)`` and :meth:`ProviderError.to_api_error` produce our own generic
  wording. Vendor exception names, response bodies, URLs, request ids and
  anything else the provider said are not in it.
* :meth:`ProviderError.log_context` carries a short ``reason`` classifier for
  the logs. It is truncated, and it is never rendered into a response.

``reason`` must be a short classifier — an exception class name, an HTTP status
— and never a raw provider payload, which could contain the transcript or an
echoed credential.
"""

from typing import ClassVar, Final

from app.core.errors import ApiError, ErrorCode

#: Hard bound on the logged reason, so an oversized string cannot be smuggled
#: into the logs through it.
_MAX_REASON_LENGTH: Final = 120


class ProviderError(Exception):
    """Base class for every AI provider failure."""

    error_code: ClassVar[ErrorCode] = ErrorCode.ANALYSIS_PROVIDER_ERROR
    public_message: ClassVar[str] = "The analysis service could not complete this request."

    def __init__(self, provider: str, *, reason: str | None = None) -> None:
        # The safe message is the exception's own text, so an accidental
        # ``str(exc)`` at a call site still cannot leak provider detail.
        super().__init__(self.public_message)
        self.provider = provider
        self.reason = reason

    def to_api_error(self) -> ApiError:
        """The client-facing error for this failure."""
        return ApiError(self.error_code, self.public_message)

    def log_context(self) -> dict[str, str]:
        """Structured fields for the logs. Never used in a response body."""
        context = {"provider": self.provider, "error_code": self.error_code.value}
        if self.reason:
            context["reason"] = self.reason[:_MAX_REASON_LENGTH]
        return context


class ProviderNotConfiguredError(ProviderError):
    """No credentials or no provider selected — a deployment state, not a fault."""

    error_code = ErrorCode.ANALYSIS_NOT_CONFIGURED
    public_message = "Speech analysis is not configured on this server."


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached."""

    error_code = ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE
    public_message = "The analysis service is temporarily unavailable. Please try again shortly."


class ProviderTimeoutError(ProviderError):
    """The provider was reached but did not answer in time."""

    error_code = ErrorCode.ANALYSIS_PROVIDER_TIMEOUT
    public_message = "The analysis service took too long to respond. Please try again."


class ProviderRateLimitedError(ProviderError):
    """The provider refused the request because of its own rate limits."""

    error_code = ErrorCode.ANALYSIS_RATE_LIMITED
    public_message = "Too many analyses are in progress right now. Please try again in a moment."


class ProviderResponseError(ProviderError):
    """The provider answered with an error, or with something unusable."""

    error_code = ErrorCode.ANALYSIS_PROVIDER_ERROR


class EmptyTranscriptError(ProviderError):
    """Transcription succeeded and found no speech.

    Not a provider fault and not our fault: the recording contained nothing to
    transcribe, which the user can act on.
    """

    error_code = ErrorCode.TRANSCRIPT_EMPTY
    public_message = "No speech was detected in this recording."
