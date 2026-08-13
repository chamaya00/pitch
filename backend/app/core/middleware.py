"""ASGI middleware.

Guards that have to act on the raw request, before routing and before the
multipart parser has had a chance to consume anything.
"""

from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.schemas.error import ErrorResponse

logger = get_logger(__name__)

_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})

_TOO_LARGE_MESSAGE: Final = "The uploaded file is larger than this server accepts."


class _BodyTooLargeError(Exception):
    """Raised from the wrapped receive channel once the cap is passed."""


class MaxBodySizeMiddleware:
    """Reject request bodies larger than ``max_bytes`` while they arrive.

    The endpoint counts bytes too, and that check is the authoritative one for
    the upload contract. This middleware exists for a different reason: FastAPI
    hands a route its ``UploadFile`` only *after* Starlette's multipart parser
    has consumed the whole body, spooling it to a temporary file on disk. By
    then an unbounded upload has already cost unbounded disk, whatever the route
    later decides. Counting here stops that at the source.

    ``Content-Length`` is used only as a fast path — it is a client-supplied
    claim, so a request that lies about it or omits it entirely is still caught
    by the running total.

    A production deployment should also cap the body at its reverse proxy; this
    is defence in depth, not a replacement for it.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        if self._declared_length_exceeds_limit(scope):
            # No body read at all: the client told us it would be too big.
            await self._reject(scope, send, reason="content_length")
            return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if response_started:
                # Headers are already on the wire; nothing safe left to say.
                logger.warning("request_body_too_large_after_response_started")
                return
            await self._reject(scope, send, reason="streamed")

    def _declared_length_exceeds_limit(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value) > self.max_bytes
                except ValueError:
                    return False
        return False

    async def _reject(self, scope: Scope, send: Send, *, reason: str) -> None:
        logger.warning(
            "request_body_too_large",
            extra={
                "reason": reason,
                "method": scope.get("method"),
                "path": scope.get("path"),
            },
        )
        body = ErrorResponse(
            error_code=ErrorCode.FILE_TOO_LARGE.value,
            message=_TOO_LARGE_MESSAGE,
        ).model_dump_json()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body.encode()})
