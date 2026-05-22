"""GET /health — System status check."""

import logging

from fastapi import APIRouter

from config.settings import settings
from app.db.supabase import check_connection

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Return system health status and service availability."""
    if not settings.debug:
        return {"status": "ok"}

    # Check Supabase connectivity
    db_ok = await check_connection()

    # Check service configuration (don't make live calls)
    claude_ok = bool(settings.anthropic_api_key)
    qdrant_configured = bool(settings.qdrant_url)
    redis_configured = bool(settings.redis_url)

    services = {
        "supabase": "connected" if db_ok else "disconnected",
        "claude": "configured" if claude_ok else "missing_key",
        "qdrant": "configured" if qdrant_configured else "disabled",
        "redis": "configured" if redis_configured else "disabled",
    }

    # Determine overall status
    if not db_ok:
        status = "critical"
    elif not claude_ok:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "version": "1.0.0",
        "services": services,
    }
