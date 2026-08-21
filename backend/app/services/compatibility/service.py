"""Deciding whether a recording and a song reference may be compared, and comparing them.

Three responsibilities, in the order ``comparison/service.py`` established:

1. **Load** the recording and the reference, both owner-scoped in SQL.
2. **Decide** whether the recording is eligible, and say precisely why when it
   is not.
3. **Delegate** the arithmetic to ``fit.py``, which touches nothing.

Look at what this service is constructed with, because that is the guarantee
rather than a promise: three repositories, no storage, no analyzer, and **no
provider**. There is no object in this graph through which a model could reach
one of these numbers, exactly as there is none in the comparison and progress
services.

**Two kinds of "no".** An id that is not the caller's is a ``404`` — on the
recording, because that is what every ``/recordings/{id}/…`` route answers, and
on the reference, because a caller may only name their own. Everything else —
nobody has measured this recording yet, a run is still going, it failed, it
carried no reliable pitch — is a *successful* response describing why the
comparison could not be made, because those are answers rather than errors and a
client renders each of them differently.
"""

import uuid

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.services.audio_analysis.models import (
    AudioAnalysisStatus,
    AudioAnalysisSummary,
)
from app.services.audio_analysis.postgres_repository import AsyncAudioAnalysisRepository
from app.services.compatibility.fit import compare, recording_range
from app.services.compatibility.models import (
    RecordingSideStatus,
    SongCompatibility,
    SongReference,
)
from app.services.compatibility.repository import SongReferenceRepository
from app.services.recordings.postgres_repository import OwnedRecordingRepository

logger = get_logger(__name__)

#: States an analysis is still moving through. Named from the enum rather than
#: listed as strings, so adding a state to the lifecycle is a decision here.
_IN_FLIGHT = frozenset({AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING})


class CompatibilityService:
    """Places one owner's recording against one of their song references."""

    def __init__(
        self,
        *,
        recordings: OwnedRecordingRepository,
        references: SongReferenceRepository,
        analyses: AsyncAudioAnalysisRepository,
    ) -> None:
        self._recordings = recordings
        self._references = references
        self._analyses = analyses

    async def compatibility(
        self, recording_id: str, reference_id: str, owner_id: uuid.UUID
    ) -> SongCompatibility:
        """Compare a recording's detected range with a reference's asserted one.

        Raises:
            ApiError: ``RECORDING_NOT_FOUND`` if the recording is unknown or is
                not this owner's, and ``REFERENCE_NOT_FOUND`` likewise for the
                reference. One answer for both cases in each pair, so neither id
                can be used to discover that somebody else's row exists.
        """
        # Ownership first, and separately for each id: both reads carry the
        # owner in the WHERE clause, so a row belonging to somebody else is
        # never selected rather than selected and then refused.
        if await self._recordings.get(recording_id, owner_id) is None:
            raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "No recording exists with that id.")

        reference = await self._references.get(reference_id, owner_id)
        if reference is None:
            raise ApiError(ErrorCode.REFERENCE_NOT_FOUND, "No song reference exists with that id.")

        # Without the timeline. A range and a voiced ratio are two scalars on a
        # summary, and the frames behind them are ~1.6 MB this read never needs.
        analysis = await self._analyses.latest_summary_for_recording(recording_id)
        return self._result(analysis, reference)

    def _result(
        self, analysis: AudioAnalysisSummary | None, reference: SongReference
    ) -> SongCompatibility:
        """Turn a stored analysis into either a comparison or a stated refusal."""
        status = _status_of(analysis)
        if status is not RecordingSideStatus.READY or analysis is None:
            logger.info("compatibility_refused", extra={"recording_status": status.value})
            return SongCompatibility(comparable=False, recording_status=status)

        # Guaranteed by _status_of, which only answers READY for a completed
        # analysis whose metrics carry a range.
        metrics = analysis.metrics
        assert metrics is not None and metrics.pitch is not None
        singer = recording_range(metrics.pitch)
        if singer is None:  # pragma: no cover - a measured range is always nameable
            return SongCompatibility(
                comparable=False,
                recording_status=RecordingSideStatus.INSUFFICIENT_PITCH_SIGNAL,
            )

        return compare(singer, reference, voiced_ratio=metrics.stability.voiced_ratio)


def _status_of(analysis: AudioAnalysisSummary | None) -> RecordingSideStatus:
    """Why this recording can or cannot take part.

    The distinctions exist because each one is a different thing to tell
    somebody: measure it, wait, try again, or sing something with a pitch in it.
    """
    if analysis is None:
        return RecordingSideStatus.ANALYSIS_MISSING
    if analysis.status in _IN_FLIGHT:
        return RecordingSideStatus.ANALYSIS_IN_PROGRESS
    if analysis.status is AudioAnalysisStatus.FAILED:
        return RecordingSideStatus.ANALYSIS_FAILED
    if analysis.metrics is None or analysis.metrics.pitch is None:
        # A completed analysis with no range: the audio decoded and no frame
        # carried a reliable pitch. Normal for a whisper or a noisy room.
        return RecordingSideStatus.INSUFFICIENT_PITCH_SIGNAL
    return RecordingSideStatus.READY


class SongReferenceService:
    """Creating, listing and removing an owner's song references.

    Thin on purpose. A reference is a handful of numbers somebody typed; the
    validation that matters lives in :class:`SongReference` itself, so this
    service assigns an id, records who it belongs to, and gets out of the way.
    """

    def __init__(self, *, references: SongReferenceRepository) -> None:
        self._references = references

    async def create(self, reference: SongReference, owner_id: uuid.UUID) -> SongReference:
        return await self._references.create(reference, owner_id)

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[SongReference]:
        return await self._references.list_for_owner(owner_id, limit)

    async def get(self, reference_id: str, owner_id: uuid.UUID) -> SongReference:
        """One reference.

        Raises:
            ApiError: ``REFERENCE_NOT_FOUND`` if it is unknown **or** somebody
                else's. One answer for both.
        """
        reference = await self._references.get(reference_id, owner_id)
        if reference is None:
            raise ApiError(ErrorCode.REFERENCE_NOT_FOUND, "No song reference exists with that id.")
        return reference

    async def delete(self, reference_id: str, owner_id: uuid.UUID) -> None:
        """Remove one of the caller's references.

        Raises:
            ApiError: ``REFERENCE_NOT_FOUND``, as above. Deleting something that
                is not there is reported rather than silently succeeding, so a
                client cannot mistake "already gone" for "removed just now".
        """
        if not await self._references.delete(reference_id, owner_id):
            raise ApiError(ErrorCode.REFERENCE_NOT_FOUND, "No song reference exists with that id.")
