"""Recording upload routes."""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import RecordingRepositoryDep, RecordingStorageDep, SettingsDep
from app.api.responses import error_responses
from app.core.errors import ApiError, ErrorCode
from app.schemas.recording import RecordingResponse
from app.services.audio.upload import process_upload

router = APIRouter(prefix="/recordings", tags=["recordings"])

_READ_CHUNK_BYTES: Final = 1024 * 1024


async def _iter_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    """Yield the upload in bounded chunks, never materialising it whole."""
    while chunk := await upload.read(_READ_CHUNK_BYTES):
        yield chunk


@router.post(
    "",
    response_model=RecordingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a recording",
    description=(
        "Upload a vocal recording for analysis.\n\n"
        "**Supported formats:** WAV and MP3. The file's actual contents are "
        "inspected — the extension and `Content-Type` are not trusted, and an "
        "extension that disagrees with the contents is rejected.\n\n"
        "**Limits:** size and duration come from server configuration "
        "(50 MB and 5 minutes by default). Duration is read from the audio "
        "itself, not from the file name.\n\n"
        "The uploaded file name is kept only as display metadata; the stored "
        "file is named from a server-generated identifier."
    ),
    responses=error_responses(
        ErrorCode.INVALID_FILENAME,
        ErrorCode.FORMAT_MISMATCH,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UNSUPPORTED_FORMAT,
        ErrorCode.AUDIO_TOO_LONG,
        ErrorCode.CORRUPTED_AUDIO,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def upload_recording(
    settings: SettingsDep,
    storage: RecordingStorageDep,
    repository: RecordingRepositoryDep,
    file: Annotated[
        UploadFile | str,
        File(description="The audio file to analyse (WAV or MP3)."),
    ],
) -> RecordingResponse:
    """Validate, store and register an uploaded recording."""
    if isinstance(file, str):
        # A multipart part carrying no filename — what a browser sends for an
        # empty file input. Starlette hands it over as a plain form value, so it
        # never reaches the pipeline's own filename check.
        #
        # Tested for ``str`` rather than ``UploadFile``: under a union FastAPI
        # yields starlette's UploadFile, which is not an instance of FastAPI's
        # subclass, so the positive check silently rejects every valid upload.
        raise ApiError(
            ErrorCode.INVALID_FILENAME,
            "The upload did not include a file. Please choose a file and try again.",
        )

    recording = await process_upload(
        chunks=_iter_chunks(file),
        filename=file.filename,
        content_type=file.content_type,
        settings=settings,
        storage=storage,
        repository=repository,
    )
    return RecordingResponse.from_recording(recording)
