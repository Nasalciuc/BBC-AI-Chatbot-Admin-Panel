"""Request logging middleware — logs every request with timing."""

import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("bbc.access")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        start = time.perf_counter()

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
        logger.info(
            f"{request.method} {request.url.path} | "
            f"{response.status_code} | {elapsed}ms | rid={request_id}"
        )
        response.headers["X-Request-ID"] = request_id
        return response
