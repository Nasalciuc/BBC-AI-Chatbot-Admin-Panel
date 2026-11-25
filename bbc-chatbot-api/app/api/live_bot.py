"""Skye's own hello in "On the site now", from outside the scheduler (app.services.live_bot):

- GET  /api/live/bot/candidates: the visitors Skye may greet now and the words she would send them;
- POST /api/live/bot/hello: her hello to one of them, the ready-written words or the caller's (held to the client's
  rules), the same rules as her run every few seconds.

Public with LIVE_BOT_KEY as a bearer, not a panel login: the caller is a service (the owner's, e.g. a workflow), not
an agent. No key set: 503, the endpoints are off. The answers are the admin envelope {success, data, count, error?},
with the refusal's `code`.
"""

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import live_bot
from app.services.live_visitors import SendRefused
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _refused(status: int, code: str, error: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "data": None, "count": 0, "error": error,
                                                     "code": code})


def _key_problem(request: Request) -> Optional[JSONResponse]:
    """LIVE_BOT_KEY as a bearer, compared in constant time; None when it holds."""
    key = (settings.live_bot_key or "").strip()
    if not key:
        return _refused(503, "not_configured", "The bot's endpoints are not configured (LIVE_BOT_KEY).")
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth.encode("utf-8"), f"Bearer {key}".encode("utf-8")):
        live_bot.BOT_HEALTH["key_refused"] += 1
        logger.warning("[live-bot] a call with a wrong key refused")
        return _refused(401, "key", "Invalid bot key.")
    return None


class HelloBody(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    # Skye's words instead of the ready-written ones (1 to 300 characters once cleaned; no contact, link or price).
    text: Optional[str] = Field(None, max_length=1000)
    # The group the words were written for (live_visitors.HELLO_GROUPS), kept with them; only with `text`.
    group: Optional[str] = Field(None, max_length=16)


@router.get("/live/bot/candidates")
async def bot_candidates(request: Request):
    problem = _key_problem(request)
    if problem is not None:
        return problem
    try:
        rows = await live_bot.candidates()
    except SendRefused as e:
        logger.info(f"[live-bot] candidates not read: {e.code}")
        return _refused(e.status, e.code, e.detail)
    return {"success": True, "data": rows, "count": len(rows)}


@router.post("/live/bot/hello")
async def bot_hello(body: HelloBody, request: Request):
    problem = _key_problem(request)
    if problem is not None:
        return problem
    try:
        out = await live_bot.greet(body.visitor_id, text=body.text, group=body.group, via="endpoint")
    except SendRefused as e:
        logger.info(f"[live-bot] hello refused: {e.code}")
        return _refused(e.status, e.code, e.detail)
    return {"success": True, "data": out, "count": 1}
