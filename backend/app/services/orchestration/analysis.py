"""The analysis workflow.

::

    recording → stored audio → speech-to-text → transcript
              → deterministic metrics → feedback → persisted analysis

Everything here is expressed through the protocols from Step 7B: this module
does not know that Deepgram or Claude exist, and swapping either for a mock
changes nothing in it. It also knows nothing about HTTP — the only thing it
takes from a caller is a recording id.

**The record is written before every slow step, not after.** Each transition is
persisted first, so a process killed mid-analysis leaves a record that says
where it got to rather than vanishing. Combined with the staleness sweep below,
that is what makes an interrupted run recoverable instead of a permanent
"transcribing".

**Feedback is optional; the numbers are not.** A transcription that succeeded
and metrics that were computed are a completed analysis, even if the language
model was unreachable. The reverse never happens: prose is never stored without
the numbers it describes.

Split into :meth:`AnalysisService.start` and :meth:`AnalysisService.run` so
Step 7E can return a record immediately and hand the slow part to a background
task. :meth:`AnalysisService.analyze` runs both, which is what tests use.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.ai.errors import ProviderError
from app.services.ai.protocols import FeedbackProvider, SpeechToTextProvider
from app.services.analysis.metrics import compute_metrics
from app.services.analysis.models import (
    Analysis,
    AnalysisStatus,
    Feedback,
    SpeechMetrics,
    Transcript,
    new_analysis_id,
)
from app.services.analysis.repository import AnalysisRepository, AnalysisRepositoryError
from app.services.audio.storage import RecordingStorage, StorageError
from app.services.recordings.models import utc_now
from app.services.recordings.repository import RecordingRepository

logger = get_logger(__name__)

#: Statuses that mean an analysis is in flight and a second one must not start.
ACTIVE_STATUSES: Final[frozenset[AnalysisStatus]] = frozenset(
    {AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING}
)

#: Serialises the find-or-create decision so two concurrent requests for one
#: recording cannot both start a paid provider call.
#:
#: Today the guarded section does not await anything — the filesystem store is
#: synchronous, so the scan and the write already run to completion without
#: yielding, and the lock changes nothing. It is here for the store that
#: replaces it: the moment the repository becomes async, the section acquires
#: yield points and the invariant would quietly disappear without it.
#:
#: Process-wide and deliberately coarse — the section is a directory scan and one
#: small write, so serialising across recordings costs nothing worth measuring
#: and needs no per-key bookkeeping. It is **not** a substitute for a database
#: constraint: run more than one worker process and the race returns. That is a
#: known limit of the filesystem store, not of this design.
_START_LOCK: Final = asyncio.Lock()


def _revalidated(analysis: Analysis, **changes: Any) -> Analysis:
    """Return a copy of ``analysis`` with ``changes`` applied, revalidated.

    Deliberately not ``model_copy(update=...)``: that skips validators, so it
    would happily produce a "failed" analysis with no error code or a
    "completed" one with no metrics. Round-tripping through the model means
    every transition in this file is checked against the invariants in
    ``models.py`` rather than trusted.
    """
    data = analysis.model_dump()
    data.pop("provenance", None)  # Derived from the content; not an input.
    data.update(changes)
    return Analysis.model_validate(data)


class AnalysisService:
    """Coordinates one analysis from recording id to persisted result."""

    def __init__(
        self,
        *,
        recordings: RecordingRepository,
        storage: RecordingStorage,
        analyses: AnalysisRepository,
        speech_to_text: SpeechToTextProvider,
        feedback: FeedbackProvider,
        stale_after_seconds: float,
    ) -> None:
        self._recordings = recordings
        self._storage = storage
        self._analyses = analyses
        self._speech_to_text = speech_to_text
        self._feedback = feedback
        self._stale_after = timedelta(seconds=stale_after_seconds)

    # --- Entry points ------------------------------------------------------

    async def analyze(self, recording_id: str) -> Analysis:
        """Start an analysis and run it to completion.

        Returns an existing analysis untouched when one is already in flight or
        already finished — see :meth:`start`.
        """
        analysis = await self.start(recording_id)
        if analysis.status is not AnalysisStatus.PENDING:
            return analysis
        return await self.run(analysis.analysis_id)

    async def start(self, recording_id: str) -> Analysis:
        """Return the analysis to work with, creating one only if needed.

        The idempotency rule, in order:

        * an analysis already in flight → return it, start nothing
        * an analysis already completed → return it, start nothing
        * otherwise (including after a failure) → a new ``pending`` record

        A "failed" analysis is therefore retried by calling this again, which
        produces a *new* record; the failed one stays on disk and inspectable.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown. No
                analysis record is created — there is nothing to analyse, and a
                record pointing at a recording that does not exist would be a
                lie on disk.
        """
        self._require_recording(recording_id)

        async with _START_LOCK:
            existing = self._existing_for(recording_id)
            if existing is not None:
                logger.info(
                    "analysis_reused",
                    extra={
                        "analysis_id": existing.analysis_id,
                        "recording_id": recording_id,
                        "status": existing.status.value,
                    },
                )
                return existing

            analysis = self._analyses.create(
                Analysis(analysis_id=new_analysis_id(), recording_id=recording_id)
            )

        logger.info(
            "analysis_created",
            extra={
                "analysis_id": analysis.analysis_id,
                "recording_id": recording_id,
                "speech_to_text_provider": self._speech_to_text.name,
                "feedback_provider": self._feedback.name,
            },
        )
        return analysis

    async def run(self, analysis_id: str) -> Analysis:
        """Execute a pending analysis to a terminal state.

        Never raises for a provider failure: the failure is persisted on the
        record and the record is returned, so a background task cannot take the
        process down with it. Cancellation is the one exception — it is
        persisted and then re-raised, because swallowing it would leave the
        event loop believing the task is still running.
        """
        analysis = self._analyses.get(analysis_id)
        if analysis is None:
            raise ApiError(ErrorCode.NOT_FOUND, "That analysis could not be found.")
        if analysis.is_terminal:
            return analysis

        try:
            return await self._execute(analysis)
        except ProviderError as exc:
            return self._fail(self._latest(analysis), exc.error_code, **exc.log_context())
        except asyncio.CancelledError:
            self._fail(self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            # Anything unforeseen still leaves an honest record behind.
            return self._fail(
                self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

    # --- The pipeline ------------------------------------------------------

    async def _execute(self, analysis: Analysis) -> Analysis:
        audio_path = self._resolve_audio(analysis.recording_id)

        analysis = self._save(
            _revalidated(analysis, status=AnalysisStatus.TRANSCRIBING, started_at=utc_now())
        )
        transcript = await self._speech_to_text.transcribe(audio_path)

        metrics = compute_metrics(
            transcript,
            recording_duration_seconds=self._recording_duration(analysis.recording_id),
        )
        analysis = self._save(
            _revalidated(
                analysis,
                status=AnalysisStatus.ANALYZING,
                transcript=transcript.model_dump(),
                metrics=metrics.model_dump(),
            )
        )

        feedback = await self._try_feedback(analysis, transcript, metrics)

        completed = self._save(
            _revalidated(
                analysis,
                status=AnalysisStatus.COMPLETED,
                feedback=feedback.model_dump() if feedback is not None else None,
                completed_at=utc_now(),
            )
        )
        logger.info(
            "analysis_completed",
            extra={
                "analysis_id": completed.analysis_id,
                "recording_id": completed.recording_id,
                "word_count": metrics.word_count,
                "has_feedback": feedback is not None,
                "is_mock": completed.provenance.is_mock,
            },
        )
        return completed

    async def _try_feedback(
        self, analysis: Analysis, transcript: Transcript, metrics: SpeechMetrics
    ) -> Feedback | None:
        """Generate feedback, or return ``None`` if the provider fails.

        The provider is handed the transcript and the metrics — the protocol
        gives it nowhere to put audio, a path, a filename, a recording id or a
        credential, and this call adds nothing to that.

        A failure here degrades the analysis to numbers without prose. Turning a
        successful transcription into a total failure because a language model
        was unavailable would throw away the part of the result that is actually
        measured. The failure is logged; the domain model has no field for a
        partial failure, and inventing one to hold it would be worse than the
        log line.
        """
        try:
            return await self._feedback.generate(transcript=transcript, metrics=metrics)
        except ProviderError as exc:
            logger.warning(
                "analysis_feedback_unavailable",
                extra={"analysis_id": analysis.analysis_id, **exc.log_context()},
            )
            return None

    # --- Helpers -----------------------------------------------------------

    def _require_recording(self, recording_id: str) -> None:
        if self._recordings.get(recording_id) is None or not self._storage.exists(recording_id):
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")

    def _resolve_audio(self, recording_id: str) -> Path:
        """The stored path, produced by storage from a server-generated id.

        Never built here, and never derived from a filename: the id is the only
        thing that reaches the filesystem.
        """
        try:
            return self._storage.path_for(recording_id)
        except StorageError as exc:
            raise ApiError(
                ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found."
            ) from exc

    def _recording_duration(self, recording_id: str) -> float | None:
        """The duration measured at upload, used only if the provider gave none."""
        recording = self._recordings.get(recording_id)
        return recording.duration_seconds if recording is not None else None

    def _existing_for(self, recording_id: str) -> Analysis | None:
        """The analysis a caller should be given back, if any.

        Sweeps abandoned records on the way past: an analysis that has been
        "transcribing" since before the staleness horizon belongs to a process
        that is no longer running, and leaving it active would make the
        recording permanently unanalysable.
        """
        for analysis in self._analyses.list_for_recording(recording_id):
            if analysis.status in ACTIVE_STATUSES:
                if self._is_stale(analysis):
                    self._fail(analysis, ErrorCode.INTERNAL_ERROR, reason="abandoned")
                    continue
                return analysis
            if analysis.status is AnalysisStatus.COMPLETED:
                return analysis
        return None

    def _is_stale(self, analysis: Analysis) -> bool:
        started = analysis.started_at or analysis.created_at
        return datetime.now(UTC) - started > self._stale_after

    def _save(self, analysis: Analysis) -> Analysis:
        return self._analyses.update(analysis)

    def _latest(self, analysis: Analysis) -> Analysis:
        """The most recently persisted state of a record.

        The failure handlers work from this rather than from the snapshot the
        run started with, so a failure part-way through does not roll the record
        back to "pending" and lose the timestamps and transcript already stored.
        """
        try:
            return self._analyses.get(analysis.analysis_id) or analysis
        except AnalysisRepositoryError:
            return analysis

    def _fail(self, analysis: Analysis, code: ErrorCode, **context: str) -> Analysis:
        """Persist a terminal failure. The recording is never touched.

        A failed analysis keeps whatever it managed to produce and stays on
        disk: it is the record of what went wrong, and a retry creates a new
        analysis rather than overwriting this one.
        """
        failed = _revalidated(
            analysis,
            status=AnalysisStatus.FAILED,
            error_code=code,
            completed_at=utc_now(),
        )
        try:
            failed = self._save(failed)
        except AnalysisRepositoryError:
            # The store is the thing that broke. Report the original failure
            # rather than replacing it with a write error nobody asked about.
            logger.error(
                "analysis_failure_not_persisted",
                extra={"analysis_id": failed.analysis_id, "error_code": code.value},
            )
        logger.warning(
            "analysis_failed",
            extra={
                "analysis_id": failed.analysis_id,
                "recording_id": failed.recording_id,
                "error_code": code.value,
                **context,
            },
        )
        return failed
