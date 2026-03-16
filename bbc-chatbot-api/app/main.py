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
from app.security.auth import verify_credentials
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
    redoc_url=None,
)

# ── CORS ──────────────────────────────────────────────────────
# Ensure origins is always a list (Pydantic may parse JSON string differently)
origins = settings.cors_origins
if isinstance(origins, str):
    import json
    try:
        origins = json.loads(origins)
    except (json.JSONDecodeError, TypeError):
        origins = [origins]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── Request Logger ────────────────────────────────────────────
app.add_middleware(RequestLoggerMiddleware)

# ── Routers ───────────────────────────────────────────────────
# Health: PUBLIC (no auth — Railway healthcheck needs it)
app.include_router(health_router, tags=["health"])

# API: ALL PROTECTED by HTTP Basic Auth
from fastapi import Depends
api_deps = [Depends(verify_credentials)]
app.include_router(chat_router,          prefix="/api", tags=["chat"],          dependencies=api_deps)
app.include_router(conversations_router, prefix="/api", tags=["conversations"], dependencies=api_deps)
app.include_router(leads_router,         prefix="/api", tags=["leads"],         dependencies=api_deps)
app.include_router(kb_router,            prefix="/api", tags=["kb"],            dependencies=api_deps)
app.include_router(dashboard_router,     prefix="/api", tags=["dashboard"],     dependencies=api_deps)
app.include_router(users_router,         prefix="/api", tags=["users"],         dependencies=api_deps)


# ── Startup ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    logger.info(f"{settings.app_name} starting — v1.0.0")
    logger.info(f"CORS origins: {settings.cors_origins}")
    logger.info(f"Claude Haiku: {settings.claude_haiku_model}")
    logger.info(f"Claude Sonnet: {settings.claude_sonnet_model}")
    logger.info(f"Qdrant: {'configured' if settings.qdrant_url else 'disabled'}")
    logger.info(f"Redis: {'configured' if settings.redis_url else 'disabled'}")
    logger.info(f"Budget: ${settings.daily_budget}/day, ${settings.per_conversation_budget}/conv")
