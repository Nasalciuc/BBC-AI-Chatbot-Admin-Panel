"""Other channels' gateways (contracts/messenger.md): PUBLIC, each call signed by its gateway.

POST /api/channels/messenger/inbound: one Facebook Messenger event from bbc-messenger, answered with the replies to
send ({"replies": [...]}). 401 a call the gateway did not sign; 422 one it could not have made; 503 the channel off
or an event that failed (the gateway delivers it again); 409 the same event still being answered.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.services import messenger_channel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/channels/messenger/inbound")
async def messenger_inbound(request: Request) -> dict:
    if not messenger_channel.enabled():
        # 503, never 404: a gateway pointed here early is told to wait, not counted as a scan (ban_guard).
        raise HTTPException(status_code=503, detail="Messenger is off.")
    body = await request.body()
    if not messenger_channel.valid_signed(request.headers.get("x-gateway-time"),
                                          request.headers.get("x-gateway-signature"), body):
        messenger_channel.MESSENGER_HEALTH["bad_signature"] += 1
        logger.warning("[messenger] an inbound call the gateway did not sign, refused")
        raise HTTPException(status_code=401, detail="Not signed by the gateway.")
    try:
        event = messenger_channel.InboundEvent.model_validate_json(body)
    except ValidationError as e:
        logger.warning(f"[messenger] an inbound event not readable: {e.error_count()} errors")
        raise HTTPException(status_code=422, detail="Not an event.") from e
    try:
        return await messenger_channel.handle(event)
    except messenger_channel.StillAnswering as e:
        raise HTTPException(status_code=409, detail="Still answering this event.") from e
    except Exception as e:
        messenger_channel.MESSENGER_HEALTH["turns_failed"] += 1
        logger.error(f"[messenger] an event failed, the gateway delivers it again: {type(e).__name__}: {e}")
        raise HTTPException(status_code=503, detail="Couldn't take the event. Try again.") from e
