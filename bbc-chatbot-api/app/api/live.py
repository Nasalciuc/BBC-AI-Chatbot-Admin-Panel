"""POST /api/live/send: an agent's hello to a visitor on the site now (app.services.live_visitors,
contracts/live.md). The list, the claims and the delivery are bbc-live's; the words and the rules are here.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import supabase as db
from app.security.auth import get_current_user
from app.services import live_visitors

logger = logging.getLogger(__name__)
router = APIRouter()


class SendBody(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    text: str = Field(..., min_length=1, max_length=1000)


@router.post("/live/send")
async def send(body: SendBody, user: dict = Depends(get_current_user)):
    user_db = await db.get_user_by_id(user.get("id"))
    # The row decides the role and the line, not a token signed hours ago.
    who = {**user, **{k: user_db[k] for k in ("role", "tunnel_scope") if user_db and user_db.get(k)}}
    try:
        out = await live_visitors.send_hello(who, user_db, body.visitor_id, body.text)
    except live_visitors.SendRefused as e:
        # The admin envelope, with the refusal's status (rule 4); `code` lets the panel tell the reasons apart.
        logger.info(f"[live] hello refused: {e.code}")
        return JSONResponse(status_code=e.status, content={"success": False, "data": None, "count": 0,
                                                           "error": e.detail, "code": e.code})
    return {"success": True, "data": out, "count": 1}
