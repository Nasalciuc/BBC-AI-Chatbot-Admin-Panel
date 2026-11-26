"""The endpoints an agent's own bot works through, as the agent (app.services.live_bot; contracts/live.md):

- GET  /api/live/bot/visitors: who is on the site now, as the panel lists them for the agent, with the hello ready;
- POST /api/live/bot/hello: the hello in the agent's name, without the three visitors an agent holds at a time;
- GET  /api/live/bot/chats: the chats its hellos started, still the agent's;
- GET  /api/live/bot/chats/{conversation_id}/messages?after=: what was said there;
- POST /api/live/bot/chats/{conversation_id}/messages: its answer, as the agent's.

Public with the agent's bot key as a bearer (Authorization: Bearer <key>, taken in the panel: POST /api/live/bot-key),
not a panel login: the caller is the agent's bot. The answers are the admin envelope {success, data, count, error?},
with the refusal's `code`.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import live_bot
from app.services.live_bot import BotRefused

logger = logging.getLogger(__name__)
router = APIRouter()


def _refused(e: BotRefused) -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"success": False, "data": None, "count": 0, "error": e.detail,
                                                       "code": e.code})


class HelloBody(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    # The bot's words instead of the ready hello: 1 to 300 characters once cleaned; no contact, link or price.
    text: Optional[str] = Field(None, max_length=1000)
    # The group the words were written for (live_visitors.HELLO_GROUPS), kept with them; only with `text`.
    group: Optional[str] = Field(None, max_length=16)


class SayBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.get("/live/bot/visitors")
async def bot_visitors(request: Request):
    try:
        agent = await live_bot.agent_for(request.headers.get("Authorization"))
        rows = await live_bot.visitors(agent)
    except BotRefused as e:
        logger.info(f"[live-bot] refused: {e.code}")
        return _refused(e)
    return {"success": True, "data": rows, "count": len(rows)}


@router.post("/live/bot/hello")
async def bot_hello(body: HelloBody, request: Request):
    try:
        agent = await live_bot.agent_for(request.headers.get("Authorization"))
        out = await live_bot.greet(agent, body.visitor_id, text=body.text, group=body.group)
    except BotRefused as e:
        logger.info(f"[live-bot] refused: {e.code}")
        return _refused(e)
    return {"success": True, "data": out, "count": 1}


@router.get("/live/bot/chats")
async def bot_chats(request: Request):
    try:
        agent = await live_bot.agent_for(request.headers.get("Authorization"))
        rows = await live_bot.chats(agent)
    except BotRefused as e:
        logger.info(f"[live-bot] refused: {e.code}")
        return _refused(e)
    return {"success": True, "data": rows, "count": len(rows)}


@router.get("/live/bot/chats/{conversation_id}/messages")
async def bot_messages(conversation_id: str, request: Request, after: Optional[str] = None):
    try:
        agent = await live_bot.agent_for(request.headers.get("Authorization"))
        rows = await live_bot.messages(agent, conversation_id, after)
    except BotRefused as e:
        logger.info(f"[live-bot] refused: {e.code}")
        return _refused(e)
    return {"success": True, "data": rows, "count": len(rows)}


@router.post("/live/bot/chats/{conversation_id}/messages")
async def bot_say(conversation_id: str, body: SayBody, request: Request):
    try:
        agent = await live_bot.agent_for(request.headers.get("Authorization"))
        out = await live_bot.say(agent, conversation_id, body.text)
    except BotRefused as e:
        logger.info(f"[live-bot] refused: {e.code}")
        return _refused(e)
    return {"success": True, "data": out, "count": 1}
