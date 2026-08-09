"""HTTP middleware shared by API routes."""

import hmac
import logging
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.errors import ErrorCode, _error_response, _handle_unexpected_error
from core.http import REQUEST_ID_HEADER
from core.logging import log_context
from services.uploads import upload_limit_message

logger = logging.getLogger(__name__)

_MULTIPART_OVERHEAD_BYTES = 64 * 1024
RUNTIME_REVISION_HEADER = "X-Cook-Mantra-Runtime-Revision"
RUNTIME_STATUS_STALE_MESSAGE = (
    "Local model status changed. Refresh it before sending a photo."
)
_ORIGIN_HEADER_BYTES = b"origin"
_RUNTIME_REVISION_HEADER_BYTES = RUNTIME_REVISION_HEADER.lower().encode("ascii")


def _safe_request_id(candidate: str | None) -> str:
    if (
        candidate
        and len(candidate) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "-._:/")
            for character in candidate
        )
    ):
        return candidate
    return str(uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a caller-provided or generated request ID to each request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _safe_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started_at = perf_counter()
        status_code = 500
        with log_context(request_id=request_id):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            finally:
                logger.info(
                    "request_completed",
                    extra={
                        "event": "request_completed",
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": round(
                            (perf_counter() - started_at) * 1_000,
                            3,
                        ),
                    },
                )


class UnexpectedErrorMiddleware(BaseHTTPMiddleware):
    """Render unhandled failures inside the outer CORS middleware."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as error:
            return await _handle_unexpected_error(request, error)


class RuntimeRevisionMiddleware:
    """Reject stale browser photo uploads before consuming request bytes."""

    def __init__(self, app: ASGIApp, *, runtime_revision: str) -> None:
        self.app = app
        self._runtime_revision = runtime_revision.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._guards_request(scope):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        has_origin = any(name.lower() == _ORIGIN_HEADER_BYTES for name, _ in headers)
        revisions = [
            value
            for name, value in headers
            if name.lower() == _RUNTIME_REVISION_HEADER_BYTES
        ]
        if not revisions and not has_origin:
            await self.app(scope, receive, send)
            return
        if len(revisions) == 1 and hmac.compare_digest(
            revisions[0], self._runtime_revision
        ):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        response = _error_response(
            request,
            code=ErrorCode.RUNTIME_STATUS_STALE,
            message=RUNTIME_STATUS_STALE_MESSAGE,
            status_code=409,
            retryable=False,
        )
        await response(scope, receive, send)

    @staticmethod
    def _guards_request(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/v1/sessions"
        )


class RequestBodyLimitMiddleware:
    """Bound raw session-upload request bodies before multipart parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_upload_bytes: int,
    ) -> None:
        self.app = app
        self._max_upload_bytes = max_upload_bytes
        self._max_body_bytes = max_upload_bytes + _MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._limits_request(scope):
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_bytes:
            await self._reject(scope, receive, send)
            return

        messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > self._max_body_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _limits_request(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/v1/sessions"
        )

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive=receive)
        response = _error_response(
            request,
            code=ErrorCode.INVALID_REQUEST,
            message=upload_limit_message(self._max_upload_bytes),
            status_code=422,
            retryable=False,
        )
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, raw_value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            value = int(raw_value)
        except ValueError:
            return None
        return value if value >= 0 else None
    return None
