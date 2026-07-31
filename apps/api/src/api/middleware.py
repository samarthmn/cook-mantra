"""HTTP middleware shared by API routes."""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from core.http import REQUEST_ID_HEADER
from core.logging import log_context

logger = logging.getLogger(__name__)


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
