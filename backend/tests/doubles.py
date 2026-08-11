"""In-memory repositories, used as test doubles only.

These are **not** a fourth persistence implementation. Nothing in ``app/``
imports this module and nothing can be configured to use it: production has one
store, PostgreSQL, and the JSON repositories survive only as the migration
source. What lives here exists so the orchestration and API suites can run in
milliseconds without a database, exactly as they did before Step 7M.

The obvious risk with a hand-written double is drift — it passes because it
agrees with the test rather than with the database. ``test_repository_contract``
answers that: every behavioural rule below is asserted against *both* this
module and the PostgreSQL repositories, from one parametrised suite, so a double
that disagrees with SQL fails the same test the real one passes.

Concurrency is modelled the way the schema enforces it, not the way an
``asyncio.Lock`` would: the active-analysis rule is a check performed inside a
single non-awaiting section (which is atomic for coroutines on one loop), and
every update is compare-and-set. The PostgreSQL versions get the same guarantees
from a partial unique index and a conditional ``UPDATE``, which is why the
service code above them cannot tell the two apart.
"""

import uuid
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI

from app.api import deps
from app.services.analysis.models import Analysis, AnalysisStatus
from app.services.analysis.postgres_repository import (
    ActiveAnalysisExistsError,
    AnalysisConflictError,
)
from app.services.audio_analysis.models import (
    AudioAnalysis,
    AudioAnalysisStatus,
    AudioFeedbackStatus,
)
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
)
from app.services.comparison.sources import ComparisonSource
from app.services.owners.models import Owner, hash_token, new_owner
from app.services.progress.sources import ProgressRow
from app.services.recordings.history import RecordingHistoryEntry
from app.services.recordings.models import Recording
from app.services.recordings.postgres_repository import RecordingAlreadyExistsError

#: The statuses the partial unique indexes treat as "in flight". Duplicated from
#: the SQL on purpose: if the two ever disagree, the contract suite fails.
ACTIVE_SPEECH_STATUSES: Final[frozenset[AnalysisStatus]] = frozenset(
    {AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING}
)
ACTIVE_AUDIO_STATUSES: Final[frozenset[AudioAnalysisStatus]] = frozenset(
    {AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING}
)


class InMemoryOwnerRepository:
    """Owners in a dictionary.

    Stores the hash rather than the token even here. A double that kept the
    clear value would let a test pass while asserting nothing about the property
    that matters — and the contract suite checks this one against both.
    """

    def __init__(self) -> None:
        self._by_hash: dict[str, Owner] = {}
        self._by_id: dict[uuid.UUID, Owner] = {}

    async def create(self, owner: Owner, token: str) -> Owner:
        self._by_hash[hash_token(token)] = owner
        self._by_id[owner.owner_id] = owner
        return owner

    async def get_by_token(self, token: str) -> Owner | None:
        return self._by_hash.get(hash_token(token))

    async def get(self, owner_id: uuid.UUID) -> Owner | None:
        return self._by_id.get(owner_id)


