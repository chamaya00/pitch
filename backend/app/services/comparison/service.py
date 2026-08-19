"""Deciding whether two recordings may be compared, and comparing them.

Three responsibilities, in this order:

1. **Load** exactly two recordings, owner-scoped in SQL.
2. **Decide** whether each side is eligible, and say precisely why when it is not.
3. **Delegate** the arithmetic to ``compare.py``, which touches nothing.

The split matters because eligibility is where the honesty lives. Every refusal
below names a distinct situation the reader can act on — nobody measured this
yet, it is still running, it failed, there was no reliable pitch — instead of
one flat "unavailable". And an ineligible comparison carries no measurements at
all, so there is no half-comparison for a client to render as if it were whole.

Nothing here computes a measurement. The note breakdown is built by
``audio_analysis/notes.py``, the same function the single-recording endpoint
uses, from the same stored timeline. There is one note aggregation in this
system.
"""

import uuid

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioMetrics,
    NoteSummary,
    PitchFields,
)
from app.services.audio_analysis.notes import frame_duration_seconds, summarise_notes
from app.services.comparison.compare import compare_metrics, compare_notes, detect_caveats
from app.services.comparison.models import (
    ComparisonSide,
    RecordingComparison,
    SideStatus,
)
from app.services.comparison.sources import ComparisonSource, ComparisonSourceReader

logger = get_logger(__name__)


class ComparisonService:
    """Compares two of one owner's recordings."""

    def __init__(self, *, recordings: ComparisonSourceReader) -> None:
        self._recordings = recordings

    async def compare(
        self, left_id: str, right_id: str, owner_id: uuid.UUID
    ) -> RecordingComparison:
        """Compare two recordings belonging to ``owner_id``.

        Raises:
            ApiError: ``VALIDATION_ERROR`` if the same recording was given
                twice. Everything else — unknown, unowned, unmeasured, failed —
                is a *successful* response describing why the comparison could
                not be made, because those are answers rather than errors and a
                client needs to render each of them differently.
        """
        if left_id == right_id:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "Choose two different recordings to compare.",
            )

        # One query, both ids, owner in the WHERE clause. Anything not this
        # owner's simply does not come back.
        sources = await self._recordings.comparison_sources(owner_id, [left_id, right_id])

        left = _side(left_id, sources.get(left_id))
        right = _side(right_id, sources.get(right_id))

        left_metrics = _metrics_of(sources.get(left_id))
        right_metrics = _metrics_of(sources.get(right_id))

        if (
            left.status is not SideStatus.READY
            or right.status is not SideStatus.READY
            or left_metrics is None
            or right_metrics is None
        ):
            logger.info(
                "comparison_refused",
                extra={"left_status": left.status.value, "right_status": right.status.value},
            )
            return RecordingComparison(left=left, right=right, comparable=False)

        left_source = sources[left_id]
        right_source = sources[right_id]

        return RecordingComparison(
            left=left,
            right=right,
            comparable=True,
            metrics=compare_metrics(left_metrics, right_metrics),
            notes=compare_notes(_notes_of(left_source), _notes_of(right_source)),
            caveats=detect_caveats(
                left_metrics,
                right_metrics,
                left_format=left_source.recording.audio_format.value,
                right_format=right_source.recording.audio_format.value,
            ),
        )


def _metrics_of(source: ComparisonSource | None) -> AudioMetrics | None:
    if source is None or source.audio_analysis is None:
        return None
    return source.audio_analysis.metrics


def _notes_of(source: ComparisonSource) -> tuple[NoteSummary, ...]:
    """The stored timeline aggregated by note, via the one function that does it."""
    analysis = source.audio_analysis
    if analysis is None or analysis.metrics is None:
        return ()
    settings = analysis.metrics.settings
    return summarise_notes(
        # This side already holds the whole record: a comparison reads two
        # documents for their metrics as well as their timelines, in one
        # statement, and takes the fields out of frames it has rather than
        # asking for them again. What that read costs is measured in
        # ``docs/architecture.md``; Step 10.15 changed the shape of this call
        # and not the read behind it.
        PitchFields.of_points(analysis.pitch_points),
        frame_seconds=frame_duration_seconds(settings.hop_length_samples, settings.sample_rate_hz),
    )


def _side(recording_id: str, source: ComparisonSource | None) -> ComparisonSide:
    """Describe one side of the comparison, including why it cannot take part.

    A recording that is unknown and one that belongs to somebody else arrive here
    identically — both as ``None`` — and both become ``NOT_FOUND``. That is the
    intended behaviour, not an accident of the query: a different answer would
    confirm an id is real to somebody with no right to know.
    """
    if source is None:
        return ComparisonSide(recording_id=recording_id, status=SideStatus.NOT_FOUND)

    recording = source.recording
    identity = {
        "recording_id": recording_id,
        "original_filename": recording.original_filename,
        "created_at": recording.created_at,
        "duration_seconds": recording.duration_seconds,
        "audio_format": recording.audio_format.value,
    }

    analysis = source.audio_analysis
    if analysis is None:
        return ComparisonSide(**identity, status=SideStatus.ANALYSIS_MISSING)

    if analysis.status is AudioAnalysisStatus.FAILED:
        return ComparisonSide(
            **identity,
            status=_failure_status(analysis),
            error_code=analysis.error_code,
        )

    if analysis.status is not AudioAnalysisStatus.COMPLETED or analysis.metrics is None:
        return ComparisonSide(**identity, status=SideStatus.ANALYSIS_IN_PROGRESS)

    pitch = analysis.metrics.pitch
    return ComparisonSide(
        **identity,
        status=SideStatus.READY,
        # Absent when frames were voiced but none was held long enough to count
        # as a range. Reported as absent, never as a zero-semitone range.
        lowest_note=None if pitch is None else pitch.lowest_note,
        highest_note=None if pitch is None else pitch.highest_note,
    )


def _failure_status(analysis: AudioAnalysis) -> SideStatus:
    """Separate "no reliable pitch" from every other failure.

    A whisper, a noisy room or an instrumental recording is a normal outcome and
    reads very differently from a decoder failure. Collapsing the two would tell
    somebody their recording is broken when it simply had nothing to measure.
    """
    if analysis.error_code is ErrorCode.INSUFFICIENT_PITCH_SIGNAL:
        return SideStatus.INSUFFICIENT_PITCH_SIGNAL
    return SideStatus.ANALYSIS_FAILED
