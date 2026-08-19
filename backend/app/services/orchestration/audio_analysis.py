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
import uuid
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
from app.services.audio_analysis.key import analyse_key
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioFeedbackStatus,
    AudioMetrics,
    DecimatedTimeline,
    KeyAnalysis,
    NoteSummary,
    TimelineFields,
    new_audio_analysis_id,
)
from app.services.audio_analysis.notes import frame_duration_seconds, summarise_notes
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AsyncAudioAnalysisRepository,
    AudioAnalysisConflictError,
)
from app.services.recordings.models import utc_now
from app.services.recordings.postgres_repository import OwnedRecordingRepository

logger = get_logger(__name__)

#: Statuses that mean an analysis is in flight and a second must not start.
#: The same set the ``audio_analyses_one_active_idx`` predicate names.
ACTIVE_STATUSES: Final[frozenset[AudioAnalysisStatus]] = frozenset(
    {AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING}
)


@dataclass(frozen=True, slots=True)
class StartedAudioAnalysis:
    """What :meth:`AudioAnalysisService.start` decided.

    ``created`` tells a caller whether *it* caused the analysis, and therefore
    whether it is the one that should schedule the work.

    The record is a summary: starting an analysis is a decision about state, and
    the caller that acts on it schedules :meth:`AudioAnalysisService.run` by id.
    Nothing on this path reads a timeline, so nothing on it loads one.
    """

    analysis: AudioAnalysisSummary
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
        recordings: OwnedRecordingRepository,
        storage: RecordingStorage,
        analyses: AsyncAudioAnalysisRepository,
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

    async def analyze(self, recording_id: str, owner_id: uuid.UUID) -> AudioAnalysisSummary:
        """Start an audio analysis and run it to completion."""
        started = await self.start(recording_id, owner_id)
        if not started.created:
            return started.analysis
        return await self.run(started.analysis.audio_analysis_id)

    async def current(self, recording_id: str, owner_id: uuid.UUID) -> AudioAnalysisSummary | None:
        """The most recent audio analysis of a recording, in any state, **without
        its timeline**.

        The read behind every "how did it go?" — the summary endpoint, the
        feedback state, the feedback claim — and none of those return a single
        pitch point. Loading the timeline for them cost 87 ms and 19 MB per
        request against 1.7 ms and 14 kB without it, at the longest recording
        this product accepts; the numbers and the query are in
        ``audio_analysis/postgres_repository.py``. A caller that genuinely needs
        the points asks :meth:`current_timeline` and pays for them deliberately.

        Returns a failed record too: a caller asking "how did it go?" needs the
        failure, where a caller asking to analyse needs a fresh attempt.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown or is
                not this owner's — one answer for both, so the endpoint cannot
                be used to discover that a recording exists. The stored audio is
                not required: a finished analysis stays readable once the bytes
                are gone.
        """
        await self._require_owned(recording_id, owner_id)
        return await self._analyses.latest_summary_for_recording(recording_id)

    async def current_timeline(
        self, recording_id: str, owner_id: uuid.UUID
    ) -> AudioAnalysis | None:
        """:meth:`current`, with the pitch timeline attached.

        The same owner-scoped read, and the same answers; the only difference is
        that this one carries the points. It exists so that reading the timeline
        is a decision a caller makes rather than a cost every caller pays.

        **No route reads this any more.** The graph reads a sample
        (:meth:`current_decimated_timeline`) and the two aggregations read the
        fields they fold (:meth:`current_timeline_fields`); each of those was one
        of these reads until the step that gave it its own. This is kept as the
        general form — the whole stored timeline, as frames, exactly as it was
        written — and the repository read beneath it is what the contract suite
        measures the narrower two against, point for point and field for field.
        """
        await self._require_owned(recording_id, owner_id)
        return await self._analyses.latest_for_recording(recording_id)

    async def current_timeline_fields(
        self, recording_id: str, owner_id: uuid.UUID
    ) -> TimelineFields | None:
        """:meth:`current`, with the **fields the aggregations fold** attached.

        What the note breakdown and the key need, and all they have ever needed:
        each frame's semitone and its deviation from that semitone. Until Step
        10.15 they read the timeline as frames and built 12 931 ``PitchPoint``
        models per request to read two fields of each — ~50 ms on the event
        loop, in front of every other request in the process, for folds costing
        3.3 ms and 1.4 ms.

        Unlike the graph, neither may be given a sample: a breakdown of every
        thirteenth frame is a breakdown of a different recording. This is the
        whole timeline, and :class:`TimelineFields` refuses one that is shorter
        than the stored count says it should be.

        Same owner-scoped read as the others, and the same single load: the
        record and its fields come from one statement, so a response cannot pair
        one analysis's ids with another analysis's notes.
        """
        await self._require_owned(recording_id, owner_id)
        return await self._analyses.latest_fields_for_recording(recording_id)

    async def current_decimated_timeline(
        self, recording_id: str, owner_id: uuid.UUID, *, max_points: int
    ) -> DecimatedTimeline | None:
        """:meth:`current`, with **at most ``max_points``** of the timeline attached.

        What the pitch graph needs, and all it has ever needed: the endpoint has
        decimated its response since Step 7I. Until Step 10.14 it decimated
        *after* materialising every stored point, which is why one graph cost
        ~110 ms of event-loop time and stalled every other request in the
        process for as long as it took. The sample is taken in the store now, so
        the points that are dropped are never built.

        Same owner-scoped read as the other two, same answers, and the same
        single load: the record and its sample come from one statement, so a
        graph cannot pair one analysis's ids with another's points.
        """
        await self._require_owned(recording_id, owner_id)
        return await self._analyses.latest_decimated_for_recording(
            recording_id, max_points=max_points
        )

    async def notes(self, recording_id: str, owner_id: uuid.UUID) -> tuple[NoteSummary, ...] | None:
        """The note breakdown of a recording's completed audio analysis.

        Derived from the stored pitch timeline on read rather than persisted
        alongside it: it is a pure function of points that are already on disk,
        and storing it too would be a second copy to keep consistent for a
        computation that costs one pass over a few thousand values.

        ``None`` when there is no completed analysis to break down — which the
        caller must distinguish from an empty tuple, meaning the analysis
        finished and found no notes.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown or is
                not this owner's.
        """
        return self.notes_of(await self.current_timeline_fields(recording_id, owner_id))

    async def key(self, recording_id: str, owner_id: uuid.UUID) -> KeyAnalysis | None:
        """The musical key a recording's completed audio analysis best fits.

        Derived on read from the stored pitch timeline, exactly as :meth:`notes`
        is, and for the same reasons: it is a pure function of points that are
        already on disk, storing it too would be a second copy to keep
        consistent, and deriving it means **every** analysis ever completed is
        answerable rather than only the ones measured after this shipped.

        Two different absences, and collapsing them would lose information:

        * ``None`` — there is no completed analysis to look at. The recording
          may be unanalysed, in flight, or failed.
        * a :class:`KeyAnalysis` whose ``key`` is ``None`` — the analysis is
          there, the timeline is there, and the notes in it do not establish a
          key. That is a **measurement outcome**, carries the reason and the
          twelve pitch-class shares that led to it, and is reported as "not
          measured" rather than as a failure.

        No key mathematics happens here. The service resolves an owner-scoped
        record and hands its timeline to the estimator; which key that is, how
        confident it is and whether there is one at all are decided in
        ``audio_analysis/key.py`` and nowhere else.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown or is
                not this owner's.
        """
        return self.key_of(await self.current_timeline_fields(recording_id, owner_id))

    @staticmethod
    def key_of(timeline: TimelineFields | None) -> KeyAnalysis | None:
        """The key of one analysis record, with no repository access.

        The counterpart to :meth:`notes_of`, and it exists for a measured
        reason rather than a symmetric one. A caller that has already resolved
        the record — the endpoint does, because it needs the recording and
        analysis ids for the response — would otherwise reach :meth:`key` and
        load the same analysis a second time. The arithmetic is ~1.4 ms at the
        longest accepted recording; **the document is the cost**, and loading it
        twice per request doubled the expensive half to save a parameter.

        It also removes a window rather than only a read. Two loads can return
        two different records if an analysis is re-run between them, which would
        pair one analysis's ids with another analysis's key. One load cannot.

        ``None`` for anything there is no completed measurement to fold: no
        record, one still in flight, one that failed, or one with no metrics.
        The caller must not collapse that with a :class:`KeyAnalysis` whose
        ``key`` is ``None``, which means the notes settled nothing.
        """
        if timeline is None or timeline.analysis.status is not AudioAnalysisStatus.COMPLETED:
            return None
        if timeline.analysis.metrics is None:
            return None
        return analyse_key(
            timeline.fields,
            frame_seconds=AudioAnalysisService._frame_seconds(timeline.analysis.metrics),
        )

    @staticmethod
    def _frame_seconds(metrics: AudioMetrics) -> float:
        """New audio each successive frame contributed, from the stored settings.

        One derivation for both aggregations. The hop rather than the frame
        length is ``notes.py``'s rule and the reason is there; what belongs here
        is only that two callers must not read it out of the settings block in
        two different ways.
        """
        return frame_duration_seconds(
            metrics.settings.hop_length_samples, metrics.settings.sample_rate_hz
        )

    @staticmethod
    def notes_of(timeline: TimelineFields | None) -> tuple[NoteSummary, ...] | None:
        """The breakdown of one analysis record, with no repository access.

        Split out so the feedback run can build the same breakdown from the
        record it already holds. Re-deriving it through :meth:`notes` would mean
        a second ownership check for a recording whose ownership was settled
        when the analysis was started, and a background task has no request to
        take an owner from.

        Now the *endpoint's* seam too, and for the reason :meth:`key_of` was
        given one in Phase 8 slice 5: the route resolves the record already,
        because it needs the recording and analysis ids for the response, and
        reaching :meth:`notes` from there loaded the same document — timeline
        and all — a second time. The two loads could also straddle a re-analysis
        and pair one analysis's ids with another analysis's notes.

        Two absences, exactly as on :meth:`key_of`: ``None`` means there is no
        completed measurement to break down, an empty tuple means there is one
        and it found no notes. Audio feedback is still not given the key — see
        ``docs/phase-8-specification.md``.
        """
        if timeline is None or timeline.analysis.status is not AudioAnalysisStatus.COMPLETED:
            return None
        if timeline.analysis.metrics is None:
            return None
        return summarise_notes(
            timeline.fields,
            frame_seconds=AudioAnalysisService._frame_seconds(timeline.analysis.metrics),
        )

    async def start_feedback(self, recording_id: str, owner_id: uuid.UUID) -> AudioAnalysisSummary:
        """Claim a completed analysis for feedback generation.

        Returns the record to work with — a summary, because claiming is a
        decision about state and :meth:`run_feedback` reloads the timeline by id
        when it actually needs one. ``feedback_status`` says what happened:
        ``generating`` means this call claimed it and the caller should schedule
        :meth:`run_feedback`; anything else means it was already claimed,
        already written, or is being retried after a failure.

        The claim is a **single conditional statement**, not a read followed by
        a write. Two concurrent requests therefore produce one claim and one
        "already claimed", with no window between the check and the update —
        which matters more here than anywhere else in the codebase, because a
        duplicate is a paid provider call.

        Raises:
            ApiError: the recording is unknown or not this owner's, has no
                completed audio analysis, or the analysis found no reliable
                pitch. **A provider is never called for a recording with nothing
                to interpret** — that is how ordinary speech avoids being handed
                back a vocal assessment.
        """
        analysis = await self._require_completed(recording_id, owner_id)

        claimed = await self._analyses.claim_feedback(analysis.audio_analysis_id)
        if claimed is None:
            # Somebody else holds the claim, or the prose is already written.
            # Either way this call must not call a provider; return what stands.
            return await self._latest_summary(analysis)

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
        analysis = await self._analyses.get(audio_analysis_id)
        if analysis is None or analysis.metrics is None:
            raise ApiError(ErrorCode.AUDIO_ANALYSIS_NOT_FOUND, "That analysis could not be found.")
        if analysis.feedback_status is not AudioFeedbackStatus.GENERATING:
            # Already written, or never claimed. Either way there is nothing to
            # do, and doing it anyway would be a second paid provider call.
            return analysis

        # A claimed record is completed with metrics, checked above, so the
        # breakdown is a tuple; `or ()` narrows it rather than substituting for
        # a missing one.
        notes = self.notes_of(TimelineFields.of_analysis(analysis)) or ()
        request = build_request(analysis.metrics, notes)

        try:
            feedback = await self._feedback.interpret_audio(request)
        except ProviderError as exc:
            return await self._fail_feedback(analysis, exc.error_code, **exc.log_context())
        except asyncio.CancelledError:
            await self._fail_feedback(analysis, ErrorCode.INTERNAL_ERROR, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            return await self._fail_feedback(
                analysis, ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

        latest = await self._latest(analysis)
        completed = await self._save(
            _revalidated(
                latest,
                feedback=feedback.model_dump(),
                feedback_status=AudioFeedbackStatus.COMPLETED,
                feedback_error_code=None,
            ),
            expect_status=latest.status,
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

    async def _require_completed(
        self, recording_id: str, owner_id: uuid.UUID
    ) -> AudioAnalysisSummary:
        """The completed analysis to interpret, or a documented refusal.

        A summary: this decides whether a provider may be called, and every
        input to that decision is a status, an error code or the presence of
        metrics. The timeline is loaded once, by the background run, and only
        after the claim has been won.
        """
        analysis = await self.current(recording_id, owner_id)
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

    async def _fail_feedback(
        self, analysis: AudioAnalysis, code: ErrorCode, **context: str
    ) -> AudioAnalysis:
        """Record a feedback failure. The measurements are never touched."""
        latest = await self._latest(analysis)
        failed = _revalidated(
            latest,
            feedback=None,
            feedback_status=AudioFeedbackStatus.FAILED,
            feedback_error_code=code,
        )
        try:
            failed = await self._save(failed, expect_status=latest.status)
        except Exception:  # noqa: BLE001 - the store is what broke, not the run
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

    async def start(self, recording_id: str, owner_id: uuid.UUID) -> StartedAudioAnalysis:
        """Return the analysis to work with, creating one only if needed.

        In order: one already in flight is returned; one already completed is
        returned; otherwise — including after a failure — a new ``pending``
        record is created. A failed analysis is therefore retried by calling
        this again, and the failed record stays stored and inspectable.

        As on the speech side, the read is an optimisation and the unique index
        is the guarantee: concurrent callers that both find nothing produce one
        insert and one refusal, across processes.
        """
        await self._require_recording(recording_id, owner_id)

        existing = await self._existing_for(recording_id)
        if existing is not None:
            return self._reused(existing, recording_id)

        try:
            analysis = await self._analyses.create(
                AudioAnalysis(audio_analysis_id=new_audio_analysis_id(), recording_id=recording_id)
            )
        except ActiveAudioAnalysisExistsError:
            existing = await self._analyses.latest_summary_for_recording(recording_id)
            if existing is None:  # pragma: no cover - the index only fires if a row exists
                raise ApiError(
                    ErrorCode.INTERNAL_ERROR, "That analysis could not be started."
                ) from None
            return self._reused(existing, recording_id)

        logger.info(
            "audio_analysis_created",
            extra={
                "audio_analysis_id": analysis.audio_analysis_id,
                "recording_id": recording_id,
            },
        )
        return StartedAudioAnalysis(analysis=analysis, created=True)

    def _reused(self, existing: AudioAnalysisSummary, recording_id: str) -> StartedAudioAnalysis:
        logger.info(
            "audio_analysis_reused",
            extra={
                "audio_analysis_id": existing.audio_analysis_id,
                "recording_id": recording_id,
                "status": existing.status.value,
            },
        )
        return StartedAudioAnalysis(analysis=existing, created=False)

    async def run(self, audio_analysis_id: str) -> AudioAnalysis:
        """Execute a pending analysis to a terminal state.

        Takes no owner: this is the continuation of a decision :meth:`start`
        already made, reachable only from a background task the API scheduled.

        Never raises for a measurement failure: it is persisted on the record
        and the record returned, so a background task cannot take the process
        down. Cancellation is persisted and then re-raised.
        """
        analysis = await self._analyses.get(audio_analysis_id)
        if analysis is None:
            raise ApiError(ErrorCode.AUDIO_ANALYSIS_NOT_FOUND, "That analysis could not be found.")
        if analysis.status is not AudioAnalysisStatus.PENDING:
            # Already finished, or already claimed. Refusing here means even a
            # caller who schedules twice cannot measure the same file twice.
            return analysis

        try:
            return await self._execute(analysis)
        except AudioAnalysisConflictError:
            # Another worker claimed it between the read and the first write.
            return await self._latest(analysis)
        except AudioAnalysisError as exc:
            return await self._fail(
                await self._latest(analysis), exc.error_code, **exc.log_context()
            )
        except asyncio.CancelledError:
            await self._fail(
                await self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason="cancelled"
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a background task must not die
            return await self._fail(
                await self._latest(analysis), ErrorCode.INTERNAL_ERROR, reason=type(exc).__name__
            )

    # --- The pipeline ------------------------------------------------------

    async def _execute(self, analysis: AudioAnalysis) -> AudioAnalysis:
        audio_path = self._resolve_audio(analysis.recording_id)

        # Claiming the record *is* this write: it requires the stored status to
        # still be "pending", so a second worker that read the same record loses
        # here rather than measuring the file twice.
        analysis = await self._save(
            _revalidated(analysis, status=AudioAnalysisStatus.ANALYZING, started_at=utc_now()),
            expect_status=AudioAnalysisStatus.PENDING,
        )

        # Off the event loop: this is numpy over the whole file, and running it
        # inline would block every other request for the duration.
        result = await asyncio.to_thread(self._analyzer.analyze, audio_path)

        completed = await self._save(
            _revalidated(
                analysis,
                status=AudioAnalysisStatus.COMPLETED,
                metrics=result.metrics.model_dump(),
                pitch_points=[point.model_dump() for point in result.pitch_points],
                completed_at=utc_now(),
            ),
            expect_status=AudioAnalysisStatus.ANALYZING,
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

    async def _require_owned(self, recording_id: str, owner_id: uuid.UUID) -> None:
        """Refuse a recording that is unknown *or* is not this owner's.

        Checked in SQL by the repository, so someone else's recording is never
        selected rather than selected and filtered out.
        """
        if await self._recordings.get(recording_id, owner_id) is None:
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")

    async def _require_recording(self, recording_id: str, owner_id: uuid.UUID) -> None:
        await self._require_owned(recording_id, owner_id)
        if not self._storage.exists(recording_id):
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

    async def _existing_for(self, recording_id: str) -> AudioAnalysisSummary | None:
        """The analysis a caller should be given back, if any.

        Sweeps abandoned records on the way past: one that has been "analyzing"
        since before the staleness horizon belongs to a process that is no
        longer running, and leaving it active would make the recording
        permanently unanalysable.

        Summaries, and this is where that matters most: a recording analysed
        five times has five stored documents, and deciding which one to hand
        back reads a status and a timestamp off each. Loading the timelines to
        do it made one ``POST`` cost a document per attempt ever made.
        """
        for analysis in await self._analyses.list_summaries_for_recording(recording_id):
            if analysis.status in ACTIVE_STATUSES:
                if self._is_stale(analysis):
                    await self._fail_abandoned(analysis)
                    continue
                return analysis
            if analysis.status is AudioAnalysisStatus.COMPLETED:
                return analysis
        return None

    async def _fail_abandoned(self, analysis: AudioAnalysisSummary) -> None:
        """Mark a record whose worker is gone as failed.

        Reloads the whole record first, because :meth:`_fail` rewrites the
        stored document and a summary would rewrite it without its timeline. An
        abandoned record is by definition still pending or analysing, so there
        is no timeline to reload — but the type says so, and the type is what
        keeps that true if the sweep is ever pointed at something else.
        """
        record = await self._analyses.get(analysis.audio_analysis_id)
        if record is None:  # pragma: no cover - it was listed a moment ago
            return
        await self._fail(record, ErrorCode.INTERNAL_ERROR, reason="abandoned")

    def _is_stale(self, analysis: AudioAnalysisSummary) -> bool:
        started = analysis.started_at or analysis.created_at
        return datetime.now(UTC) - started > self._stale_after

    async def _save(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        """Persist a transition, but only from the status the caller last read."""
        return await self._analyses.update(analysis, expect_status=expect_status)

    async def _latest(self, analysis: AudioAnalysis) -> AudioAnalysis:
        """The most recently persisted state, so a failure keeps its timestamps.

        The whole record, because every caller of this is about to write it back.
        """
        try:
            return await self._analyses.get(analysis.audio_analysis_id) or analysis
        except Exception:  # noqa: BLE001 - a failing store must not mask the failure
            return analysis

    async def _latest_summary(self, analysis: AudioAnalysisSummary) -> AudioAnalysisSummary:
        """:meth:`_latest` for a caller that is only going to read it."""
        try:
            return await self._analyses.summary(analysis.audio_analysis_id) or analysis
        except Exception:  # noqa: BLE001 - a failing store must not mask the failure
            return analysis

    async def _fail(
        self, analysis: AudioAnalysis, code: ErrorCode, **context: str
    ) -> AudioAnalysis:
        """Persist a terminal failure. The recording is never touched."""
        failed = _revalidated(
            analysis,
            status=AudioAnalysisStatus.FAILED,
            error_code=code,
            completed_at=utc_now(),
        )
        try:
            failed = await self._save(failed, expect_status=analysis.status)
        except AudioAnalysisConflictError:
            # A concurrent sweep, or the worker that owns the record, already
            # moved it. Theirs is the truth.
            logger.info(
                "audio_analysis_failure_superseded",
                extra={"audio_analysis_id": failed.audio_analysis_id, "error_code": code.value},
            )
            return await self._latest(analysis)
        except Exception:  # noqa: BLE001 - the store is what broke
            # Report the original failure rather than replacing it with a write
            # error nobody asked about.
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