class InMemoryRecordingRepository:
    """Recordings in a dictionary, scoped by owner on every read.

    ``analyses``/``audio_analyses`` are optional back-references used only by
    :meth:`list_history`, which in PostgreSQL is one statement with two lateral
    joins. Wiring them here keeps the double able to answer the same question
    the real repository answers.
    """

    def __init__(
        self,
        analyses: "InMemoryAnalysisRepository | None" = None,
        audio_analyses: "InMemoryAudioAnalysisRepository | None" = None,
    ) -> None:
        self._records: dict[str, tuple[uuid.UUID, Recording]] = {}
        self._analyses = analyses
        self._audio_analyses = audio_analyses

    async def create(self, recording: Recording, owner_id: uuid.UUID) -> Recording:
        if recording.recording_id in self._records:
            raise RecordingAlreadyExistsError(f"recording {recording.recording_id} already exists")
        self._records[recording.recording_id] = (owner_id, recording)
        return recording

    async def get(self, recording_id: str, owner_id: uuid.UUID) -> Recording | None:
        entry = self._records.get(recording_id)
        # Someone else's recording is not "filtered out" — it is indistinguishable
        # from one that does not exist, which is what the SQL does too.
        if entry is None or entry[0] != owner_id:
            return None
        return entry[1]

    async def list_for_owner(self, owner_id: uuid.UUID, limit: int) -> list[Recording]:
        owned = [record for owner, record in self._records.values() if owner == owner_id]
        owned.sort(key=lambda record: (record.created_at, record.recording_id), reverse=True)
        return owned[:limit]

    async def list_history(self, owner_id: uuid.UUID, limit: int) -> list[RecordingHistoryEntry]:
        entries = []
        for recording in await self.list_for_owner(owner_id, limit):
            speech = (
                await self._analyses.latest_for_recording(recording.recording_id)
                if self._analyses is not None
                else None
            )
            audio = (
                await self._audio_analyses.latest_for_recording(recording.recording_id)
                if self._audio_analyses is not None
                else None
            )
            stamps = [record.created_at for record in (speech, audio) if record is not None]
            entries.append(
                RecordingHistoryEntry(
                    recording=recording,
                    speech_status=speech.status if speech else None,
                    audio_status=audio.status if audio else None,
                    feedback_status=audio.feedback_status if audio else None,
                    last_analysed_at=max(stamps) if stamps else None,
                )
            )
        return entries

    async def comparison_sources(
        self, owner_id: uuid.UUID, recording_ids: list[str]
    ) -> dict[str, ComparisonSource]:
        sources: dict[str, ComparisonSource] = {}
        for recording_id in recording_ids:
            recording = await self.get(recording_id, owner_id)
            # A recording that is not this owner's is absent from the result,
            # exactly as the SQL leaves it unselected — not present-but-flagged.
            if recording is None:
                continue
            analysis = (
                await self._audio_analyses.latest_for_recording(recording_id)
                if self._audio_analyses is not None
                else None
            )
            sources[recording_id] = ComparisonSource(recording=recording, audio_analysis=analysis)
        return sources

    async def progress_rows(self, owner_id: uuid.UUID, limit: int) -> list[ProgressRow]:
        """Mirrors the SQL: the latest `limit` recordings, returned oldest first.

        The window is taken from the *newest* end and then reversed, which is
        what the real query's inner/outer ordering does — a double that sliced
        the oldest `limit` instead would pass its own tests and disagree with
        the database.
        """
        newest = await self.list_for_owner(owner_id, limit)
        rows: list[ProgressRow] = []
        for recording in reversed(newest):
            analysis = (
                await self._audio_analyses.latest_for_recording(recording.recording_id)
                if self._audio_analyses is not None
                else None
            )
            # The query asks only for completed analyses, so anything else reads
            # the same as none.
            if analysis is not None and analysis.status is not AudioAnalysisStatus.COMPLETED:
                analysis = None
            rows.append(_progress_row(recording, analysis))
        return rows

    async def owner_of(self, recording_id: str) -> uuid.UUID | None:
        entry = self._records.get(recording_id)
        return None if entry is None else entry[0]


class InMemoryAnalysisRepository:
    """Speech analyses in a dictionary."""

    def __init__(self) -> None:
        self._records: dict[str, Analysis] = {}

    async def create(self, analysis: Analysis) -> Analysis:
        # No await between the check and the write: on one event loop that is
        # as atomic as the unique index, which is what this stands in for.
        for stored in self._records.values():
            if (
                stored.recording_id == analysis.recording_id
                and stored.status in ACTIVE_SPEECH_STATUSES
            ):
                raise ActiveAnalysisExistsError(
                    f"an analysis of {analysis.recording_id} is already in flight"
                )
        self._records[analysis.analysis_id] = analysis
        return analysis

    async def get(self, analysis_id: str) -> Analysis | None:
        return self._records.get(analysis_id)

    async def update(self, analysis: Analysis, *, expect_status: AnalysisStatus) -> Analysis:
        stored = self._records.get(analysis.analysis_id)
        if stored is None or stored.status is not expect_status:
            raise AnalysisConflictError(
                f"analysis {analysis.analysis_id} is no longer {expect_status.value}"
            )
        self._records[analysis.analysis_id] = analysis
        return analysis

    async def latest_for_recording(self, recording_id: str) -> Analysis | None:
        for analysis in await self.list_for_recording(recording_id):
            return analysis
        return None

    async def list_for_recording(self, recording_id: str) -> list[Analysis]:
        matches = [
            analysis for analysis in self._records.values() if analysis.recording_id == recording_id
        ]
        matches.sort(key=lambda analysis: (analysis.created_at, analysis.analysis_id), reverse=True)
        return matches


