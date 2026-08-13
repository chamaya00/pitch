"""Move existing filesystem state into PostgreSQL.

Before Step 7M every record lived as a JSON document under ``storage_root``.
Those installations exist, and their recordings are the whole point of adding
history — so the change is a migration, not a cut-over.

Three properties, in the order they matter:

**Nothing is deleted.** This reads the JSON documents and writes rows. The files
are left exactly where they are, so a failed import costs nothing and the old
state remains available to try again. Removing them is a separate, later,
deliberate act.

**It is idempotent.** Every insert is ``ON CONFLICT DO NOTHING``, so running it
twice imports each record once. That matters because the honest way to handle a
partial failure is to fix the cause and re-run.

**Every imported recording gets an owner.** The pre-7M model had none, so one
owner is minted for the whole import and its token is printed **once**, to
stdout, for the operator to keep. There is no way to recover it afterwards, and
that is stated where it is printed rather than discovered later.

Run it with::

    python -m app.db.import_filesystem

Analyses whose recording is missing from the filesystem are skipped and counted,
not invented: a row referencing a recording that does not exist would fail the
foreign key, and forcing one through would produce an analysis nobody can reach.
"""

import argparse
import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.migrate import apply_migrations
from app.db.pool import Database, execute
from app.services.analysis.models import Analysis
from app.services.analysis.repository import JsonFileAnalysisRepository
from app.services.audio_analysis.models import AudioAnalysis
from app.services.audio_analysis.repository import JsonFileAudioAnalysisRepository
from app.services.owners.credentials import new_credential
from app.services.owners.models import Owner, hash_token, new_owner
from app.services.recordings.models import Recording
from app.services.recordings.repository import JsonFileRecordingRepository

logger = get_logger(__name__)


@dataclass
class ImportReport:
    """What the import did. Printed, and returned so a test can assert on it."""

    owner_id: uuid.UUID | None = None
    recordings: int = 0
    speech_analyses: int = 0
    audio_analyses: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"recordings imported:     {self.recordings}",
            f"speech analyses:         {self.speech_analyses}",
            f"audio analyses:          {self.audio_analyses}",
            f"skipped:                 {len(self.skipped)}",
        ]
        lines.extend(f"  - {reason}" for reason in self.skipped)
        return "\n".join(lines)


def _documents(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


async def import_filesystem(
    database: Database,
    storage_root: Path,
    *,
    owner: Owner,
    token: str,
) -> ImportReport:
    """Import every JSON document under ``storage_root`` into ``database``.

    ``owner`` is created if it does not already exist, and everything imported
    is attributed to it. Returns a report; raises only if the database itself is
    unreachable — a document that will not parse is skipped and named.
    """
    report = ImportReport(owner_id=owner.owner_id)

    # The owner and the way in to it, together. An owner with no credential
    # would be an identity holding every imported recording that nobody could
    # ever reach. Both inserts are ``DO NOTHING`` so a re-run imports neither
    # twice — the credential conflicts on its hash, which is the same value on
    # every run because the token is.
    credential, _ = new_credential(owner.owner_id, "Import key")
    async with database.transaction() as connection:
        await execute(
            connection,
            "INSERT INTO owners (id, created_at) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (owner.owner_id, owner.created_at),
        )
        await execute(
            connection,
            """
            INSERT INTO credentials (id, owner_id, credential_hash, label, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (credential_hash) DO NOTHING
            """,
            (
                credential.credential_id,
                owner.owner_id,
                hash_token(token),
                credential.label,
                owner.created_at,
            ),
        )

    recordings = JsonFileRecordingRepository(storage_root)
    analyses = JsonFileAnalysisRepository(storage_root)
    audio = JsonFileAudioAnalysisRepository(storage_root)

    known: set[str] = set()

    for path in _documents(recordings.directory):
        try:
            recording = Recording.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the import
            report.skipped.append(f"{path.name}: unreadable recording ({type(exc).__name__})")
            continue

        async with database.transaction() as connection:
            affected = await execute(
                connection,
                """
                INSERT INTO recordings (
                    id, owner_id, original_filename, audio_format, duration_seconds,
                    sample_rate, channels, size_bytes, bits_per_sample, created_at, document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    recording.recording_id,
                    owner.owner_id,
                    recording.original_filename,
                    recording.audio_format,
                    recording.duration_seconds,
                    recording.sample_rate,
                    recording.channels,
                    recording.size_bytes,
                    recording.bits_per_sample,
                    recording.created_at,
                    recording.model_dump_json(),
                ),
            )
        known.add(recording.recording_id)
        report.recordings += affected

    for path in _documents(analyses.directory):
        try:
            analysis = Analysis.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            report.skipped.append(f"{path.name}: unreadable analysis ({type(exc).__name__})")
            continue
        if analysis.recording_id not in known:
            report.skipped.append(
                f"{path.name}: recording {analysis.recording_id} is not in the filesystem store"
            )
            continue

        async with database.transaction() as connection:
            affected = await execute(
                connection,
                """
                INSERT INTO speech_analyses
                    (id, recording_id, status, created_at, error_code, document)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    analysis.analysis_id,
                    analysis.recording_id,
                    analysis.status.value,
                    analysis.created_at,
                    analysis.error_code.value if analysis.error_code else None,
                    analysis.model_dump_json(),
                ),
            )
        report.speech_analyses += affected

    for path in _documents(audio.directory):
        try:
            measured = AudioAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            report.skipped.append(f"{path.name}: unreadable audio analysis ({type(exc).__name__})")
            continue
        if measured.recording_id not in known:
            report.skipped.append(
                f"{path.name}: recording {measured.recording_id} is not in the filesystem store"
            )
            continue

        async with database.transaction() as connection:
            affected = await execute(
                connection,
                """
                INSERT INTO audio_analyses
                    (id, recording_id, status, feedback_status, created_at, error_code, document)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    measured.audio_analysis_id,
                    measured.recording_id,
                    measured.status.value,
                    measured.feedback_status.value,
                    measured.created_at,
                    measured.error_code.value if measured.error_code else None,
                    measured.model_dump_json(),
                ),
            )
        report.audio_analyses += affected

    logger.info(
        "filesystem_import_complete",
        extra={
            "owner_id": str(owner.owner_id),
            "recordings": report.recordings,
            "speech_analyses": report.speech_analyses,
            "audio_analyses": report.audio_analyses,
            "skipped": len(report.skipped),
        },
    )
    return report


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=None,
        help="Where the JSON documents live. Defaults to the configured storage root.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.database_url:
        print("DATABASE_URL is not set. Nothing was imported.")
        return 2

    storage_root = args.storage_root or settings.storage_root
    owner, token = new_owner()

    database = Database(settings.database_url)
    await database.open()
    try:
        async with database.transaction() as connection:
            await apply_migrations(connection)
        report = await import_filesystem(database, storage_root, owner=owner, token=token)
    finally:
        await database.close()

    print(report.summary())
    print()
    print("Imported recordings belong to a newly created owner. Its token is printed")
    print("once and cannot be recovered — store it now, and set it as the browser's")
    print("X-VocalLens-Owner value to see this history:")
    print()
    print(f"    {token}")
    print()
    print("The JSON documents were not modified or deleted.")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    raise SystemExit(asyncio.run(_main()))
