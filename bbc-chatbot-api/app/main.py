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
from app.api.tasks import router as tasks_router
from app.api.notifications import router as notifications_router
from app.api.auth_routes import router as auth_router
from app.api.cron import router as cron_router
from app.security.auth import get_current_user
from app.security.request_logger import RequestLoggerMiddleware

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
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

# Chat: PUBLIC (customer widget — no auth)
app.include_router(chat_router, prefix="/api", tags=["chat"])

# Cron: PUBLIC with CRON_SECRET header (not JWT)
app.include_router(cron_router, prefix="/api", tags=["cron"])

# Auth: PUBLIC login, protected invite (auth dependency inside the route)
app.include_router(auth_router)

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

    if settings.internal_scheduler_enabled:
        from app.services.scheduler import start as start_scheduler

        app.state.scheduler_tasks = start_scheduler(app.state, settings)
    else:
        app.state.scheduler_tasks = []


@app.on_event("shutdown")
async def shutdown() -> None:
    for task in getattr(app.state, "scheduler_tasks", []):
        task.cancel()
