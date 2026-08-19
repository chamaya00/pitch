"""Deterministic audio-analysis routes.

A thin translation layer, exactly like ``routes/analysis.py``: path parameter in,
service call, domain object out as a response schema. No measurement logic, no
filesystem access, no decisions about retry or idempotency — those belong to
``services/orchestration/audio_analysis.py`` and stay there.

Paths follow the convention the speech analysis already set:
``/recordings/{id}/<noun>``. The noun is ``audio-analysis`` rather than
something like ``pitch``, because the resource is the whole deterministic
measurement — pitch, loudness and spectrum — and the timeline hangs off it at
``/audio-analysis/pitch``.

The slow work never happens inside a request. ``POST`` creates the record and
hands execution to a background task, so a provider-free but CPU-heavy
measurement cannot hold a connection open. A measurement failure is therefore
never a ``POST`` error: it surfaces on ``GET`` as ``200`` with
``status: "failed"``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Query, Response, status

from app.api.deps import AudioAnalysisServiceDep, CostlyRequestDep, OwnerIdDep
from app.api.responses import error_responses
from app.core.errors import ApiError, ErrorCode
from app.schemas.audio_analysis import (
    AudioAnalysisResponse,
    AudioFeedbackStateResponse,
    MusicalKeyResponse,
    NoteBreakdownResponse,
    PitchTimelineResponse,
)
from app.services.audio_analysis.models import (
    AudioAnalysisStatus,
    AudioAnalysisSummary,
    AudioFeedbackStatus,
    DecimatedTimeline,
    TimelineFields,
)
from app.services.orchestration.audio_analysis import AudioAnalysisService

router = APIRouter(prefix="/recordings", tags=["audio-analysis"])

#: Recording ids are ``uuid4().hex``. Constraining the path parameter means a
#: malformed id is refused at the edge and never reaches a service, so nothing
#: shaped like a path or a traversal sequence travels further in.
RecordingIdPath = Annotated[
    str,
    Path(
        description="Server-generated recording identifier: 32 lower-case hex characters.",
        pattern=r"^[0-9a-f]{32}$",
        examples=["8f14e45fceea167a5a36dedd4bea2543"],
    ),
]

#: Default cap on returned pitch points. A five-minute recording produces around
#: 13 000 voiced frames; a graph a few hundred pixels wide cannot show them, and
#: sending them all costs a megabyte for nothing. Callers that genuinely want
#: every point can raise it.
DEFAULT_MAX_POINTS = 1000
MAX_POINTS_LIMIT = 50000


@router.post(
    "/{recording_id}/audio-analysis",
    response_model=AudioAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyse a recording's audio",
    description=(
        "Start a deterministic audio analysis of a stored recording — pitch, "
        "detected range, pitch stability, loudness and spectral characteristics "
        "— or return the one that already exists.\n\n"
        "This is **separate from the speech analysis** on "
        "`/recordings/{id}/analysis`, which transcribes the recording and "
        "measures pace, pauses and filler words. The two measure different "
        "things, run independently, and are never combined into one figure.\n\n"
        "**This returns before the analysis runs.** Poll `GET` on the same path "
        "for progress and results. A measurement failure therefore never appears "
        "here — it is recorded on the analysis and surfaces on `GET` as `200` "
        'with `status: "failed"`.\n\n'
        "**Repeating the request is safe.** An analysis already queued, running "
        "or finished is returned as-is. Only a recording whose last audio "
        "analysis *failed* starts a new one.\n\n"
        "Returns `202` when work is queued, and `200` when an already-completed "
        "analysis is handed back."
    ),
    responses={
        status.HTTP_200_OK: {
            "model": AudioAnalysisResponse,
            "description": "An audio analysis of this recording had already finished.",
        },
        **error_responses(
            ErrorCode.RECORDING_NOT_FOUND,
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.INTERNAL_ERROR,
        ),
    },
)
async def start_audio_analysis(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
    _limit: CostlyRequestDep,
    background_tasks: BackgroundTasks,
    response: Response,
) -> AudioAnalysisResponse:
    """Create or return the audio analysis for a recording."""
    started = await service.start(recording_id, owner_id)

    if started.created:
        # Scheduled only for an analysis this request created; scheduling one
        # for a record already in flight would measure the same file twice.
        background_tasks.add_task(service.run, started.analysis.audio_analysis_id)
    elif started.analysis.is_terminal:
        response.status_code = status.HTTP_200_OK

    return AudioAnalysisResponse.from_domain(started.analysis)


@router.get(
    "/{recording_id}/audio-analysis",
    response_model=AudioAnalysisResponse,
    summary="Get a recording's audio analysis",
    description=(
        "Return the current audio analysis of a recording: its progress while it "
        "runs, its measurements once it finishes.\n\n"
        "**A failed analysis is a successful response.** It comes back as `200` "
        'with `status: "failed"` and an `error_code` — `INSUFFICIENT_PITCH_SIGNAL` '
        "when the audio decoded but carried no reliable pitch, which is a normal "
        "outcome for a whisper, a noisy room or an instrumental recording.\n\n"
        "A `null` measurement always means the signal did not support it, never "
        "zero. The pitch timeline is not included here; fetch it from "
        "`/audio-analysis/pitch`."
    ),
    responses=error_responses(
        ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_audio_analysis(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
) -> AudioAnalysisResponse:
    """Return the recording's most recent audio analysis."""
    return AudioAnalysisResponse.from_domain(
        await _require_analysis(service, recording_id, owner_id)
    )


