"""Failures raised by the deterministic audio analyzer.

Same split as ``services/ai/errors.py``: the exception's own text is the
client-facing wording, and everything diagnostic lives in
:meth:`AudioAnalysisError.log_context`. A decoder's exception message can carry
a filesystem path or a library's internal state, so it never becomes the
message a client sees — only a short classifier reaches the logs.
"""

from typing import ClassVar, Final

from app.core.errors import ApiError, ErrorCode

#: Hard bound on the logged reason, so an oversized decoder message cannot be
#: smuggled into the logs through it.
_MAX_REASON_LENGTH: Final = 120


class AudioAnalysisError(Exception):
    """Base class for every audio-analysis failure."""

    error_code: ClassVar[ErrorCode] = ErrorCode.AUDIO_ANALYSIS_FAILED
    public_message: ClassVar[str] = "This recording could not be analysed."

    def __init__(self, *, reason: str | None = None) -> None:
        super().__init__(self.public_message)
        self.reason = reason

    def to_api_error(self) -> ApiError:
        return ApiError(self.error_code, self.public_message)

    def log_context(self) -> dict[str, str]:
        context = {"error_code": self.error_code.value}
        if self.reason:
            context["reason"] = self.reason[:_MAX_REASON_LENGTH]
        return context


class AudioUnsupportedError(AudioAnalysisError):
    """The file could not be decoded, or holds no audio we can read."""

    error_code = ErrorCode.AUDIO_UNSUPPORTED
    public_message = "This recording's audio could not be read."


class AudioTooShortError(AudioAnalysisError):
    """Too little audio to measure anything from."""

    error_code = ErrorCode.AUDIO_TOO_SHORT
    public_message = "This recording is too short to analyse."


class InsufficientPitchSignalError(AudioAnalysisError):
    """The audio decoded, but no frame carried a reliable pitch.

    A normal outcome, not a bug: whispering, heavy background noise, an
    instrumental recording, or simply silence all land here. The analysis
    records it as a failure with this code so the UI can say what was actually
    missing rather than showing an empty result.
    """

    error_code = ErrorCode.INSUFFICIENT_PITCH_SIGNAL
    public_message = "We could not detect enough reliable pitch information in this recording."
