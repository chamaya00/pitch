"""Recording upload and history routes."""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, File, Path, Query, UploadFile, status

from app.api.deps import (
    CostlyRequestDep,
    OwnerIdDep,
    RecordingRepositoryDep,
    RecordingStorageDep,
    SettingsDep,
)
from app.api.responses import error_responses
from app.core.errors import ApiError, ErrorCode
from app.schemas.history import RecordingHistoryResponse
from app.schemas.recording import RecordingResponse
from app.services.audio.upload import process_upload
from app.services.recordings.cursor import (
    CURSOR_PATTERN,
    InvalidCursorError,
    decode_cursor,
)

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
        ErrorCode.RATE_LIMITED,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def upload_recording(
    settings: SettingsDep,
    storage: RecordingStorageDep,
    repository: RecordingRepositoryDep,
    owner_id: OwnerIdDep,
    _limit: CostlyRequestDep,
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
        owner_id=owner_id,
    )
    return RecordingResponse.from_recording(recording)


#: How many history rows a request may ask for. History is a list screen, not a
#: bulk export: a client that needs everything is a client this API does not
#: have, and an unbounded LIMIT is how a cheap query becomes an expensive one.
DEFAULT_HISTORY_LIMIT: Final = 50
MAX_HISTORY_LIMIT: Final = 200

#: Recording ids are ``uuid4().hex``. Constraining the path parameter means a
#: malformed id never reaches a query.
RecordingIdPath = Annotated[
    str,
    Path(pattern=r"^[0-9a-f]{32}$", description="Server-generated recording identifier."),
]


@router.get(
    "",
    response_model=RecordingHistoryResponse,
    summary="List your recordings",
    description=(
        "One page of the recordings belonging to the caller, newest first.\n\n"
        "**Ownership is resolved from the `X-VocalLens-Owner` header and "
        "enforced in the database.** A caller with no header is issued a new "
        "identity and sees an empty history — never somebody else's. A caller "
        "cannot ask for another owner's recordings, because there is no "
        "parameter that would let them name one — including `cursor`, which is "
        "a position in a result set that is already scoped to the caller.\n\n"
        "**A page says whether it is the last.** `next_cursor` is `null` only "
        "when there is genuinely nothing older; otherwise pass it back as "
        "`cursor` for the next page. Do not infer completeness from `count`: an "
        "owner with exactly `limit` recordings and one with a thousand return "
        "the same count.\n\n"
        "**Paging is by position, not by offset.** Uploading a recording "
        "between two requests cannot shift the window, so nothing is skipped or "
        "repeated.\n\n"
        "**Statuses, not results.** Each item says how far its analyses got; "
        "the measurements themselves come from the per-recording endpoints. A "
        "`null` status means that analysis has never been run, which is not the "
        "same as pending and not a failure."
    ),
    responses=error_responses(ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR),
)
async def list_recordings(
    repository: RecordingRepositoryDep,
    owner_id: OwnerIdDep,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_HISTORY_LIMIT, description="Largest number of recordings to return."),
    ] = DEFAULT_HISTORY_LIMIT,
    cursor: Annotated[
        str | None,
        Query(
            # The shape of what base64url produces. A value that is not even
            # that is refused here, before anything tries to decode it.
            pattern=CURSOR_PATTERN,
            description=(
                "A `next_cursor` from a previous page. Omit for the newest "
                "recordings. Opaque: this server issued it and only this server "
                "reads it."
            ),
        ),
    ] = None,
) -> RecordingHistoryResponse:
    """Return one page of this owner's recordings with the state of each one's analyses."""
    try:
        position = None if cursor is None else decode_cursor(cursor)
    except InvalidCursorError as exc:
        # A cursor nobody issued is a bad request, not a server fault and not an
        # empty history: answering "you have no more recordings" to a damaged
        # bookmark would be the same untruth this endpoint exists to stop
        # telling.
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "That page marker is not one this server issued. Start from the newest recordings.",
        ) from exc

    page = await repository.list_history(owner_id, limit, position)
    return RecordingHistoryResponse.from_page(page, limit=limit)


@router.get(
    "/{recording_id}",
    response_model=RecordingResponse,
    summary="Get one of your recordings",
    description=(
        "The stored metadata of a single recording.\n\n"
        "A recording belonging to somebody else answers exactly as one that "
        "does not exist: `404`. The two are deliberately indistinguishable, "
        "because a different answer would confirm that an id is real to "
        "somebody with no right to know."
    ),
    responses=error_responses(
        ErrorCode.RECORDING_NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ),
)
async def get_recording(
    recording_id: RecordingIdPath,
    repository: RecordingRepositoryDep,
    owner_id: OwnerIdDep,
) -> RecordingResponse:
    """Return one recording, provided it belongs to the caller."""
    recording = await repository.get(recording_id, owner_id)
    if recording is None:
        raise ApiError(ErrorCode.RECORDING_NOT_FOUND, "That recording could not be found.")
    return RecordingResponse.from_recording(recording)
