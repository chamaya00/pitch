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

**Concurrency is the database's job, not this module's.** Step 7M removed the
``asyncio.Lock`` that used to guard the find-or-create decision. It could only
ever serialise coroutines inside one interpreter — two worker processes reopened
the race, and its own docstring admitted as much. What replaces it is two
database properties:

* a partial unique index over ``recording_id`` where the status is non-terminal,
  so a second insert for a recording already being analysed fails outright; and
* compare-and-set on every write, so a worker that resumes after its record was
  swept cannot overwrite the result that replaced it.

Both hold across processes and machines, which is precisely what the lock could
not do. Every ownership check is a ``WHERE`` clause for the same reason.
"""

import asyncio
import uuid
from dataclasses import dataclass
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
from app.services.analysis.postgres_repository import (
    ActiveAnalysisExistsError,
    AnalysisConflictError,
    AsyncAnalysisRepository,
)
from app.services.audio.storage import RecordingStorage, StorageError
from app.services.recordings.models import utc_now
from app.services.recordings.postgres_repository import OwnedRecordingRepository

logger = get_logger(__name__)

#: Statuses that mean an analysis is in flight and a second one must not start.
#: The same set the ``speech_analyses_one_active_idx`` predicate names.
ACTIVE_STATUSES: Final[frozenset[AnalysisStatus]] = frozenset(
    {AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING}
)


@dataclass(frozen=True, slots=True)
class StartedAnalysis:
    """What :meth:`AnalysisService.start` decided.

    ``created`` is the whole point: only a caller that knows it caused a *new*
    analysis may schedule the work. Without it a caller would have to compare
    timestamps or re-derive the idempotency rules to tell a record it just
    created from one that was already in flight, and scheduling the second kind
    would run a paid provider call twice over the same recording.
    """

    analysis: Analysis
    created: bool


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
        recordings: OwnedRecordingRepository,
        storage: RecordingStorage,
        analyses: AsyncAnalysisRepository,
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

    async def analyze(self, recording_id: str, owner_id: uuid.UUID) -> Analysis:
        """Start an analysis and run it to completion.

        Returns an existing analysis untouched when one is already in flight or
        already finished — see :meth:`start`.
        """
        started = await self.start(recording_id, owner_id)
        if not started.created:
            return started.analysis
        return await self.run(started.analysis.analysis_id)

    async def current(self, recording_id: str, owner_id: uuid.UUID) -> Analysis | None:
        """The most recent analysis of a recording, whatever state it is in.

        Read-only, and deliberately different from what :meth:`start` looks at:
        this one returns a ``failed`` analysis too, because a caller asking
        "how did the analysis go?" needs the failure, while a caller asking to
        analyse the recording needs a fresh attempt.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown **or
                belongs to someone else** — the two are deliberately the same
                answer, because distinguishing them would confirm the existence
                of a recording to somebody with no right to know. The stored
                audio is not required: an analysis that already ran stays
                readable even if the recording's bytes were since removed.
        """
        await self._require_owned(recording_id, owner_id)
        return await self._analyses.latest_for_recording(recording_id)

    async def start(self, recording_id: str, owner_id: uuid.UUID) -> StartedAnalysis:
        """Return the analysis to work with, creating one only if needed.

        The idempotency rule, in order:

        * an analysis already in flight → return it, start nothing
        * an analysis already completed → return it, start nothing
        * otherwise (including after a failure) → a new ``pending`` record

        A "failed" analysis is therefore retried by calling this again, which
        produces a *new* record; the failed one stays stored and inspectable.

        The read below is an optimisation, not the guarantee. Two callers can
        both find nothing and both try to insert; the unique index lets exactly
        one through and the loser is handed the winner's record. That is why
        this holds with several worker processes, where a lock would not.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown or is
                not this owner's. No analysis record is created — there is
                nothing to analyse, and a record pointing at a recording that
                does not exist would be a lie in the database.
        """
        await self._require_recording(recording_id, owner_id)

        existing = await self._existing_for(recording_id)
        if existing is not None:
            return self._reused(existing, recording_id)

        try:
            analysis = await self._analyses.create(
                Analysis(analysis_id=new_analysis_id(), recording_id=recording_id)
            )
        except ActiveAnalysisExistsError:
            # Lost the insert race. Whoever won has a record for this recording;
            # returning theirs is the same answer this call would have given a
            # moment earlier.
            existing = await self._analyses.latest_for_recording(recording_id)
            if existing is None:  # pragma: no cover - the index only fires if a row exists
                raise ApiError(
                    ErrorCode.INTERNAL_ERROR, "That analysis could not be started."
                ) from None
            return self._reused(existing, recording_id)

        logger.info(
            "analysis_created",
            extra={
                "analysis_id": analysis.analysis_id,
                "recording_id": recording_id,
                "speech_to_text_provider": self._speech_to_text.name,
                "feedback_provider": self._feedback.name,
            },
        )
        return StartedAnalysis(analysis=analysis, created=True)

    async def run(self, analysis_id: str) -> Analysis:
        """Execute a pending analysis to a terminal state.

        Takes no owner: ownership was established by :meth:`start`, and this is
        the continuation of that decision rather than a new request. It is
        reachable only from a background task the API scheduled itself.

        Never raises for a provider failure: the failure is persisted on the
        record and the record is returned, so a background task cannot take the
        process down with it. Cancellation is the one exception — it is
        persisted and then re-raised, because swallowing it would leave the
        event loop believing the task is still running.
        """
        analysis = await self._analyses.get(analysis_id)
        if analysis is None:
            raise ApiError(ErrorCode.ANALYSIS_NOT_FOUND, "That analysis could not be found.")
        if analysis.status is not AnalysisStatus.PENDING:
            # Already finished, or already being worked on. Refusing here means
            # that even a caller who schedules the same analysis twice cannot
            # make two provider calls happen — the second one finds the record
            # claimed and stops.
            return analysis

        try:
            return await self._execute(analysis)
        except AnalysisConflictError:
            # Another worker claimed this record between the read above and the
            # first write. It owns the run now; report what is actually stored.
            return await self._latest(analysis)
        except ProviderError as exc:
            return await self._fail(
                await self._latest(analysis), exc.error_code, **exc.log_context()
            )
        except asyncio.CancelledError:
            await self._fail(
                await self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason="cancelled"
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            # Anything unforeseen still leaves an honest record behind.
            return await self._fail(
                await self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

    def _reused(self, existing: Analysis, recording_id: str) -> StartedAnalysis:
        logger.info(
            "analysis_reused",
            extra={
                "analysis_id": existing.analysis_id,
                "recording_id": recording_id,
                "status": existing.status.value,
            },
        )
        return StartedAnalysis(analysis=existing, created=False)

    # --- The pipeline ------------------------------------------------------

    async def _execute(self, analysis: Analysis) -> Analysis:
        audio_path = self._resolve_audio(analysis.recording_id)

        # Claiming the record *is* this write: the update requires the stored
        # status to still be "pending", so a second worker that read the same
        # pending record loses here rather than making a second paid call.
        analysis = await self._save(
            _revalidated(analysis, status=AnalysisStatus.TRANSCRIBING, started_at=utc_now()),
            expect_status=AnalysisStatus.PENDING,
        )
        transcript = await self._speech_to_text.transcribe(audio_path)

        metrics = compute_metrics(
            transcript,
            recording_duration_seconds=await self._recording_duration(analysis.recording_id),
        )
        analysis = await self._save(
            _revalidated(
                analysis,
                status=AnalysisStatus.ANALYZING,
                transcript=transcript.model_dump(),
                metrics=metrics.model_dump(),
            ),
            expect_status=AnalysisStatus.TRANSCRIBING,
        )

        feedback = await self._try_feedback(analysis, transcript, metrics)

        completed = await self._save(
            _revalidated(
                analysis,
                status=AnalysisStatus.COMPLETED,
                feedback=feedback.model_dump() if feedback is not None else None,
                completed_at=utc_now(),
            ),
            expect_status=AnalysisStatus.ANALYZING,
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

    async def _require_owned(self, recording_id: str, owner_id: uuid.UUID) -> None:
        """Refuse a recording that is unknown *or* is not this owner's.

        The repository does the check in SQL — a recording belonging to someone
        else is never selected, rather than selected and filtered — so no caller
        can reach past it, and no frontend is trusted to hide anything.
        """
        if await self._recordings.get(recording_id, owner_id) is None:
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")

    async def _require_recording(self, recording_id: str, owner_id: uuid.UUID) -> None:
        await self._require_owned(recording_id, owner_id)
        if not self._storage.exists(recording_id):
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

    async def _recording_duration(self, recording_id: str) -> float | None:
        """The duration measured at upload, used only if the provider gave none.

        Read by id alone. Ownership was checked before the run started, and this
        is the same recording the run is already measuring.
        """
        owner_id = await self._recordings.owner_of(recording_id)
        if owner_id is None:
            return None
        recording = await self._recordings.get(recording_id, owner_id)
        return recording.duration_seconds if recording is not None else None

    async def _existing_for(self, recording_id: str) -> Analysis | None:
        """The analysis a caller should be given back, if any.

        Sweeps abandoned records on the way past: an analysis that has been
        "transcribing" since before the staleness horizon belongs to a process
        that is no longer running, and leaving it active would make the
        recording permanently unanalysable.
        """
        for analysis in await self._analyses.list_for_recording(recording_id):
            if analysis.status in ACTIVE_STATUSES:
                if self._is_stale(analysis):
                    await self._fail(analysis, ErrorCode.INTERNAL_ERROR, reason="abandoned")
                    continue
                return analysis
            if analysis.status is AnalysisStatus.COMPLETED:
                return analysis
        return None

    def _is_stale(self, analysis: Analysis) -> bool:
        started = analysis.started_at or analysis.created_at
        return datetime.now(UTC) - started > self._stale_after

    async def _save(self, analysis: Analysis, *, expect_status: AnalysisStatus) -> Analysis:
        """Persist a transition, but only from the status the caller last read.

        Raises:
            AnalysisConflictError: the stored record has moved on, so this
                worker is no longer the one running the analysis.
        """
        return await self._analyses.update(analysis, expect_status=expect_status)

    async def _latest(self, analysis: Analysis) -> Analysis:
        """The most recently persisted state of a record.

        The failure handlers work from this rather than from the snapshot the
        run started with, so a failure part-way through does not roll the record
        back to "pending" and lose the timestamps and transcript already stored.
        """
        try:
            return await self._analyses.get(analysis.analysis_id) or analysis
        except Exception:  # noqa: BLE001 - a failing store must not mask the failure
            return analysis

    async def _fail(self, analysis: Analysis, code: ErrorCode, **context: str) -> Analysis:
        """Persist a terminal failure. The recording is never touched.

        A failed analysis keeps whatever it managed to produce and stays
        stored: it is the record of what went wrong, and a retry creates a new
        analysis rather than overwriting this one.
        """
        failed = _revalidated(
            analysis,
            status=AnalysisStatus.FAILED,
            error_code=code,
            completed_at=utc_now(),
        )
        try:
            failed = await self._save(failed, expect_status=analysis.status)
        except AnalysisConflictError:
            # Somebody else already moved this record — a concurrent staleness
            # sweep, or the worker that owns it. Theirs is the truth; report it.
            logger.info(
                "analysis_failure_superseded",
                extra={"analysis_id": failed.analysis_id, "error_code": code.value},
            )
            return await self._latest(analysis)
        except Exception:  # noqa: BLE001 - the store is what broke
            # Report the original failure rather than replacing it with a write
            # error nobody asked about.
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
