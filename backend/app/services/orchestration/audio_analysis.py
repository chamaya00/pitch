"""The audio-analysis workflow.

::

    recording → stored audio → deterministic measurement → persisted result

Structurally the same as ``orchestration/analysis.py`` — same ``start``/``run``
split, same idempotency rules, same staleness sweep — and separate from it
because the two pipelines answer different questions and must be re-runnable
independently. A recording can have a speech analysis, an audio analysis, both,
or neither, and neither one's failure affects the other.

Two differences from the speech pipeline, both consequences of there being no
provider involved:

* **The work is CPU-bound, not I/O-bound.** It runs in a worker thread rather
  than on the event loop, so a five-minute recording cannot stall every other
  request while it is measured.
* **There is no partial success.** Speech analysis can complete without
  feedback, because feedback is a separate provider that may be down. Here the
  measurement either produced numbers or it did not.

Nothing in this module imports numpy or a decoder: it depends on the
:class:`AudioAnalyzer` protocol, so a test drives the whole workflow with a stub
in microseconds.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.ai.errors import ProviderError
from app.services.ai.protocols import AudioFeedbackProvider
from app.services.audio.storage import RecordingStorage, StorageError
from app.services.audio_analysis.analyzer import AudioAnalyzer
from app.services.audio_analysis.errors import AudioAnalysisError
from app.services.audio_analysis.feedback_payload import build_request
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
    NoteSummary,
    new_audio_analysis_id,
)
from app.services.audio_analysis.notes import frame_duration_seconds, summarise_notes
from app.services.audio_analysis.repository import (
    AudioAnalysisRepository,
    AudioAnalysisRepositoryError,
)
from app.services.recordings.models import utc_now
from app.services.recordings.repository import RecordingRepository

logger = get_logger(__name__)

#: Statuses that mean an analysis is in flight and a second must not start.
ACTIVE_STATUSES: Final[frozenset[AudioAnalysisStatus]] = frozenset(
    {AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING}
)

#: Serialises the find-or-create decision. Same reasoning, and the same known
#: limit, as the lock in ``orchestration/analysis.py``: it is not a substitute
#: for a database constraint, and a second worker process reopens the race.
_START_LOCK: Final = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class StartedAudioAnalysis:
    """What :meth:`AudioAnalysisService.start` decided.

    ``created`` tells a caller whether *it* caused the analysis, and therefore
    whether it is the one that should schedule the work.
    """

    analysis: AudioAnalysis
    created: bool


def _revalidated(analysis: AudioAnalysis, **changes: Any) -> AudioAnalysis:
    """Return a copy with ``changes`` applied, run back through the validators.

    Deliberately not ``model_copy(update=...)``, which skips them and would
    happily produce a "failed" record with no error code.
    """
    data = analysis.model_dump()
    data.update(changes)
    return AudioAnalysis.model_validate(data)


class AudioAnalysisService:
    """Coordinates one audio analysis from recording id to persisted result."""

    def __init__(
        self,
        *,
        recordings: RecordingRepository,
        storage: RecordingStorage,
        analyses: AudioAnalysisRepository,
        analyzer: AudioAnalyzer,
        feedback: AudioFeedbackProvider,
        stale_after_seconds: float,
    ) -> None:
        self._recordings = recordings
        self._storage = storage
        self._analyses = analyses
        self._analyzer = analyzer
        self._feedback = feedback
        self._stale_after = timedelta(seconds=stale_after_seconds)

    # --- Entry points ------------------------------------------------------

    async def analyze(self, recording_id: str) -> AudioAnalysis:
        """Start an audio analysis and run it to completion."""
        started = await self.start(recording_id)
        if not started.created:
            return started.analysis
        return await self.run(started.analysis.audio_analysis_id)

    def current(self, recording_id: str) -> AudioAnalysis | None:
        """The most recent audio analysis of a recording, in any state.

        Returns a failed record too: a caller asking "how did it go?" needs the
        failure, where a caller asking to analyse needs a fresh attempt.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown. The
                stored audio is not required — a finished analysis stays
                readable even once the bytes are gone.
        """
        if self._recordings.get(recording_id) is None:
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")

        for analysis in self._analyses.list_for_recording(recording_id):
            return analysis
        return None

    def notes(self, recording_id: str) -> tuple[NoteSummary, ...] | None:
        """The note breakdown of a recording's completed audio analysis.

        Derived from the stored pitch timeline on read rather than persisted
        alongside it: it is a pure function of points that are already on disk,
        and storing it too would be a second copy to keep consistent for a
        computation that costs one pass over a few thousand values.

        ``None`` when there is no completed analysis to break down — which the
        caller must distinguish from an empty tuple, meaning the analysis
        finished and found no notes.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown.
        """
        analysis = self.current(recording_id)
        if analysis is None or analysis.status is not AudioAnalysisStatus.COMPLETED:
            return None
        if analysis.metrics is None:
            return None

        settings = analysis.metrics.settings
        return summarise_notes(
            analysis.pitch_points,
            frame_seconds=frame_duration_seconds(
                settings.hop_length_samples, settings.sample_rate_hz
            ),
        )

    async def start_feedback(self, recording_id: str) -> AudioAnalysis:
        """Claim a completed analysis for feedback generation.

        Returns the record to work with. ``feedback_status`` says what happened:
        ``generating`` means this call claimed it and the caller should schedule
        :meth:`run_feedback`; anything else means it was already claimed,
        already written, or is being retried after a failure.

        Raises:
            ApiError: the recording is unknown, has no completed audio analysis,
                or the analysis found no reliable pitch. **A provider is never
                called for a recording with nothing to interpret** — that is how
                ordinary speech avoids being handed back a vocal assessment.
        """
        analysis = self._require_completed(recording_id)

        if analysis.feedback_status in (
            AudioFeedbackStatus.COMPLETED,
            AudioFeedbackStatus.GENERATING,
        ):
            return analysis

        claimed = self._save(
            _revalidated(
                analysis,
                feedback_status=AudioFeedbackStatus.GENERATING,
                feedback=None,
                feedback_error_code=None,
            )
        )
        logger.info(
            "audio_feedback_started",
            extra={
                "audio_analysis_id": claimed.audio_analysis_id,
                "recording_id": recording_id,
                "provider": self._feedback.name,
            },
        )
        return claimed

    async def run_feedback(self, audio_analysis_id: str) -> AudioAnalysis:
        """Generate the feedback for a claimed analysis.

        Never raises for a provider failure: it is recorded on the record and
        the record returned, so a background task cannot take the process down
        — and the measurements stay exactly where they were. A failure here
        costs the prose and nothing else.
        """
        analysis = self._analyses.get(audio_analysis_id)
        if analysis is None or analysis.metrics is None:
            raise ApiError(ErrorCode.AUDIO_ANALYSIS_NOT_FOUND, "That analysis could not be found.")
        if analysis.feedback_status is not AudioFeedbackStatus.GENERATING:
            # Already written, or never claimed. Either way there is nothing to
            # do, and doing it anyway would be a second paid provider call.
            return analysis

        notes = self.notes(analysis.recording_id) or ()
        request = build_request(analysis.metrics, notes)

        try:
            feedback = await self._feedback.interpret_audio(request)
        except ProviderError as exc:
            return self._fail_feedback(analysis, exc.error_code, **exc.log_context())
        except asyncio.CancelledError:
            self._fail_feedback(analysis, ErrorCode.INTERNAL_ERROR, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            return self._fail_feedback(
                analysis, ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

        completed = self._save(
            _revalidated(
                self._latest(analysis),
                feedback=feedback.model_dump(),
                feedback_status=AudioFeedbackStatus.COMPLETED,
                feedback_error_code=None,
            )
        )
        logger.info(
            "audio_feedback_completed",
            extra={
                "audio_analysis_id": completed.audio_analysis_id,
                "recording_id": completed.recording_id,
                "provider": feedback.provenance.provider,
                "is_mock": feedback.provenance.is_mock,
            },
        )
        return completed

    def _require_completed(self, recording_id: str) -> AudioAnalysis:
        """The completed analysis to interpret, or a documented refusal."""
        analysis = self.current(recording_id)
        if analysis is None:
            raise ApiError(
                ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
                "That recording's audio has not been analysed yet.",
            )
        if analysis.status is AudioAnalysisStatus.FAILED:
            code = analysis.error_code or ErrorCode.AUDIO_ANALYSIS_FAILED
            if code is ErrorCode.INSUFFICIENT_PITCH_SIGNAL:
                raise ApiError(
                    ErrorCode.INSUFFICIENT_PITCH_SIGNAL,
                    "Audio feedback is unavailable because there was not enough "
                    "reliable pitch information in this recording.",
                )
            raise ApiError(code, "This recording's audio could not be analysed.")
        if analysis.status is not AudioAnalysisStatus.COMPLETED or analysis.metrics is None:
            raise ApiError(
                ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
                "That recording's audio analysis has not finished yet.",
            )
        return analysis

    def _fail_feedback(
        self, analysis: AudioAnalysis, code: ErrorCode, **context: str
    ) -> AudioAnalysis:
        """Record a feedback failure. The measurements are never touched."""
        failed = _revalidated(
            self._latest(analysis),
            feedback=None,
            feedback_status=AudioFeedbackStatus.FAILED,
            feedback_error_code=code,
        )
        try:
            failed = self._save(failed)
        except AudioAnalysisRepositoryError:
            logger.error(
                "audio_feedback_failure_not_persisted",
                extra={"audio_analysis_id": failed.audio_analysis_id, "error_code": code.value},
            )
        logger.warning(
            "audio_feedback_failed",
            extra={
                "audio_analysis_id": failed.audio_analysis_id,
                "recording_id": failed.recording_id,
                "error_code": code.value,
                **context,
            },
        )
        return failed

    async def start(self, recording_id: str) -> StartedAudioAnalysis:
        """Return the analysis to work with, creating one only if needed.

        In order: one already in flight is returned; one already completed is
        returned; otherwise — including after a failure — a new ``pending``
        record is created. A failed analysis is therefore retried by calling
        this again, and the failed record stays on disk and inspectable.
        """
        self._require_recording(recording_id)

        async with _START_LOCK:
            existing = self._existing_for(recording_id)
            if existing is not None:
                logger.info(
                    "audio_analysis_reused",
                    extra={
                        "audio_analysis_id": existing.audio_analysis_id,
                        "recording_id": recording_id,
                        "status": existing.status.value,
                    },
                )
                return StartedAudioAnalysis(analysis=existing, created=False)

            analysis = self._analyses.create(
                AudioAnalysis(audio_analysis_id=new_audio_analysis_id(), recording_id=recording_id)
            )

        logger.info(
            "audio_analysis_created",
            extra={
                "audio_analysis_id": analysis.audio_analysis_id,
                "recording_id": recording_id,
            },
        )
        return StartedAudioAnalysis(analysis=analysis, created=True)

    async def run(self, audio_analysis_id: str) -> AudioAnalysis:
        """Execute a pending analysis to a terminal state.

        Never raises for a measurement failure: it is persisted on the record
        and the record returned, so a background task cannot take the process
        down. Cancellation is persisted and then re-raised.
        """
        analysis = self._analyses.get(audio_analysis_id)
        if analysis is None:
            raise ApiError(ErrorCode.AUDIO_ANALYSIS_NOT_FOUND, "That analysis could not be found.")
        if analysis.status is not AudioAnalysisStatus.PENDING:
            # Already finished, or already claimed. Refusing here means even a
            # caller who schedules twice cannot measure the same file twice.
            return analysis

        try:
            return await self._execute(analysis)
        except AudioAnalysisError as exc:
            return self._fail(self._latest(analysis), exc.error_code, **exc.log_context())
        except asyncio.CancelledError:
            self._fail(self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            return self._fail(
                self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

    # --- The pipeline ------------------------------------------------------

    async def _execute(self, analysis: AudioAnalysis) -> AudioAnalysis:
        audio_path = self._resolve_audio(analysis.recording_id)

        analysis = self._save(
            _revalidated(analysis, status=AudioAnalysisStatus.ANALYZING, started_at=utc_now())
        )

        # Off the event loop: this is numpy over the whole file, and running it
        # inline would block every other request for the duration.
        result = await asyncio.to_thread(self._analyzer.analyze, audio_path)

        completed = self._save(
            _revalidated(
                analysis,
                status=AudioAnalysisStatus.COMPLETED,
                metrics=result.metrics.model_dump(),
                pitch_points=[point.model_dump() for point in result.pitch_points],
                completed_at=utc_now(),
            )
        )
        logger.info(
            "audio_analysis_completed",
            extra={
                "audio_analysis_id": completed.audio_analysis_id,
                "recording_id": completed.recording_id,
                "voiced_frames": result.metrics.stability.voiced_frames,
                "total_frames": result.metrics.stability.total_frames,
            },
        )
        return completed

    # --- Helpers -----------------------------------------------------------

    def _require_recording(self, recording_id: str) -> None:
        if self._recordings.get(recording_id) is None or not self._storage.exists(recording_id):
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")

    def _resolve_audio(self, recording_id: str) -> Path:
        """The stored path, built by storage from a server-generated id.

        Never assembled here and never derived from a filename: the id is the
        only thing that reaches the filesystem.
        """
        try:
            return self._storage.path_for(recording_id)
        except StorageError as exc:
            raise ApiError(
                ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found."
            ) from exc

    def _existing_for(self, recording_id: str) -> AudioAnalysis | None:
        """The analysis a caller should be given back, if any.

        Sweeps abandoned records on the way past: one that has been "analyzing"
        since before the staleness horizon belongs to a process that is no
        longer running, and leaving it active would make the recording
        permanently unanalysable.
        """
        for analysis in self._analyses.list_for_recording(recording_id):
            if analysis.status in ACTIVE_STATUSES:
                if self._is_stale(analysis):
                    self._fail(analysis, ErrorCode.INTERNAL_ERROR, reason="abandoned")
                    continue
                return analysis
            if analysis.status is AudioAnalysisStatus.COMPLETED:
                return analysis
        return None

    def _is_stale(self, analysis: AudioAnalysis) -> bool:
        started = analysis.started_at or analysis.created_at
        return datetime.now(UTC) - started > self._stale_after

    def _save(self, analysis: AudioAnalysis) -> AudioAnalysis:
        return self._analyses.update(analysis)

    def _latest(self, analysis: AudioAnalysis) -> AudioAnalysis:
        """The most recently persisted state, so a failure keeps its timestamps."""
        try:
            return self._analyses.get(analysis.audio_analysis_id) or analysis
        except AudioAnalysisRepositoryError:
            return analysis

    def _fail(self, analysis: AudioAnalysis, code: ErrorCode, **context: str) -> AudioAnalysis:
        """Persist a terminal failure. The recording is never touched."""
        failed = _revalidated(
            analysis,
            status=AudioAnalysisStatus.FAILED,
            error_code=code,
            completed_at=utc_now(),
        )
        try:
            failed = self._save(failed)
        except AudioAnalysisRepositoryError:
            # The store is the thing that broke. Report the original failure
            # rather than replacing it with a write error nobody asked about.
            logger.error(
                "audio_analysis_failure_not_persisted",
                extra={
                    "audio_analysis_id": failed.audio_analysis_id,
                    "error_code": code.value,
                },
            )
        logger.warning(
            "audio_analysis_failed",
            extra={
                "audio_analysis_id": failed.audio_analysis_id,
                "recording_id": failed.recording_id,
                "error_code": code.value,
                **context,
            },
        )
        return failed