@router.get(
    "/{recording_id}/audio-analysis/pitch",
    response_model=PitchTimelineResponse,
    summary="Get the pitch timeline",
    description=(
        "Every voiced frame of the analysis, in order.\n\n"
        "**Unvoiced frames are omitted, not nulled.** Silence, noise, consonants "
        "and anything below the clarity threshold produce no point at all, so "
        "every point returned was measured. A gap between consecutive timestamps "
        "is meaningful — draw it as a gap and never interpolate across it. The "
        "share of the recording that was voiced is `stability.voiced_ratio` on "
        "the summary.\n\n"
        "Long recordings produce tens of thousands of points, so the response is "
        f"decimated to `max_points` (default {DEFAULT_MAX_POINTS}) by taking "
        "every n-th point. `decimation` states the factor used."
    ),
    responses=error_responses(
        ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_pitch_timeline(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
    max_points: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_POINTS_LIMIT,
            description="Largest number of points to return. The response says what it did.",
        ),
    ] = DEFAULT_MAX_POINTS,
) -> PitchTimelineResponse:
    """Return the analysis's pitch points, decimated to ``max_points``."""
    timeline = await _require_decimated_timeline(
        service, recording_id, owner_id, max_points=max_points
    )
    if timeline.analysis.status is not AudioAnalysisStatus.COMPLETED:
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio analysis has not finished yet.",
        )

    return PitchTimelineResponse.from_domain(
        timeline.analysis,
        points=list(timeline.points),
        decimation=timeline.decimation,
    )


