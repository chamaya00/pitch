"""Tests for analysis persistence.

Real temporary directories throughout — the filesystem *is* the thing under
test, so mocking it would prove nothing. The properties that matter are the
ones a half-finished write could violate: a reader never sees a partial record,
a failed write leaves nothing behind, and ``create`` cannot destroy an analysis
that is already in flight.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.errors import ErrorCode
from app.services.analysis.models import (
    Analysis,
    AnalysisStatus,
    Provenance,
    SpeechMetrics,
    Transcript,
    new_analysis_id,
)
from app.services.analysis.repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    AnalysisRepository,
    AnalysisRepositoryError,
    JsonFileAnalysisRepository,
)

MOCK = Provenance(provider="mock", is_mock=True)


def make_analysis(recording_id: str | None = None, **overrides: object) -> Analysis:
    payload: dict[str, object] = {
        "analysis_id": new_analysis_id(),
        "recording_id": recording_id or new_analysis_id(),
    }
    payload.update(overrides)
    return Analysis.model_validate(payload)


def completed(recording_id: str, **overrides: object) -> Analysis:
    return make_analysis(
        recording_id,
        status=AnalysisStatus.COMPLETED,
        transcript=Transcript(text="hello there", provenance=MOCK),
        metrics=SpeechMetrics(word_count=2),
        completed_at=datetime.now(UTC),
        **overrides,
    )


@pytest.fixture
def repository(tmp_path: Path) -> JsonFileAnalysisRepository:
    return JsonFileAnalysisRepository(tmp_path)


def temp_files(repository: JsonFileAnalysisRepository) -> list[Path]:
    if not repository.directory.is_dir():
        return []
    return [path for path in repository.directory.iterdir() if path.name.startswith("analysis-")]


# --- The protocol ----------------------------------------------------------


def test_the_implementation_satisfies_the_protocol(repository: JsonFileAnalysisRepository):
    assert isinstance(repository, AnalysisRepository)


def test_records_land_in_the_documented_location(
    tmp_path: Path, repository: JsonFileAnalysisRepository
):
    analysis = repository.create(make_analysis())
    expected = tmp_path / "analyses" / f"{analysis.analysis_id}.json"

    assert expected.is_file()
    assert repository.directory == tmp_path / "analyses"


# --- Round trip ------------------------------------------------------------


def test_a_created_record_can_be_read_back(repository: JsonFileAnalysisRepository):
    analysis = repository.create(make_analysis())
    assert repository.get(analysis.analysis_id) == analysis


def test_a_completed_record_survives_the_round_trip(repository: JsonFileAnalysisRepository):
    """Everything the analysis carries, including the derived provenance."""
    original = repository.create(completed(new_analysis_id()))
    restored = repository.get(original.analysis_id)

    assert restored == original
    assert restored is not None
    assert restored.transcript is not None
    assert restored.metrics is not None
    assert restored.provenance.is_mock is True
    assert restored.provenance.transcription == MOCK


def test_an_unknown_id_is_absent_rather_than_an_error(repository: JsonFileAnalysisRepository):
    assert repository.get(new_analysis_id()) is None


@pytest.mark.parametrize(
    "bad_id", ["../../etc/passwd", "", "not-hex", "0123456789abcdef", "z" * 32]
)
def test_a_malformed_id_cannot_reach_the_filesystem(
    repository: JsonFileAnalysisRepository, bad_id: str
):
    assert repository.get(bad_id) is None
    assert repository.delete(bad_id) is False
    assert repository.list_for_recording(bad_id) == []


def test_a_corrupt_document_is_a_fault_not_a_missing_record(
    repository: JsonFileAnalysisRepository,
):
    analysis = repository.create(make_analysis())
    path = repository.directory / f"{analysis.analysis_id}.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(AnalysisRepositoryError):
        repository.get(analysis.analysis_id)


# --- Create vs update ------------------------------------------------------


def test_create_refuses_to_overwrite_an_existing_record(
    repository: JsonFileAnalysisRepository,
):
    """A create that clobbered an in-flight analysis would destroy a transcript."""
    analysis = repository.create(make_analysis())
    with pytest.raises(AnalysisAlreadyExistsError):
        repository.create(analysis)


def test_create_leaves_the_original_intact_when_it_refuses(
    repository: JsonFileAnalysisRepository,
):
    original = repository.create(completed(new_analysis_id()))
    replacement = make_analysis(original.recording_id).model_copy(
        update={"analysis_id": original.analysis_id}
    )

    with pytest.raises(AnalysisAlreadyExistsError):
        repository.create(replacement)

    assert repository.get(original.analysis_id) == original


def test_update_overwrites_and_returns_the_new_state(repository: JsonFileAnalysisRepository):
    analysis = repository.create(make_analysis())
    advanced = Analysis.model_validate(
        {**analysis.model_dump(exclude={"provenance"}), "status": AnalysisStatus.TRANSCRIBING}
    )

    returned = repository.update(advanced)

    assert returned == advanced
    assert repository.get(analysis.analysis_id) == advanced


def test_update_refuses_a_record_that_does_not_exist(repository: JsonFileAnalysisRepository):
    with pytest.raises(AnalysisNotFoundError):
        repository.update(make_analysis())


def test_delete_removes_the_record(repository: JsonFileAnalysisRepository):
    analysis = repository.create(make_analysis())

    assert repository.delete(analysis.analysis_id) is True
    assert repository.get(analysis.analysis_id) is None
    assert repository.delete(analysis.analysis_id) is False


# --- Listing ---------------------------------------------------------------


def test_analyses_are_listed_for_their_own_recording_only(
    repository: JsonFileAnalysisRepository,
):
    mine, theirs = new_analysis_id(), new_analysis_id()
    first = repository.create(make_analysis(mine))
    second = repository.create(make_analysis(mine))
    repository.create(make_analysis(theirs))

    listed = repository.list_for_recording(mine)

    assert {item.analysis_id for item in listed} == {first.analysis_id, second.analysis_id}
    assert repository.list_for_recording(new_analysis_id()) == []


def test_analyses_are_listed_newest_first(repository: JsonFileAnalysisRepository):
    recording_id = new_analysis_id()
    now = datetime.now(UTC)
    older = repository.create(make_analysis(recording_id, created_at=now - timedelta(hours=2)))
    newer = repository.create(make_analysis(recording_id, created_at=now))

    listed = repository.list_for_recording(recording_id)
    assert [item.analysis_id for item in listed] == [newer.analysis_id, older.analysis_id]


def test_listing_an_empty_store_is_not_an_error(tmp_path: Path):
    assert JsonFileAnalysisRepository(tmp_path).list_for_recording(new_analysis_id()) == []


def test_one_corrupt_record_does_not_hide_the_others(repository: JsonFileAnalysisRepository):
    """A recording must not become permanently unanalysable over one bad file."""
    recording_id = new_analysis_id()
    good = repository.create(make_analysis(recording_id))
    broken = repository.create(make_analysis(recording_id))
    (repository.directory / f"{broken.analysis_id}.json").write_text("{{{", encoding="utf-8")

    listed = repository.list_for_recording(recording_id)
    assert [item.analysis_id for item in listed] == [good.analysis_id]


def test_stray_files_are_ignored_by_the_scan(repository: JsonFileAnalysisRepository):
    recording_id = new_analysis_id()
    repository.create(make_analysis(recording_id))
    (repository.directory / "notes.txt").write_text("hello", encoding="utf-8")
    (repository.directory / "analysis-abc.json").write_text("{}", encoding="utf-8")

    assert len(repository.list_for_recording(recording_id)) == 1


# --- Durability ------------------------------------------------------------


def test_no_temporary_file_remains_after_a_successful_write(
    repository: JsonFileAnalysisRepository,
):
    analysis = repository.create(make_analysis())
    repository.update(
        Analysis.model_validate(
            {**analysis.model_dump(exclude={"provenance"}), "status": AnalysisStatus.TRANSCRIBING}
        )
    )

    assert temp_files(repository) == []
    assert [path.name for path in repository.directory.iterdir()] == [
        f"{analysis.analysis_id}.json"
    ]


@pytest.mark.parametrize(
    ("operation", "failing_call"),
    [
        # ``create`` publishes with link, ``update`` with replace; both fsync.
        ("create", "link"),
        ("create", "fsync"),
        ("update", "replace"),
        ("update", "fsync"),
    ],
)
def test_no_temporary_file_remains_after_a_failed_write(
    repository: JsonFileAnalysisRepository,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failing_call: str,
):
    """A write that dies part-way leaves the directory as it found it."""
    seed = repository.create(make_analysis())
    original = repository.get(seed.analysis_id)

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, failing_call, explode)

    with pytest.raises(AnalysisRepositoryError):
        if operation == "create":
            repository.create(make_analysis())
        else:
            repository.update(seed)

    monkeypatch.undo()
    assert temp_files(repository) == []
    # And the record that was already there is untouched.
    assert repository.get(seed.analysis_id) == original


def test_a_record_is_never_readable_in_a_half_written_state(
    repository: JsonFileAnalysisRepository, monkeypatch: pytest.MonkeyPatch
):
    """The rename is the only moment the document becomes visible."""
    seed = repository.create(completed(new_analysis_id()))
    observed: list[Analysis | None] = []

    real_replace = os.replace

    def observe(source: object, destination: object) -> None:
        # Whatever a reader sees immediately before the rename is the old
        # record, never a partial new one.
        observed.append(repository.get(seed.analysis_id))
        real_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", observe)
    updated = Analysis.model_validate(
        {
            **seed.model_dump(exclude={"provenance"}),
            "status": AnalysisStatus.FAILED,
            "error_code": ErrorCode.ANALYSIS_PROVIDER_ERROR,
        }
    )
    repository.update(updated)

    assert observed == [seed]
    assert repository.get(seed.analysis_id) == updated


def test_records_are_written_owner_only(repository: JsonFileAnalysisRepository):
    analysis = repository.create(make_analysis())
    path = repository.directory / f"{analysis.analysis_id}.json"

    assert path.stat().st_mode & 0o777 == 0o600
