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
from datetime import datetime, timedelta
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
    AudioAnalysisSummary,
    AudioFeedbackStatus,
    DecimatedTimeline,
    PitchFields,
    TimelineFields,
)
from app.services.audio_analysis.postgres_repository import (
    ActiveAudioAnalysisExistsError,
    AudioAnalysisConflictError,
)
from app.services.comparison.sources import ComparisonSource
from app.services.owners.credentials import (
    Credential,
    CredentialExistsError,
    LastCredentialError,
    credential_hash,
    new_credential,
)
from app.services.owners.identity import OwnerDataSummary
from app.services.owners.models import Owner, new_owner
from app.services.progress.sources import ProgressRow
from app.services.recordings.cursor import HistoryCursor, encode_cursor
from app.services.recordings.history import RecordingHistoryEntry, RecordingHistoryPage
from app.services.recordings.models import Recording, utc_now
from app.services.recordings.postgres_repository import RecordingAlreadyExistsError

#: The statuses the partial unique indexes treat as "in flight". Duplicated from
#: the SQL on purpose: if the two ever disagree, the contract suite fails.
ACTIVE_SPEECH_STATUSES: Final[frozenset[AnalysisStatus]] = frozenset(
    {AnalysisStatus.PENDING, AnalysisStatus.TRANSCRIBING, AnalysisStatus.ANALYZING}
)
ACTIVE_AUDIO_STATUSES: Final[frozenset[AudioAnalysisStatus]] = frozenset(
    {AudioAnalysisStatus.PENDING, AudioAnalysisStatus.ANALYZING}
)


