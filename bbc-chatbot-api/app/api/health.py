"""GET /health — System status check."""

import logging

from fastapi import APIRouter

from config.settings import settings
from app.db.supabase import check_connection, supervisor_columns_status
from app.services.scheduler import get_scheduler_health

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Return system health status and service availability."""
    scheduler = get_scheduler_health()
    # Always surface: a sticky False here freezes activity clocks and poisons
    # derived tags (false no_engagement). Ops must see it without log diving.
    supervisor_columns = supervisor_columns_status()
    # Money-path counter: lead writes must never fail silently.
    from app.services.lead_service import LEAD_WRITE_HEALTH
    # Provider fallback counter: primary-model failures must be visible.
    from app.ai.claude import AI_FALLBACK_HEALTH
    # Missed handoffs: every count is a client who asked for a human.
    from app.services.handoff import HANDOFF_HEALTH
    # Generation cascade deaths: each count is a client who got a template
    # because every model failed.
    from app.pipeline.generator import GENERATION_HEALTH
    # CRM push truth: ok = proven (2xx+id); failed/refused = flag stayed false.
    from app.services.crm import CRM_PUSH_HEALTH
    # Executor saturation: the thing nothing measured while we blamed the network
    # for two weeks. peak_in_flight near `workers` is the cliff, not a symptom.
    from app.db.supabase import db_health_snapshot
    from app.api.agent import DEADLINE_HEALTH, heartbeat_health_snapshot
    # Live SSE subscribers: the panel and the widget share a conversation's
    # fan-out now, so a count that never rises means the panel is still polling.
    from app.realtime.manager import manager as _sse
    # Presence gate: aggregate only — never who was blocked, or when.
    from app.api.integration import PRESENCE_GATE_HEALTH
    # Claim races: exactly one wins; the losers are counted, not punished.
    from app.api.conversations import CLAIM_HEALTH
    # Sticky's only defence is the deadline — these two say if it is holding.
    from app.services.routing import STICKY_HEALTH
    # Turns we refused to speak: every count is a moment the bot was about to
    # answer a question nobody asked.
    from app.pipeline.orchestrator import PIPELINE_HEALTH

    if not settings.debug:
        return {
            "status": "ok",
            "scheduler": scheduler,
            "supervisor_columns": supervisor_columns,
            "lead_writes": dict(LEAD_WRITE_HEALTH),
            "ai_fallback": dict(AI_FALLBACK_HEALTH),
            "handoffs_expired": dict(HANDOFF_HEALTH),
            "claims": dict(CLAIM_HEALTH),
            "sticky": dict(STICKY_HEALTH),
            "generation_fallbacks": dict(GENERATION_HEALTH),
            "crm_pushes": dict(CRM_PUSH_HEALTH),
            "db": db_health_snapshot(),
            "heartbeat": heartbeat_health_snapshot(),
            "deadline_skips": dict(DEADLINE_HEALTH),
            "sse": {"subscribers": _sse.subscriber_count()},
            "presence_gate": dict(PRESENCE_GATE_HEALTH),
            "phantom_turns": dict(PIPELINE_HEALTH),
        }

    # Check Supabase connectivity
    db_ok = await check_connection()

    # Queue visibility — DB-backed, so only in the full (debug) payload; the
    # lightweight payload keeps to in-memory counters (UptimeRobot pings it).
    from app.db import supabase as _db
    _queue_stats = await _db.get_queue_stats()
    _load = await _db.get_operator_load(settings.queue_presence_window_seconds)
    _thr = settings.operator_load_threshold
    _load["over_threshold"] = sum(
        1 for v in _load["per_agent"].values() if v["active"] > _thr
    )

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
        "scheduler": scheduler,
        "supervisor_columns": supervisor_columns,
        "lead_writes": dict(LEAD_WRITE_HEALTH),
        "ai_fallback": dict(AI_FALLBACK_HEALTH),
        "handoffs_expired": dict(HANDOFF_HEALTH),
        "claims": dict(CLAIM_HEALTH),
        "sticky": dict(STICKY_HEALTH),
        "queue": _queue_stats,
        "operator_load": _load,
        "generation_fallbacks": dict(GENERATION_HEALTH),
        "crm_pushes": dict(CRM_PUSH_HEALTH),
        "db": db_health_snapshot(),
        "heartbeat": heartbeat_health_snapshot(),
        "deadline_skips": dict(DEADLINE_HEALTH),
        "sse": {"subscribers": _sse.subscriber_count()},
        "presence_gate": dict(PRESENCE_GATE_HEALTH),
        "phantom_turns": dict(PIPELINE_HEALTH),
    }