@router.get(
    "/{recording_id}/audio-analysis/notes",
    response_model=NoteBreakdownResponse,
    summary="Get the note breakdown",
    description=(
        "How the recording's pitched time was divided between musical notes.\n\n"
        "A companion to `/audio-analysis/pitch` rather than a replacement: that "
        "path returns the timeline frame by frame for drawing, this one returns "
        "it aggregated by note for reading. Both derive from the same stored "
        "measurement, and the aggregation happens here rather than in a client "
        "so a browser never downloads thousands of points to build a short "
        "table.\n\n"
        "**`percentage_of_voiced_time` is a share of pitched time, not of the "
        "recording.** Silence and unvoiced audio are excluded from the "
        "denominator — a recording that is half silence would otherwise report "
        "every note at half its real share — so the percentages sum to 100.\n\n"
        "**An empty `notes` list means no notes were detected.** That is not a "
        "successful measurement of zero notes, and must not be rendered as an "
        "empty table.\n\n"
        "Notes are ordered longest first, with the lower note first on a tie, so "
        "the same analysis always renders the same way."
    ),
    responses=error_responses(
        ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_note_breakdown(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
) -> NoteBreakdownResponse:
    """Return the analysis's pitched time, aggregated by note."""
    timeline = await _require_timeline_fields(service, recording_id, owner_id)
    # Aggregated from the record already loaded, exactly as the key is below.
    # This route used to resolve the analysis and then call `service.notes`,
    # which resolved it again — two loads of a document whose timeline is the
    # whole cost of the request, and a window in which a re-analysis between
    # them could pair one analysis's ids with another analysis's notes.
    notes = service.notes_of(timeline)
    if notes is None:
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio analysis has not finished yet.",
        )
    return NoteBreakdownResponse.from_domain(timeline.analysis, notes)


@router.get(
    "/{recording_id}/audio-analysis/key",
    response_model=MusicalKeyResponse,
    summary="Get the estimated musical key",
    description=(
        "The key implied by **what was sung in this recording**, folded from the "
        "same stored pitch timeline `/audio-analysis/pitch` returns.\n\n"
        "It is not a claim that the key was the right one. There is no reference "
        "melody in this product, so this says which key the notes best fit and "
        "nothing about whether those were the intended notes.\n\n"
        "**`key: null` is a measurement outcome, not an error.** A hum, a "
        "three-note arpeggio or a chromatic wander all analyse successfully and "
        "establish no key; `unmeasured_reason` says which evidence was missing, "
        "and the twelve pitch-class shares are returned anyway so a reader can "
        'see why. Render it as "not measured", never as a failure.\n\n'
        "**`confidence` is a margin over the next-best candidate**, not a "
        "correlation and not a probability. Random pitch classes correlate +0.49 "
        "with a real key profile and a single held note +0.48, so a correlation "
        "would read as near-certainty for a hum.\n\n"
        "A recording whose audio analysis has not completed is a different thing "
        "and answers `404`."
    ),
    responses=error_responses(
        ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_musical_key(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
) -> MusicalKeyResponse:
    """Return the key the analysis's pitch classes best fit, or say there is none."""
    timeline = await _require_timeline_fields(service, recording_id, owner_id)
    # Folded from the record already loaded, not fetched again: the pitch
    # timeline is the expensive part of this request and a second read would
    # buy nothing but the chance of pairing one analysis's ids with another
    # analysis's key.
    key = service.key_of(timeline)
    if key is None:
        # No *completed* analysis. Deliberately the same answer `/notes` gives,
        # and deliberately not a null key: one means there is nothing to look
        # at, the other means we looked and the notes settled nothing.
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio analysis has not finished yet.",
        )
    return MusicalKeyResponse.from_domain(timeline.analysis, key)


@router.post(
    "/{recording_id}/audio-analysis/feedback",
    response_model=AudioFeedbackStateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Explain the audio measurements",
    description=(
        "Ask a language model to explain this recording's measured audio in "
        "plain language.\n\n"
        "**The model is not given the recording.** It receives a structured set "
        "of measurements the analysis already computed, and nothing else — no "
        "audio, no path, no filename, no recording id. It explains numbers; it "
        "does not produce them, and it is instructed never to state one that was "
        "not supplied.\n\n"
        "**Nothing is generated automatically.** This is an explicit request, "
        "and repeating it is safe: feedback already written or already being "
        "written is returned as-is, so a client cannot run up a provider bill by "
        "re-rendering.\n\n"
        "**Requires a completed analysis.** A recording whose audio analysis "
        "failed with `INSUFFICIENT_PITCH_SIGNAL` — ordinary speech, a whisper, a "
        "noisy room — is refused with that code rather than handed to a model, "
        "which is what stops a vocal assessment being invented from a recording "
        "that contained no singing.\n\n"
        "Returns `202` while generation runs; poll `GET` on the same path."
    ),
    responses={
        status.HTTP_200_OK: {
            "model": AudioFeedbackStateResponse,
            "description": "Feedback for this analysis had already been written.",
        },
        **error_responses(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            ErrorCode.INSUFFICIENT_PITCH_SIGNAL,
            ErrorCode.RECORDING_NOT_FOUND,
            ErrorCode.ANALYSIS_NOT_CONFIGURED,
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.INTERNAL_ERROR,
        ),
    },
)
async def start_audio_feedback(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
    _limit: CostlyRequestDep,
    background_tasks: BackgroundTasks,
    response: Response,
) -> AudioFeedbackStateResponse:
    """Claim the analysis for feedback and schedule the provider call."""
    analysis = await service.start_feedback(recording_id, owner_id)

    if analysis.feedback_status is AudioFeedbackStatus.GENERATING:
        background_tasks.add_task(service.run_feedback, analysis.audio_analysis_id)
    else:
        response.status_code = status.HTTP_200_OK

    return AudioFeedbackStateResponse.from_domain(analysis)


@router.get(
    "/{recording_id}/audio-analysis/feedback",
    response_model=AudioFeedbackStateResponse,
    summary="Get the audio feedback",
    description=(
        "The current state of this recording's audio feedback.\n\n"
        "**A failed generation is a successful response**: `200` with "
        '`status: "failed"` and an `error_code`. The measurements are unaffected '
        "— they were computed without a model and a provider outage costs the "
        "prose and nothing else.\n\n"
        "`provenance.is_mock` on the feedback says whether it came from a "
        "development stand-in. Content marked `true` is demo data and must never "
        "be presented as a real interpretation."
    ),
    responses=error_responses(
        ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_audio_feedback(
    recording_id: RecordingIdPath,
    service: AudioAnalysisServiceDep,
    owner_id: OwnerIdDep,
) -> AudioFeedbackStateResponse:
    """Return the recording's audio feedback and its state."""
    return AudioFeedbackStateResponse.from_domain(
        await _require_analysis(service, recording_id, owner_id)
    )


async def _require_analysis(
    service: AudioAnalysisService, recording_id: str, owner_id: uuid.UUID
) -> AudioAnalysisSummary:
    """The recording's current analysis, without its pitch timeline.

    What the two summary routes need. Neither returns a pitch point, and
    loading the timeline for them cost 87 ms and 19 MB per request at the
    longest recording this product accepts — on the read the browser polls
    while a measurement runs.
    """
    analysis = await service.current(recording_id, owner_id)
    if analysis is None:
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio has not been analysed yet.",
        )
    return analysis


async def _require_timeline_fields(
    service: AudioAnalysisService, recording_id: str, owner_id: uuid.UUID
) -> TimelineFields:
    """The same record with **what the two folding routes read** of its timeline.

    Separate from :func:`_require_analysis` so that paying for the timeline is a
    choice a route makes, and visible in its own body. Both routes here fold
    every frame and return none, and they read two of a frame's six fields; this
    reads those two, as arrays. Building the frames to reach them cost ~50 ms of
    event-loop time per request — time in which every other request in the
    process, including the poll that says whether an analysis has finished, was
    waiting.
    """
    timeline = await service.current_timeline_fields(recording_id, owner_id)
    if timeline is None:
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio has not been analysed yet.",
        )
    return timeline


async def _require_decimated_timeline(
    service: AudioAnalysisService,
    recording_id: str,
    owner_id: uuid.UUID,
    *,
    max_points: int,
) -> DecimatedTimeline:
    """The record with a **sample** of its timeline, for the one route that draws it.

    A third read rather than a slice of the second, because the points this
    route discards are points nothing needs to build: at the longest accepted
    recording the graph returns 995 of 12 931, and materialising the other
    11 936 cost ~110 ms of event-loop time per request — time in which every
    other request in the process, including the poll that says whether the
    analysis has finished, was waiting.
    """
    timeline = await service.current_decimated_timeline(
        recording_id, owner_id, max_points=max_points
    )
    if timeline is None:
        raise ApiError(
            ErrorCode.AUDIO_ANALYSIS_NOT_FOUND,
            "That recording's audio has not been analysed yet.",
        )
    return timeline
