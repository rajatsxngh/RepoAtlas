"""ASGI middleware that every request passes through (outermost first in ``create_app``)."""

from __future__ import annotations

import re
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from repoatlas.api.errors import ErrorCode, error_response
from repoatlas.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

log = get_logger(__name__)


def request_id_of(scope: Scope) -> str | None:
    state = scope.get("state")
    value = state.get("request_id") if isinstance(state, dict) else None
    return value if isinstance(value, str) else None


class RequestContextMiddleware:
    """Give every request an ID: a well-formed incoming ``X-Request-ID`` or a new one. The ID
    is bound to the log context and echoed in the response, so a reported error can be found
    in the logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            await self.app(scope, receive, send_with_id)


class UnhandledErrorMiddleware:
    """Turn an exception that escaped every handler into a 500 ``internal`` error in the error
    model. It sits inside CORS, so browsers can read the response; the traceback goes to the
    log, never to the client."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            log.exception("unhandled error", method=scope.get("method"), path=scope.get("path"))
            if started:  # too late for an error response; the server closes the connection
                raise
            request_id = request_id_of(scope)
            response = error_response(
                500,
                ErrorCode.INTERNAL,
                "Something went wrong on our side.",
                details={"request_id": request_id} if request_id else None,
            )
            await response(scope, receive, send)


class EncodedKeyMiddleware:
    """Keep a percent-encoded symbol key in one path segment, so the route decodes it once.

    Servers decode the request path before routing, which turns ``%2F`` inside a key (keys hold
    file paths) into ``/`` and splits the key across segments. For paths that name a node,
    this puts the key's raw, still-encoded segment back from ``raw_path``; the route then
    unquotes it exactly once, so a ``%`` that is part of the key survives as ``%25``.
    """

    def __init__(self, app: ASGIApp, *, prefix: str) -> None:
        self.app = app
        self._pattern = re.compile(
            rb"^(" + re.escape(prefix.encode("ascii")) + rb"/snapshots/[^/]+/nodes/)([^/]+)"
            rb"(/neighbors)?$"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            raw = scope.get("raw_path")
            match = self._pattern.match(raw) if isinstance(raw, bytes) else None
            if match is not None and match.group(0).isascii():  # else keep the decoded path
                scope = dict(scope, path=match.group(0).decode("ascii"))
        await self.app(scope, receive, send)


class BodyTooLarge(StarletteHTTPException):
    """Raised while a body streams in past the limit (FastAPI re-raises HTTP exceptions from
    body reads, so the handlers render it as a 413 in the error model)."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(413, f"The request body is larger than {max_bytes:,} bytes.")


class BodyLimitMiddleware:
    """Refuse request bodies over ``max_bytes`` with 413 before they are read into memory: by
    ``Content-Length`` when the client sends one, else while the body streams in."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            response = error_response(
                413,
                ErrorCode.INVALID_REQUEST,
                f"The request body is larger than {self.max_bytes:,} bytes.",
                details={"max_bytes": self.max_bytes},
            )
            await response(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge(self.max_bytes)
            return message

        await self.app(scope, limited_receive, send)
