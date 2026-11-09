"""Request middleware: the request id every log line carries, and an access
line only for the requests that need a person.

Railway's HTTP log already holds every request — path, status, duration, the
client's IP and browser — and filters on all of them (@httpStatus,
@totalDuration, @requestId). Writing each one again here, plus uvicorn's own
access line, was 89% of our log on 9 Nov, and the volume that made Railway
drop 75,211 lines in a week, in the very minutes of the incidents. A request
that failed on our side (5xx) or was slow gets a WARNING; every other one is
silent here and complete in Railway's HTTP log.

The id is Railway's X-Railway-Request-Id when it sent one, so `@requestId:<rid>`
in the HTTP log finds the request any of our lines came from. It goes back to
the client in X-Request-ID, as before.
"""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.flags import flag
from app.log_setup import request_id_var

logger = logging.getLogger("bbc.access")

_RID_SHAPE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _request_id(request: Request) -> str:
    """Railway's id when the header looks like one, ours otherwise. It only
    correlates lines; nothing is decided on it, so a forged value can at most
    mislabel this request's own lines."""
    sent = (request.headers.get("x-railway-request-id") or "").strip()
    return sent if _RID_SHAPE.match(sent) else uuid.uuid4().hex[:12]


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = _request_id(request)
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            # Decoy key replayed in a header or query value → alert, once per hour
            # per IP. Body is NOT read here (SSE/uploads); PR-11's ASGI middleware
            # covers the body. Never blocks the request in PR-3 (alert-only).
            try:
                from app.security.decoys import scan_headers_and_query

                await scan_headers_and_query(request)
            except Exception as _e:  # noqa: silent — a tripwire must never break a real request
                logger.debug(f"decoy scan skipped: {type(_e).__name__}")

            response = await call_next(request)

            elapsed = int((time.perf_counter() - start) * 1000)
            status = response.status_code
            slow_ms = int(flag("LOG_SLOW_REQUEST_MS", 2000))
            if status >= 500 or elapsed >= slow_ms:
                logger.warning(
                    f"{request.method} {request.url.path} | {status} | {elapsed}ms",
                    extra={
                        "event": "http.5xx" if status >= 500 else "http.slow",
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": elapsed,
                    },
                )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)