class InMemoryAudioAnalysisRepository:
    """Audio analyses in a dictionary."""

    def __init__(self) -> None:
        self._records: dict[str, AudioAnalysis] = {}

    async def create(self, analysis: AudioAnalysis) -> AudioAnalysis:
        for stored in self._records.values():
            if (
                stored.recording_id == analysis.recording_id
                and stored.status in ACTIVE_AUDIO_STATUSES
            ):
                raise ActiveAudioAnalysisExistsError(
                    f"an audio analysis of {analysis.recording_id} is already in flight"
                )
        self._records[analysis.audio_analysis_id] = analysis
        return analysis

    async def get(self, audio_analysis_id: str) -> AudioAnalysis | None:
        return self._records.get(audio_analysis_id)

    async def update(
        self, analysis: AudioAnalysis, *, expect_status: AudioAnalysisStatus
    ) -> AudioAnalysis:
        stored = self._records.get(analysis.audio_analysis_id)
        if stored is None or stored.status is not expect_status:
            raise AudioAnalysisConflictError(
                f"audio analysis {analysis.audio_analysis_id} is no longer {expect_status.value}"
            )
        self._records[analysis.audio_analysis_id] = analysis
        return analysis

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysis | None:
        stored = self._records.get(audio_analysis_id)
        if stored is None or stored.status is not AudioAnalysisStatus.COMPLETED:
            return None
        if stored.feedback_status not in (
            AudioFeedbackStatus.NOT_REQUESTED,
            AudioFeedbackStatus.FAILED,
        ):
            return None
        claimed = AudioAnalysis.model_validate(
            {
                **stored.model_dump(),
                "feedback_status": AudioFeedbackStatus.GENERATING,
                "feedback": None,
                "feedback_error_code": None,
            }
        )
        self._records[audio_analysis_id] = claimed
        return claimed

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        for analysis in await self.list_for_recording(recording_id):
            return analysis
        return None

    async def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        matches = [
            analysis for analysis in self._records.values() if analysis.recording_id == recording_id
        ]
        matches.sort(
            key=lambda analysis: (analysis.created_at, analysis.audio_analysis_id), reverse=True
        )
        return matches


@dataclass(frozen=True, slots=True)
class Doubles:
    """One wired set of in-memory repositories, plus an owner to use.

    Bundled because a test that overrides one of them has to override all of
    them: a service holding the real PostgreSQL repository and a double at the
    same time would half-connect to a database that is not there.
    """

    owners: InMemoryOwnerRepository
    recordings: InMemoryRecordingRepository
    analyses: InMemoryAnalysisRepository
    audio_analyses: InMemoryAudioAnalysisRepository
    owner: Owner
    token: str


async def build_doubles() -> Doubles:
    """A wired set of doubles with one owner already registered."""
    analyses = InMemoryAnalysisRepository()
    audio_analyses = InMemoryAudioAnalysisRepository()
    recordings = InMemoryRecordingRepository(analyses, audio_analyses)
    owners = InMemoryOwnerRepository()
    owner, token = new_owner()
    await owners.create(owner, token)
    return Doubles(
        owners=owners,
        recordings=recordings,
        analyses=analyses,
        audio_analyses=audio_analyses,
        owner=owner,
        token=token,
    )


def override_repositories(app: FastAPI, doubles: Doubles) -> None:
    """Point an application's repository dependencies at ``doubles``.

    Overrides the repositories rather than ``get_database``, so no test ever
    reaches a pool. The application's own lifespan still runs; it finds no
    ``DATABASE_URL`` in the test settings, logs that, and attaches no pool —
    which is exactly the configuration these overrides then make irrelevant.
    """
    app.dependency_overrides[deps.get_owner_repository] = lambda: doubles.owners
    app.dependency_overrides[deps.get_recording_repository] = lambda: doubles.recordings
    app.dependency_overrides[deps.get_analysis_repository] = lambda: doubles.analyses
    app.dependency_overrides[deps.get_audio_analysis_repository] = lambda: doubles.audio_analyses


def _progress_row(recording: Recording, analysis: AudioAnalysis | None) -> ProgressRow:
    """Read the same scalars the SQL extracts by JSONB path."""
    metrics = analysis.metrics if analysis is not None else None
    if metrics is None:
        return ProgressRow(
            recording_id=recording.recording_id,
            recorded_at=recording.created_at,
            original_filename=recording.original_filename,
            recording_duration_seconds=recording.duration_seconds,
            audio_format=recording.audio_format.value,
            analysed=False,
        )
    return ProgressRow(
        recording_id=recording.recording_id,
        recorded_at=recording.created_at,
        original_filename=recording.original_filename,
        recording_duration_seconds=recording.duration_seconds,
        audio_format=recording.audio_format.value,
        analysed=True,
        duration_seconds=metrics.duration_seconds,
        in_tune_ratio=metrics.stability.in_tune_ratio,
        mean_abs_cents_deviation=metrics.stability.mean_abs_cents_deviation,
        cents_std=metrics.stability.cents_std,
        voiced_ratio=metrics.stability.voiced_ratio,
        voiced_frames=metrics.stability.voiced_frames,
        hop_length_samples=metrics.settings.hop_length_samples,
        sample_rate_hz=metrics.settings.sample_rate_hz,
        semitone_span=None if metrics.pitch is None else metrics.pitch.semitone_span,
        lowest_note=None if metrics.pitch is None else metrics.pitch.lowest_note,
        highest_note=None if metrics.pitch is None else metrics.pitch.highest_note,
    )
