"""Other channels' gateways (contracts/messenger.md): PUBLIC, each call signed by its gateway.

POST /api/channels/messenger/inbound: one Facebook Messenger event from bbc-messenger, answered with the replies to
send ({"replies": [...]}). 401 a call the gateway did not sign; 422 one it could not have made; 409 the same event
still being answered; 503 an event that failed (the gateway delivers it again; with X-Messenger-Kept the API keeps
it and answers it later itself), or the channel off (with X-Messenger-Off: nothing kept, the gateway stops there).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

# What the gateway reads on a 503 (bbc-messenger gateway.HeaderKept, gateway.HeaderOff).
HEADER_KEPT = "X-Messenger-Kept"
HEADER_OFF = "X-Messenger-Off"

from app.services import messenger_channel

logger = logging.getLogger(__name__)

router = APIRouter()


# The gateway never sends more (bbc-messenger MaxBody): a bigger body is read no further, signed or not.
MAX_BODY = 1 << 20


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_BODY:
        raise HTTPException(status_code=413, detail="Too large.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise HTTPException(status_code=413, detail="Too large.")
    return bytes(body)


@router.post("/channels/messenger/inbound")
async def messenger_inbound(request: Request) -> dict:
    if not messenger_channel.enabled():
        # 503, never 404: a gateway pointed here early is told to wait, not counted as a scan (ban_guard). Off, the
        # event is kept nowhere: the gateway stops trying it.
        raise HTTPException(status_code=503, detail="Messenger is off.", headers={HEADER_OFF: "1"})
    body = await _read_capped(request)
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
        kept = isinstance(e, messenger_channel.EventFailed) and e.kept
        # The cause's kind only: its words can carry what the person wrote (rule 7).
        cause = type(e.__cause__ or e).__name__
        logger.error(f"[messenger] an event failed ({e}), the gateway delivers it again: {cause}")
        raise HTTPException(status_code=503, detail="Couldn't take the event. Try again.",
                            headers={HEADER_KEPT: "1"} if kept else None) from e
