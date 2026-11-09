"""FastAPI application — CORS, middleware, router includes."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.conversations import router as conversations_router
from app.api.leads import router as leads_router
from app.api.kb import router as kb_router
from app.api.dashboard import router as dashboard_router
from app.api.users import router as users_router
from app.api.teams import router as teams_router
from app.api.agent import router as agent_router
from app.api.integration import router as integration_router
from app.api.tasks import router as tasks_router
from app.api.notifications import router as notifications_router
from app.api.lessons import router as lessons_router
from app.api.auth_routes import router as auth_router
from app.api.cron import router as cron_router
from app.api.canary import router as canary_router
from app.security.auth import get_current_user
from app.security.request_logger import RequestLoggerMiddleware

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# httpx logs the full URL of every Supabase request at INFO — PostgREST puts
# filters and looked-up values in the query string, so that is PII at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

# ── CORS ──────────────────────────────────────────────────────
# settings.cors_origins: parsed from CORS_ORIGINS env + REQUIRED_CORS_ORIGINS merge
origins = settings.cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request Logger ────────────────────────────────────────────
app.add_middleware(RequestLoggerMiddleware)

# ── Routers ───────────────────────────────────────────────────
# Health: PUBLIC (no auth — Railway healthcheck needs it)
app.include_router(health_router, tags=["health"])

# Canary content: PUBLIC decoys (/.env, /api/admin/export). Included before the
# admin routers so /api/admin/export resolves here, not to a 404 that an admin
# route might later shadow.
app.include_router(canary_router, tags=["canary"])

# Chat: PUBLIC (customer widget — no auth)
app.include_router(chat_router, prefix="/api", tags=["chat"])

# Cron: PUBLIC with CRON_SECRET header (not JWT)
app.include_router(cron_router, prefix="/api", tags=["cron"])

# Auth: PUBLIC login, protected invite (auth dependency inside the route)
app.include_router(auth_router)

# Integration: PUBLIC by design — the caller is the CRM's BACKEND, not a
# panel user, and its auth is the CRM-signed token (same shared secret as
# /sso/crm-exchange). Read-only: it answers questions, it changes nothing.
app.include_router(integration_router, prefix="/api", tags=["integration"])

# Admin: ALL PROTECTED by get_current_user (Basic + Bearer)
from fastapi import Depends
admin_deps = [Depends(get_current_user)]
app.include_router(conversations_router, prefix="/api", tags=["conversations"], dependencies=admin_deps)
app.include_router(leads_router,         prefix="/api", tags=["leads"],         dependencies=admin_deps)
app.include_router(kb_router,            prefix="/api", tags=["kb"],            dependencies=admin_deps)
app.include_router(dashboard_router,     prefix="/api", tags=["dashboard"],     dependencies=admin_deps)
app.include_router(users_router,         prefix="/api", tags=["users"],         dependencies=admin_deps)
app.include_router(teams_router,         prefix="/api", tags=["teams"],         dependencies=admin_deps)
app.include_router(agent_router,         prefix="/api", tags=["agent"],         dependencies=admin_deps)
app.include_router(tasks_router,         prefix="/api", tags=["tasks"],         dependencies=admin_deps)
app.include_router(notifications_router, prefix="/api", tags=["notifications"], dependencies=admin_deps)
app.include_router(lessons_router,       prefix="/api", tags=["lessons"],       dependencies=admin_deps)


# ── Startup / shutdown ────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    logger.info(f"{settings.app_name} starting — v1.0.0")
    logger.info(f"CORS origins: {settings.cors_origins}")
    logger.info(f"Claude Haiku: {settings.claude_haiku_model}")
    logger.info(f"Claude Sonnet: {settings.claude_sonnet_model}")
    logger.info(f"Qdrant: {'configured' if settings.qdrant_url else 'disabled'}")
    logger.info(f"Redis: {'configured' if settings.redis_url else 'disabled'}")
    logger.info(f"Budget: ${settings.daily_budget}/day, ${settings.per_conversation_budget}/conv")
    if not settings.ops_alert_email:
        logger.warning("ops alerts fall back to super@ — set OPS_ALERT_EMAIL")

    # Runtime switches (app/flags.py): read once now so the first requests see
    # the database's values, then every 30 s in the background.
    # Bounded: a slow database must not hold the healthcheck; the refresher
    # retries in the background either way.
    import asyncio

    from app import flags as runtime_flags

    try:
        await asyncio.wait_for(runtime_flags.refresh(), timeout=5)
    except asyncio.TimeoutError as e:
        logger.warning(f"[flags] first read took over 5 s ({type(e).__name__}) — continuing on env/defaults")
    app.state.flags_task = runtime_flags.start_refresher()

    # AI cost ledger: drain the record queue into ai_calls in the background.
    from app.ai import ledger

    app.state.ledger_task = ledger.start_flusher()
    # Bounded like the flags' first read: a slow database must not hold startup.
    await ledger.seed_budget_bounded()

    if settings.internal_scheduler_enabled:
        from app.services.scheduler import start as start_scheduler

        app.state.scheduler_tasks = start_scheduler(app.state, settings)
    else:
        app.state.scheduler_tasks = []


@app.on_event("shutdown")
async def shutdown() -> None:
    flags_task = getattr(app.state, "flags_task", None)
    if flags_task is not None:
        flags_task.cancel()
    ledger_task = getattr(app.state, "ledger_task", None)
    if ledger_task is not None:
        # The final flush runs inside the task's CancelledError handler: wait
        # for it, or the loop closes first and the last ~2 s of ai_calls rows
        # are lost on every deploy. A batch already mid-insert when the
        # cancel lands is not retried (its thread may still write it).
        import asyncio

        ledger_task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(ledger_task, return_exceptions=True), timeout=5)
        except asyncio.TimeoutError as e:
            logger.warning(
                f"[ledger] final flush did not finish within 5 s; queued ai_calls rows may be lost: {type(e).__name__}"
            )
    for task in getattr(app.state, "scheduler_tasks", []):
        task.cancel()