class CredentialStore:
    """The ``credentials`` table, as a dictionary keyed by hash.

    One store backs both :class:`InMemoryOwnerRepository` and
    :class:`InMemoryCredentialRepository`, mirroring PostgreSQL, where one table
    serves both protocols. Two separate stores would let a key resolve through
    one object and not the other — a disagreement the real schema cannot have.

    The hash is the key here as it is there: a double holding the clear value
    would let a test pass while asserting nothing about the property that
    matters.
    """

    def __init__(self) -> None:
        self._by_hash: dict[str, Credential] = {}

    def add(self, credential: Credential, key: str) -> Credential:
        stored = credential_hash(key)
        if stored in self._by_hash:  # the UNIQUE constraint on ``credential_hash``
            raise CredentialExistsError("that credential is already registered")
        self._by_hash[stored] = credential
        return credential

    def find(self, key: str) -> Credential | None:
        return self._by_hash.get(credential_hash(key))

    def for_owner(self, owner_id: uuid.UUID) -> list[Credential]:
        owned = [c for c in self._by_hash.values() if c.owner_id == owner_id]
        return sorted(owned, key=lambda c: (c.created_at, str(c.credential_id)))

    def remove(self, credential_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        """Scoped by owner, so somebody else's credential is simply not found."""
        for stored, credential in list(self._by_hash.items()):
            if credential.credential_id == credential_id and credential.owner_id == owner_id:
                del self._by_hash[stored]
                return True
        return False

    def drop_owner(self, owner_id: uuid.UUID) -> None:
        """What ``ON DELETE CASCADE`` does when the owner row goes."""
        for stored, credential in list(self._by_hash.items()):
            if credential.owner_id == owner_id:
                del self._by_hash[stored]


class InMemoryOwnerRepository:
    """Owners in a dictionary.

    Since Step 10.2 an owner is no longer a key: ``create`` writes the owner and
    its first credential together, and ``get_by_token`` resolves through the
    credential store — exactly as the SQL joins ``credentials`` to ``owners``.
    """

    def __init__(
        self,
        recordings: "InMemoryRecordingRepository | None" = None,
        credentials: CredentialStore | None = None,
    ) -> None:
        self._by_id: dict[uuid.UUID, Owner] = {}
        #: The activity signal migration 0003 adds to ``owners``.
        self._last_seen: dict[uuid.UUID, datetime] = {}
        self._credentials = credentials if credentials is not None else CredentialStore()
        # Optional back-reference so the identity summary and the cascade can be
        # answered from the same data the other doubles hold, rather than from a
        # second copy that could disagree with them.
        self._recordings = recordings

    @property
    def credentials(self) -> CredentialStore:
        """The shared store, so a credential double can be built over the same rows."""
        return self._credentials

    async def create(self, owner: Owner, token: str) -> Owner:
        credential, _ = new_credential(owner.owner_id, "Original key")
        self._credentials.add(credential.model_copy(update={"created_at": owner.created_at}), token)
        self._by_id[owner.owner_id] = owner
        self._last_seen[owner.owner_id] = owner.created_at
        return owner

    async def get_by_token(self, token: str) -> Owner | None:
        credential = self._credentials.find(token)
        return None if credential is None else self._by_id.get(credential.owner_id)

    async def get(self, owner_id: uuid.UUID) -> Owner | None:
        return self._by_id.get(owner_id)

    async def touch(self, owner_id: uuid.UUID, stale_after: timedelta) -> None:
        """Same throttle as the SQL: rewrite only what is already stale."""
        seen = self._last_seen.get(owner_id)
        now = utc_now()
        if seen is None or seen < now - stale_after:
            self._last_seen[owner_id] = now

    def last_seen(self, owner_id: uuid.UUID) -> datetime | None:
        """For tests: what the activity signal currently says."""
        return self._last_seen.get(owner_id)

    def set_last_seen(self, owner_id: uuid.UUID, when: datetime) -> None:
        """For tests: age an identity without waiting a month."""
        self._last_seen[owner_id] = when

    async def expired_owner_ids(self, cutoff: datetime, limit: int) -> list[uuid.UUID]:
        """Empty identities not seen since ``cutoff``, oldest first."""
        candidates = [
            owner_id
            for owner_id, seen in self._last_seen.items()
            if owner_id in self._by_id and seen < cutoff and not self._owns_recordings(owner_id)
        ]
        candidates.sort(key=lambda owner_id: self._last_seen[owner_id])
        return candidates[:limit]

    async def claim_expired_owner(self, owner_id: uuid.UUID, cutoff: datetime) -> bool:
        """Re-check and delete in one non-awaiting section.

        Atomic for coroutines on one loop, which is the same guarantee the real
        repository gets from ``FOR UPDATE`` — see the module docstring.
        """
        seen = self._last_seen.get(owner_id)
        if owner_id not in self._by_id or seen is None or seen >= cutoff:
            return False
        if self._owns_recordings(owner_id):
            return False
        self._by_id.pop(owner_id, None)
        self._credentials.drop_owner(owner_id)
        self._last_seen.pop(owner_id, None)
        if self._recordings is not None:
            self._recordings.delete_for_owner(owner_id)
        return True

    def _owns_recordings(self, owner_id: uuid.UUID) -> bool:
        if self._recordings is None:
            return False
        return any(owner == owner_id for owner, _ in self._recordings.records())

    async def data_summary(self, owner_id: uuid.UUID) -> OwnerDataSummary:
        """Counts, taken from the sibling doubles rather than a second store.

        **Every** analysis of a recording is considered, not the latest one.
        Until Step 10.19 this asked ``latest_audio_analysis`` and counted what
        that one said, which is a different question: a recording measured
        yesterday and re-analysed a minute ago counted as unmeasured while the
        new run was pending, and one whose feedback was generated on an earlier
        analysis counted as having none. SQL had always looked at every
        analysis, so the two stores disagreed and the contract suite had never
        asked.
        """
        recordings = self._recordings
        if recordings is None:
            return OwnerDataSummary(recordings=0, analysed_recordings=0, ai_feedback=0)
        owned = [record for owner, record in recordings.records() if owner == owner_id]
        analysed = 0
        feedback = 0
        for record in owned:
            analyses = await recordings.audio_analyses_of(record.recording_id)
            if any(one.status is AudioAnalysisStatus.COMPLETED for one in analyses):
                analysed += 1
            if any(one.feedback_status is AudioFeedbackStatus.COMPLETED for one in analyses):
                feedback += 1
        return OwnerDataSummary(
            recordings=len(owned), analysed_recordings=analysed, ai_feedback=feedback
        )

    async def recording_ids(self, owner_id: uuid.UUID) -> list[str]:
        if self._recordings is None:
            return []
        return sorted(
            record.recording_id for owner, record in self._recordings.records() if owner == owner_id
        )

    async def delete_owner(self, owner_id: uuid.UUID) -> bool:
        """Cascades by hand, exactly as the foreign keys do in PostgreSQL."""
        owner = self._by_id.pop(owner_id, None)
        self._last_seen.pop(owner_id, None)
        self._credentials.drop_owner(owner_id)
        if self._recordings is not None:
            self._recordings.delete_for_owner(owner_id)
        return owner is not None


class InMemoryCredentialRepository:
    """The credential protocol over the same store the owner double uses.

    Built from an :class:`InMemoryOwnerRepository` rather than standing alone,
    because in PostgreSQL there is one table: a key created through one object
    must resolve through the other.
    """

    def __init__(self, owners: InMemoryOwnerRepository) -> None:
        self._owners = owners
        self._store = owners.credentials

    async def create(self, credential: Credential, key: str) -> Credential:
        return self._store.add(credential, key)

    async def owner_for_key(self, key: str) -> uuid.UUID | None:
        credential = self._store.find(key)
        return None if credential is None else credential.owner_id

    async def credential_for_key(self, key: str) -> uuid.UUID | None:
        credential = self._store.find(key)
        return None if credential is None else credential.credential_id

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Credential]:
        return self._store.for_owner(owner_id)

    async def revoke(self, credential_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        """Never the last one.

        The count and the delete sit in one non-awaiting section, which is
        atomic for coroutines on a single loop — the same guarantee the real
        repository gets from ``SELECT … FOR UPDATE`` on the owner row.

        Refusal is checked only when the credential named is genuinely this
        owner's: a request naming somebody else's must fall through to "not
        found" rather than reveal a count.
        """
        owned = self._store.for_owner(owner_id)
        if len(owned) <= 1 and any(c.credential_id == credential_id for c in owned):
            raise LastCredentialError("an owner must keep at least one credential")
        return self._store.remove(credential_id, owner_id)


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

    async def list_history(
        self, owner_id: uuid.UUID, limit: int, cursor: HistoryCursor | None = None
    ) -> RecordingHistoryPage:
        owned = [record for owner, record in self._records.values() if owner == owner_id]
        owned.sort(key=lambda record: (record.created_at, record.recording_id), reverse=True)
        if cursor is not None:
            # The same row-wise comparison the SQL makes: strictly older, or the
            # same instant and a lower id.
            owned = [
                record
                for record in owned
                if (record.created_at, record.recording_id)
                < (cursor.created_at, cursor.recording_id)
            ]
        # One more than asked for, so the page can say whether another follows —
        # exactly what the query's `LIMIT limit + 1` is for.
        window = owned[: limit + 1]
        has_more = len(window) > limit
        visible = window[:limit]

        entries = []
        for recording in visible:
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
        return RecordingHistoryPage(
            entries=entries,
            next_cursor=(
                encode_cursor(visible[-1].created_at, visible[-1].recording_id)
                if has_more and visible
                else None
            ),
        )

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
            # The fields the comparison folds, not the frames — the statement
            # projects them for both sides at once, and this asks the read that
            # produces the same shape. Going through ``latest_for_recording``
            # would report the comparison as loading two whole timelines when
            # the only thing loading one is the double.
            analysis = (
                await self._audio_analyses.latest_fields_for_recording(recording_id)
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

    # --- Accessors for the owner double, not part of any production protocol.

    def records(self) -> list[tuple[uuid.UUID, Recording]]:
        return list(self._records.values())

    async def latest_audio_analysis(self, recording_id: str) -> AudioAnalysis | None:
        if self._audio_analyses is None:
            return None
        return await self._audio_analyses.latest_for_recording(recording_id)

    async def audio_analyses_of(self, recording_id: str) -> list[AudioAnalysisSummary]:
        """Every analysis of a recording, which is what the owner summary counts.

        A recording can be analysed more than once, and "has this been
        measured?" is a question about all of them rather than about the newest.
        Summaries, because the counts read status and feedback status and
        nothing else.
        """
        if self._audio_analyses is None:
            return []
        return await self._audio_analyses.list_summaries_for_recording(recording_id)

    def delete_for_owner(self, owner_id: uuid.UUID) -> None:
        """What ``ON DELETE CASCADE`` does in the real schema.

        Both analysis tables reference ``recordings(id) ON DELETE CASCADE``, so
        removing a recording removes its analyses too. The contract suite caught
        this double deleting only the recordings.
        """
        for recording_id, (owner, _) in list(self._records.items()):
            if owner != owner_id:
                continue
            del self._records[recording_id]
            if self._analyses is not None:
                self._analyses.delete_for_recording(recording_id)
            if self._audio_analyses is not None:
                self._audio_analyses.delete_for_recording(recording_id)


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

    def delete_for_recording(self, recording_id: str) -> None:
        """The cascade from ``recordings``. Not part of any production protocol."""
        for stored_id, analysis in list(self._records.items()):
            if analysis.recording_id == recording_id:
                del self._records[stored_id]


def _summary_of(analysis: AudioAnalysis) -> AudioAnalysisSummary:
    """Drop the timeline, exactly as ``document - 'pitch_points'`` does in SQL.

    Built by *removing* the points rather than by copying fields across, so a
    field added to the record cannot go missing from the summary here while the
    database keeps returning it.
    """
    return AudioAnalysisSummary.model_validate(analysis.model_dump(exclude={"pitch_points"}))


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

    async def summary(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
        stored = self._records.get(audio_analysis_id)
        return None if stored is None else _summary_of(stored)

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

    async def claim_feedback(self, audio_analysis_id: str) -> AudioAnalysisSummary | None:
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
        # The SQL claims by conditional UPDATE and returns the document with its
        # timeline stripped; returning the whole record here would let a caller
        # depend on points the database never sends.
        return _summary_of(claimed)

    async def latest_for_recording(self, recording_id: str) -> AudioAnalysis | None:
        for analysis in await self.list_for_recording(recording_id):
            return analysis
        return None

    async def latest_summary_for_recording(self, recording_id: str) -> AudioAnalysisSummary | None:
        for analysis in await self.list_summaries_for_recording(recording_id):
            return analysis
        return None

    async def latest_decimated_for_recording(
        self, recording_id: str, *, max_points: int
    ) -> DecimatedTimeline | None:
        """Every ``n``-th point, where the SQL selects them with ``WITH ORDINALITY``.

        The stride is derived the same way and the slice starts at the same
        index, so the double and the database return the same points for the
        same stored timeline — which the contract suite asserts rather than
        assumes.
        """
        if max_points < 1:
            raise ValueError("max_points must be at least 1")
        # Deliberately not via ``latest_for_recording``: in PostgreSQL this is
        # its own statement, and the suites that count which read a route asked
        # for patch that method. Going through it here would report the graph as
        # loading a whole timeline when the only thing loading one is the double.
        stored = next(iter(await self.list_for_recording(recording_id)), None)
        if stored is None:
            return None
        decimation = max(1, -(-len(stored.pitch_points) // max_points))
        return DecimatedTimeline(
            analysis=_summary_of(stored),
            points=stored.pitch_points[::decimation],
            decimation=decimation,
        )

    async def latest_fields_for_recording(self, recording_id: str) -> TimelineFields | None:
        """The fields the two aggregations fold, where the SQL projects them.

        PostgreSQL never builds the frames; this takes them apart again after
        the fact. The contract suite asserts that both routes arrive at the same
        arrays for the same stored timeline — a fold reading ``cents`` that
        belong to other notes would produce a breakdown that looks entirely
        reasonable, so "the same" is asserted rather than assumed.
        """
        # Not via ``latest_for_recording``: in PostgreSQL this is its own
        # statement, and the suites that count which read a route asked for
        # patch that method. See the note on the decimated read above.
        stored = next(iter(await self.list_for_recording(recording_id)), None)
        if stored is None:
            return None
        return TimelineFields(
            analysis=_summary_of(stored),
            fields=PitchFields.of_points(stored.pitch_points),
        )

    async def list_for_recording(self, recording_id: str) -> list[AudioAnalysis]:
        matches = [
            analysis for analysis in self._records.values() if analysis.recording_id == recording_id
        ]
        matches.sort(
            key=lambda analysis: (analysis.created_at, analysis.audio_analysis_id), reverse=True
        )
        return matches

    async def list_summaries_for_recording(self, recording_id: str) -> list[AudioAnalysisSummary]:
        return [_summary_of(analysis) for analysis in await self.list_for_recording(recording_id)]

    def delete_for_recording(self, recording_id: str) -> None:
        """The cascade from ``recordings``. Not part of any production protocol."""
        for stored_id, analysis in list(self._records.items()):
            if analysis.recording_id == recording_id:
                del self._records[stored_id]


@dataclass(frozen=True, slots=True)
class Doubles:
    """One wired set of in-memory repositories, plus an owner to use.

    Bundled because a test that overrides one of them has to override all of
    them: a service holding the real PostgreSQL repository and a double at the
    same time would half-connect to a database that is not there.
    """

    owners: InMemoryOwnerRepository
    credentials: InMemoryCredentialRepository
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
    owners = InMemoryOwnerRepository(recordings)
    # Over the same store, not a second one: a key minted through the owner
    # repository has to resolve through the credential repository.
    credentials = InMemoryCredentialRepository(owners)
    owner, token = new_owner()
    await owners.create(owner, token)
    return Doubles(
        owners=owners,
        credentials=credentials,
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
    app.dependency_overrides[deps.get_owner_data_repository] = lambda: doubles.owners
    app.dependency_overrides[deps.get_credential_repository] = lambda: doubles.credentials
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
