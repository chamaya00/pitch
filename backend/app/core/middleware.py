"""ASGI middleware.

Guards that have to act on the raw request, before routing and before the
multipart parser has had a chance to consume anything.
"""

from typing import Final

from starlette.datastructures import MutableHeaders
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


#: What a JSON API response is allowed to do, which is nothing.
#:
#: Step 10.5 shipped ``X-Content-Type-Options``, ``Referrer-Policy`` and
#: ``X-Frame-Options`` at the edge, and Step 10.9 gave the *web app* a
#: nonce-based policy. ``limitations.md`` recorded what was left: responses from
#: ``/api/`` carried no policy of their own, and ``nosniff`` — which stops a
#: browser treating one as a document — is a narrower guarantee than a policy.
#: This is the policy, and every directive says the same thing a different way:
#: a response that is data has no business loading, framing, submitting or
#: executing anything.
#:
#: ``sandbox`` is the one that matters if the others are ever bypassed. It
#: applies only when a browser *does* treat the response as a document, and it
#: then gives that document a unique origin with no scripts, no forms and no
#: plugins — so the failure ``nosniff`` prevents becomes survivable rather than
#: merely unlikely.
#:
#: Set on the response, unlike the web app's policy, because there is no nonce
#: to mint: nothing served here is a document with scripts in it. That is also
#: why this one can be a constant.
API_SECURITY_POLICY: Final = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox"
)


class ApiSecurityPolicyMiddleware:
    """Attach :data:`API_SECURITY_POLICY` to every response but the docs UIs.

    Registered outermost, so a response produced by another middleware — the
    ``FILE_TOO_LARGE`` refusal above, a CORS preflight — carries it too. A
    response that never reached a route is exactly the kind that should not be
    the one without a policy.

    **The exemptions are the interactive documentation, and they are read from
    the application rather than written here.** ``/docs`` and ``/redoc`` are
    HTML documents that load Swagger UI and ReDoc from a CDN, and handing them
    ``default-src 'none'`` would render two blank pages. They are also not
    reachable through the shipped deployment at all — ``deploy/nginx`` routes
    only ``/api/`` to this application — so what is exempted is a development
    convenience rather than a public surface.
    """

    def __init__(self, app: ASGIApp, *, exempt_paths: frozenset[str]) -> None:
        self.app = app
        self._exempt = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # Set rather than append: a browser given two policies enforces
                # both, and the intersection of two policies nobody wrote
                # together is not a policy anybody chose.
                headers["content-security-policy"] = API_SECURITY_POLICY
            await send(message)

        await self.app(scope, receive, send_with_policy)
