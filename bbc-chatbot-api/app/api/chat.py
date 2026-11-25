"""POST /api/chat — Main chat endpoint.
Thin controller: validate → sanitize → orchestrate → respond.
"""

import logging
import uuid as _uuid_mod

import asyncio
import weakref
import json
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config.settings import settings
from app.models.chat import ChatRequest, ChatResponse, VisitorInfo
from app.models.trip_context import TripContext
from app.security.input_sanitizer import sanitize_message, is_suspicious
from app.security.rate_limiter import check_rate_limit
from app.pipeline.orchestrator import process_message, _fire_and_forget
from app.db import supabase as db
from app.realtime.manager import manager
from app.services import conversation_service, lead_service
from app.services.conversation_lock import ConversationLock, conversation_lock, local_locks
from app.services.routing import route_conversation
from app.services.handoff import perform_handoff_to_agent

from pydantic import BaseModel, Field, field_validator

from app.deps.ownership import require_visitor_ownership
from app.services.blocklist import is_blocked, REFUSAL_MESSAGE as BLOCK_REFUSAL
from app.security.client_ip import client_ip
from app.security.metadata import allowed_widget_metadata
from app.services.client_presence import signal_patch


class _SkipGreetingGen(Exception):
    """Internal: the template-greeting arm already produced the opening."""

logger = logging.getLogger(__name__)


class TypingBody(BaseModel):
    text: str = ""


router = APIRouter()


# --- Init endpoint: pre-create conversation for SSE pre-connect ---

class ChatInitRequest(BaseModel):
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None
    visitor_id: Optional[str] = None
    # PR-12: the invisible Turnstile pass and the pre-chat form's trap
    # (hidden `website` field + time to fill). Neither is metadata: the
    # metadata allowlist would drop them, and they must not be stored raw.
    turnstile_token: Optional[str] = Field(default=None, max_length=4096)
    form_check: Optional[dict] = None
    # The proactive chat (no form): the visitor answered the card the widget
    # opened by itself. `trip` is what the page said they searched for; the
    # server composes the card's words from it again (app/services/proactive).
    trip: Optional[TripContext] = None
    proactive: Optional["ProactiveInit"] = None
    # "On the site now" (contracts/live.md): the visitor tapped an agent's hello; the tap token inside
    # it makes this chat the agent's (app.services.live_visitors). One that doesn't hold is ignored.
    live_tap: Optional[str] = Field(default=None, max_length=2048)

    # Neither may cost the visitor the chat: a trip that is not an object, or
    # a trigger this server does not know yet (a newer widget), is dropped and
    # the conversation opens as a plain one — never a 422.
    @field_validator("trip", mode="before")
    @classmethod
    def _trip_or_none(cls, v):
        return v if isinstance(v, (dict, TripContext)) else None

    @field_validator("proactive", mode="before")
    @classmethod
    def _proactive_or_none(cls, v):
        if isinstance(v, ProactiveInit):
            return v
        if isinstance(v, dict) and v.get("trigger") in _PROACTIVE_TRIGGERS:
            return v
        return None


_PROACTIVE_TRIGGERS = ("kayak", "ads", "quote", "reader", "exit", "note")


class ProactiveInit(BaseModel):
    # "note": the visitor tapped the note beside the button (app/services/proactive).
    trigger: Literal["kayak", "ads", "quote", "reader", "exit", "note"]
    # The words /offer used: how the note asked its first question (app/services/
    # note_bandit), the quote card that offered the chat instead of the form
    # (proactive.CONTACT), the row beside the refused form (ROW) or the Kayak
    # card's tested sentence (BEAT). The opening is written with the same words and
    # the chat is counted for it. A value that is not one of ours is dropped, never a 422.
    variant: Optional[str] = None
    # The language the card spoke ("es", "fr"; proactive_i18n): the opening is written in it, and the
    # chat keeps it. Anything else is English.
    lang: Optional[str] = None
    # The owner's contact test (app.services.contact_field_test): the side /offer named, and on the field
    # side the field the yes opened at once. Written on the chat only when it is this visitor's side;
    # anything else is dropped, never a 422.
    contact_test: Optional[str] = None
    field: Optional[str] = None

    @field_validator("variant", mode="before")
    @classmethod
    def _known_variant_or_none(cls, v):
        from app.services.proactive import known_variant
        return known_variant(v)

    @field_validator("contact_test", mode="before")
    @classmethod
    def _known_side_or_none(cls, v):
        from app.services.contact_field_test import SIDES
        return v if v in SIDES else None

    @field_validator("field", mode="before")
    @classmethod
    def _known_field_or_none(cls, v):
        from app.services.contact_field_test import CHANNELS
        return v if v in CHANNELS else None

    @field_validator("lang", mode="before")
    @classmethod
    def _card_lang_or_none(cls, v):
        from app.services.proactive_i18n import card_lang
        return card_lang(v)


ChatInitRequest.model_rebuild()


class ChatInitResponse(BaseModel):
    conversation_id: str
    visitor_token: Optional[str] = None
    # The bot's first message as stored (after the output guard), when the
    # proactive card opened this conversation; the widget shows these words,
    # and reads messages after `opening_at` so the opening is not shown twice.
    opening: Optional[str] = None
    opening_at: Optional[str] = None
    # The agent whose hello the visitor tapped, when the chat started as theirs: their first name
    # and their hello's words, which are the chat's first message, and when it was stored: the widget
    # reads messages after `live_at`, so the hello it already shows is not drawn twice.
    live_agent: Optional[str] = None
    live_hello: Optional[str] = None
    live_at: Optional[str] = None


async def _widget_gate(request: Request, turnstile_token: Optional[str], form_check: Optional[dict], meta: dict) -> None:
    """The conversation-creating doors (/chat/init, /chat/start): Turnstile
    (off at merge), then the form trap — which marks, never refuses."""
    from app.security.form_trap import apply as _apply_form_trap
    from app.security.turnstile import verify_or_raise

    await verify_or_raise(turnstile_token, client_ip(request))
    _apply_form_trap(form_check, meta)


class VisitorTokenRequest(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    turnstile_token: Optional[str] = Field(default=None, max_length=4096)


@router.post("/chat/token")
async def chat_token(
    payload: VisitorTokenRequest,
    request: Request,
    x_visitor_token: Optional[str] = Header(None),
    _rate: None = Depends(check_rate_limit),
):
    """A fresh visitor token (PR-12) for a widget whose token is near expiry
    or that predates tokens. An expired token of the same visitor is traded in
    for up to a week; otherwise the visitor passes Turnstile again (when on)."""
    from app.security import visitor_token as vt
    from app.security.turnstile import verify_or_raise

    if not vt.enabled():
        return {"success": True, "visitor_token": None}
    old_vid, reason = vt.parse(x_visitor_token or "", grace=vt.REFRESH_GRACE)
    if not (reason == "ok" and old_vid == payload.visitor_id):
        await verify_or_raise(payload.turnstile_token, client_ip(request))
    return {"success": True, "visitor_token": vt.issue(payload.visitor_id)}


@router.post("/chat/init", response_model=ChatInitResponse)
async def chat_init(
    payload: ChatInitRequest,
    request: Request,
    _rate: None = Depends(check_rate_limit),
):
    """Create conversation instantly for SSE pre-connect. No pipeline."""
    _meta = allowed_widget_metadata(payload.metadata)
    _client_ip = client_ip(request)
    if _client_ip:
        _meta.setdefault("client_ip", _client_ip)
    _ua = request.headers.get("user-agent")
    if _ua:
        _meta.setdefault("user_agent", _ua)
    _referer = request.headers.get("referer") or request.headers.get("referrer")
    if _referer:
        _meta.setdefault("referrer", _referer)
    _cf_country = (request.headers.get("cf-ipcountry") or "").strip().upper()
    if _cf_country and _cf_country not in ("XX", "T1"):
        _meta.setdefault("ip_country", _cf_country)
        _meta.setdefault("origin_country_hint", _cf_country)
    if payload.visitor_id:
        _meta.setdefault("visitor_id", payload.visitor_id)
    # The owner's test: the chat's side, server-made (app.services.agent_first_test).
    from app.services import agent_first_test as _agent_first_test
    _agent_first_test.tag(_meta, payload.visitor_id)

    from app.services import proactive as _proactive
    # The moments follow the experiment's switches; the tapped note has its
    # own (on at merge): the visitor asked for the chat.
    _proactive_trigger = (
        payload.proactive.trigger
        if payload.proactive and payload.tunnel == "sales"
        and (
            _proactive.note_enabled() if payload.proactive.trigger == _proactive.NOTE
            else _proactive.enabled()
        )
        else None
    )

    await _widget_gate(request, payload.turnstile_token, payload.form_check, _meta)

    # Blocklist — identity chokepoint. Only phone/email refuse; a blocked IP
    # alone never does (shared IPs would catch innocent visitors).
    if await is_blocked(
        phone=getattr(payload.visitor, "phone", None),
        email=getattr(payload.visitor, "email", None),
    ):
        logger.warning("[init] Blocked visitor refused")
        raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)

    conv = await conversation_service.get_or_create_conversation(
        conversation_id=None,
        tunnel=payload.tunnel,
        visitor=payload.visitor,
        visitor_id=payload.visitor_id,
        metadata=_meta or None,
    )
    if not conv or "id" not in conv:
        raise HTTPException(status_code=500, detail="Failed to create conversation")

    conv_id = conv["id"]
    logger.info(f"[init] Conv {conv_id} created/found for SSE pre-connect")
    _opening: dict = {}
    if _proactive_trigger:
        # The card's words become the bot's first message; the lead starts
        # with the page's search. A failure here must not cost the visitor
        # the chat: it is counted and logged, and the chat goes on.
        try:
            # The owner's contact test: the side /offer named this card, server-checked, written with the
            # opening's label (app.services.contact_field_test).
            from app.services import contact_field_test as _contact_field_test
            _contact: dict = {}
            if payload.proactive:
                _contact_field_test.tag(
                    _contact, payload.visitor_id, payload.proactive.contact_test, payload.proactive.field,
                )
            # `site` as the metadata allowlist validated it (bbc | bct): the
            # same brand the card named at /offer.
            _opening = await _proactive.open_conversation(
                conv_id, _proactive_trigger, payload.trip, _meta.get("site"),
                payload.proactive.variant if payload.proactive else None,
                payload.proactive.lang if payload.proactive else None,
                contact=_contact or None,
            ) or {}
        except Exception as e:
            _proactive.PROACTIVE_HEALTH["open_failures"] += 1
            logger.error(f"[init] proactive opening failed (conv={conv_id}): {type(e).__name__}")
    _live: dict = {}
    if payload.live_tap and payload.tunnel == "sales" and not _opening:
        from app.services.live_visitors import start_with_the_agent
        _live = await start_with_the_agent(conv_id, payload.visitor_id, payload.live_tap)
    from app.security.visitor_token import issue as _issue_token
    return ChatInitResponse(
        conversation_id=conv_id,
        visitor_token=_issue_token(payload.visitor_id) if payload.visitor_id else None,
        opening=_opening.get("content"),
        opening_at=_opening.get("created_at"),
        live_agent=_live.get("agent"),
        live_hello=_live.get("text"),
        live_at=_live.get("at"),
    )


class LivePassRequest(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    # The pass this visitor was handed before (the widget keeps it): with it, a new one.
    live_pass: Optional[str] = Field(default=None, max_length=512)


@router.post("/chat/live")
async def chat_live(payload: LivePassRequest, request: Request):
    """Whether the widget beats to bbc-live, where, and the visitor's pass to it (contracts/live.md). No chat
    rate limit: a page read costs the visitor nothing of their chat; new visitor ids are limited per address
    (live_visitors.widget_pass). Never cached: the pass is this visitor's."""
    from fastapi.responses import JSONResponse
    from app.services import agent_first_test
    from app.services.live_visitors import widget_pass

    from app.services import visit_registry

    # The owner's test (app.services.agent_first_test): Alex's side is never listed, so no agent greets it. The
    # visit registry still takes its visits (live=False: a pass for the registry only, "enabled" stays false).
    side = agent_first_test.side(payload.visitor_id)
    if side == agent_first_test.ALEX and not visit_registry.registry_enabled():
        return JSONResponse({"enabled": False, "test": side}, headers={"Cache-Control": "no-store"})
    # The address as the rate limiter reads it (X-Forwarded-For, then the peer): a cf-connecting-ip header is the
    # client's to write unless the edge is Cloudflare, and the limit on new ids would not hold.
    out = await widget_pass(payload.visitor_id, payload.live_pass, client_ip(request, prefer_cf=False),
                            live=side != agent_first_test.ALEX)
    if side == agent_first_test.ALEX:
        out = {**out, "enabled": False, "test": side}
    elif side == agent_first_test.AGENT:
        out = {**out, "test": side, "hold_s": agent_first_test.hold_seconds()}
    if out.get("visits"):
        # The country Cloudflare saw, for the registry (bbc-live sees no Cloudflare): two letters or nothing.
        _country = (request.headers.get("cf-ipcountry") or "").strip().upper()
        if len(_country) == 2 and _country.isalpha() and _country not in ("XX", "T1"):
            out = {**out, "country": _country}
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


class ProactiveOfferRequest(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=64)
    trigger: Literal["kayak", "ads", "quote", "reader", "exit", "note"]
    trip: Optional[TripContext] = None
    # The brand the visitor is on (bbc | bct), so the card names it. Anything
    # else is dropped, never a 422: the card then speaks for BBC.
    site: Optional[str] = None
    # The quote moment: the kinds of the fields the quote form refused (the
    # widget's FIELD_KINDS, never a value). Only known kinds pass, six at most,
    # each once; anything else is dropped, never a 422.
    refused: Optional[list[str]] = None
    # The browser's language ("es", "fr": the card speaks it, ADR-9b), the screen (`device`: the card's
    # own channel is SMS on a phone and email on a computer, ADR-3a), a row the widget can put beside
    # the refused quote form (`place="form"`, ADR-4a) and the C4 side of a Kayak visitor ("early", "now",
    # ADR-1b). Anything else is dropped, never a 422.
    lang: Optional[str] = None
    device: Optional[Literal["phone", "computer"]] = None
    place: Optional[Literal["form"]] = None
    c4: Optional[Literal["early", "now"]] = None
    # The widget opens the contact field with the yes's answer (the owner's contact test, 22 Nov:
    # app.services.contact_field_test): only such a widget takes part. Anything but true is false.
    opens_field: bool = False

    @field_validator("trip", mode="before")
    @classmethod
    def _trip_or_none(cls, v):
        return v if isinstance(v, (dict, TripContext)) else None

    @field_validator("opens_field", mode="before")
    @classmethod
    def _true_or_false(cls, v):
        return v is True

    @field_validator("lang", mode="before")
    @classmethod
    def _lang_or_none(cls, v):
        from app.services.proactive_i18n import card_lang
        return card_lang(v)

    @field_validator("device", mode="before")
    @classmethod
    def _device_or_none(cls, v):
        return v if v in ("phone", "computer") else None

    @field_validator("place", mode="before")
    @classmethod
    def _place_or_none(cls, v):
        return v if v == "form" else None

    @field_validator("c4", mode="before")
    @classmethod
    def _c4_or_none(cls, v):
        return v if v in ("early", "now") else None

    @field_validator("site", mode="before")
    @classmethod
    def _known_site_or_none(cls, v):
        from app.ai.prompts import SITE_CONFIGS
        return v if isinstance(v, str) and v in SITE_CONFIGS else None

    @field_validator("refused", mode="before")
    @classmethod
    def _known_kinds_or_none(cls, v):
        from app.services.proactive import REFUSED_KINDS
        if not isinstance(v, list):
            return None
        kinds: list[str] = []
        for k in v:
            if isinstance(k, str) and k in REFUSED_KINDS and k not in kinds:
                kinds.append(k)
        return kinds[:6] or None


@router.post("/chat/proactive/offer")
async def proactive_offer(
    payload: ProactiveOfferRequest,
    _rate: None = Depends(check_rate_limit),
):
    """Should the widget open the chat by itself now, and with what words.

    Writes nothing: a visitor who sees the card and never answers leaves no
    conversation, no lead and no row. The control arm is told show=False, so
    the widget counts the moment without showing anything.

    The owner's test (app.services.agent_first_test): on the agent's side, while an agent who may say hello has
    "On the site now" open, the card waits (show=False with retry_after_s; only a Redis key, no row), and after a
    hello or a "Not now" it doesn't come. Every answer names the side while the test runs ("test")."""
    from app.services import agent_first_test, note_bandit, proactive
    from app.services.closing import is_within_team_hours
    held = await agent_first_test.offer_hold(payload.visitor_id, payload.trigger)
    if held.answer is not None:
        return held.answer
    if payload.trigger == proactive.NOTE:
        # The note's bandit reads its counts in the background, at most every few
        # minutes: this answer never waits for the database.
        note_bandit.refresh_soon()
    # C4: the early side asks whether it still runs (a count, read where every process sees it).
    _c4_open = (
        await proactive.c4_running()
        if payload.trigger == "kayak" and payload.c4 == "early" else True
    )
    out = proactive.offer(
        payload.visitor_id, payload.trigger, payload.trip, payload.site, payload.refused,
        lang=payload.lang, device=payload.device, place=payload.place, c4=payload.c4,
        c4_open=_c4_open, team_online=is_within_team_hours(), opens_field=payload.opens_field,
    )
    if out.get("show") and payload.trigger == "kayak" and payload.c4:
        await proactive.c4_count(payload.c4)
    side = agent_first_test.side(payload.visitor_id)
    if side:
        out = {**out, "test": side, **({"after_hold": True} if held.after_hold else {})}
    return out


# --- Start endpoint: the AI greets FIRST — no fake client message ---

class ChatStartRequest(BaseModel):
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None
    visitor_id: Optional[str] = None
    turnstile_token: Optional[str] = Field(default=None, max_length=4096)
    form_check: Optional[dict] = None


class ChatStartResponse(BaseModel):
    conversation_id: str
    message: str
    visitor_token: Optional[str] = None


def _widget_request_meta(request: Request, metadata: Optional[dict], visitor_id: Optional[str]) -> dict:
    """Metadata enrichment shared with the first-message path: IP, UA,
    referrer, CF country, visitor id — same spread, same precedence."""
    _meta = allowed_widget_metadata(metadata)
    _client_ip = client_ip(request)
    if _client_ip:
        _meta.setdefault("client_ip", _client_ip)
    _ua = request.headers.get("user-agent")
    if _ua:
        _meta.setdefault("user_agent", _ua)
    _referer = request.headers.get("referer") or request.headers.get("referrer")
    if _referer:
        _meta.setdefault("referrer", _referer)
    _cf_country = (request.headers.get("cf-ipcountry") or "").strip().upper()
    if _cf_country and _cf_country not in ("XX", "T1"):
        _meta.setdefault("ip_country", _cf_country)
        _meta.setdefault("origin_country_hint", _cf_country)
    if visitor_id:
        _meta.setdefault("visitor_id", visitor_id)
    from app.services import agent_first_test
    agent_first_test.tag(_meta, visitor_id)
    return _meta


# Not persisted anywhere — the one-turn instruction that stands in for the
# user turn the Anthropic API requires. The client never "said" anything.
_GREETING_KICKOFF = (
    "(SYSTEM: the visitor just opened the chat and has not written anything. "
    "Open the conversation yourself: greet them personally using VISITOR "
    "CONTEXT and SITE CONTEXT, then ask the one right first question.)"
)


async def _existing_opening(conversation_id: str) -> Optional[str]:
    """First ai/agent message of a conversation, or None."""
    try:
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("messages")
            .select("content,role")
            .eq("conversation_id", conversation_id)
            .in_("role", ["ai", "agent"])
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        return res.data[0]["content"] if res.data else None
    except Exception as e:
        logger.warning(f"[start] existing-opening lookup failed: {e}")
        return None


def _start_token(visitor_id: Optional[str]) -> Optional[str]:
    from app.security.visitor_token import issue
    return issue(visitor_id) if visitor_id else None


@router.post("/chat/start", response_model=ChatStartResponse)
async def chat_start(
    payload: ChatStartRequest,
    request: Request,
    _rate: None = Depends(check_rate_limit),
):
    """Create the conversation and let the AI open it — personalized.

    Replaces the widget's auto-sent canned client message. No role="user"
    message is created, so last_user_message_at stays NULL until the client
    actually types — no_engagement is finally derivable.
    """
    _meta = _widget_request_meta(request, payload.metadata, payload.visitor_id)
    await _widget_gate(request, payload.turnstile_token, payload.form_check, _meta)

    if await is_blocked(
        phone=getattr(payload.visitor, "phone", None),
        email=getattr(payload.visitor, "email", None),
    ):
        logger.warning("[start] Blocked visitor refused")
        raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)

    conv = await conversation_service.get_or_create_conversation(
        conversation_id=None,
        tunnel=payload.tunnel,
        visitor=payload.visitor,
        visitor_id=payload.visitor_id,
        metadata=_meta or None,
    )
    if not conv or "id" not in conv:
        raise HTTPException(status_code=500, detail="Failed to create conversation")
    conv_id = conv["id"]

    # Every completed form = a visible lead row IMMEDIATELY (contact seeds
    # live on the conversation's top-level columns, written just above).
    if payload.tunnel == "sales":
        await lead_service.ensure_lead(conv_id, visitor=payload.visitor)

    # Idempotency: a second /start (double mount, tab restore race) reuses the
    # visitor's active conversation (Path B above) — never re-greet it.
    if await db.count_messages(conv_id) > 0:
        opening = await _existing_opening(conv_id)
        logger.info(f"[start] Conv {conv_id} already started — returning existing opening")
        return ChatStartResponse(
            conversation_id=conv_id, message=opening or "",
            visitor_token=_start_token(payload.visitor_id),
        )

    # The #168 live-greeting path: full prompt (SITE CONTEXT + t0 persona/
    # frames) on Sonnet for sales; generate_response falls back to the welcome
    # template internally on any failure, and support greets by template.
    from app.pipeline.generator import generate_response
    from app.pipeline.intent import Intent

    text, model_used, cost = None, "template", 0.0
    # Greeting A/B (PR-6, ⤑1): a stable bucket gets the one-line template
    # greeting (cost 0, asks the opening question immediately) instead of the
    # AI greeting. Default percent 0 = today's AI greeting for everyone.
    from app.flags import flag as _flag
    from app.experiments import in_arm as _in_arm
    _greeting_template = (payload.tunnel == "sales") and _in_arm(
        "greeting_template", conv_id, int(_flag("GREETING_TEMPLATE_PERCENT", 0))
    )
    if _greeting_template:
        _first = (getattr(payload.visitor, "name", "") or "").strip().split(" ")[0]
        text = (
            f"Welcome, {_first}! Where are you flying from and to?" if _first
            else "Welcome! Where are you flying from and to?"
        )
        model_used, cost = "template", 0.0
    try:
        if text:
            raise _SkipGreetingGen
        _entities = {
            "_raw_message": _GREETING_KICKOFF,
            "_metadata": _meta,
            "site": _meta.get("site"),
        }
        gen = await asyncio.to_thread(
            generate_response,
            Intent.GREETING,
            _entities,
            [],
            payload.visitor,
            None,
            [],
            payload.tunnel,
        )
        if gen and gen.text and gen.text.strip():
            text, model_used, cost = gen.text.strip(), gen.model_used, gen.cost
    except _SkipGreetingGen:  # noqa: silent — the template-greeting arm already produced the opening
        pass
    except Exception as e:
        logger.error(f"[start] Greeting generation failed (conv={conv_id}): {e}")

    if not text:
        from app.ai.templates import get_template
        text = get_template("welcome", payload.tunnel, payload.visitor) or (
            "Welcome! Where are you looking to fly?"
        )
        model_used = "template"

    row = await conversation_service.add_message(
        conv_id, "ai", text, model_used=model_used, cost=cost,
    )
    # The greeting reads the landing page and its tags, a way in for injected
    # text: the client gets what was stored, after the output guard ran on it.
    if isinstance(row, dict) and isinstance(row.get("content"), str):
        text = row["content"]
    else:
        from app.ai.output_guard import guard_ai_text

        text = await guard_ai_text(text, conversation_id=conv_id) or text
    logger.info(f"[start] Conv {conv_id} opened by AI ({model_used})")
    return ChatStartResponse(
        conversation_id=conv_id, message=text,
        visitor_token=_start_token(payload.visitor_id),
    )


async def _mark_client_active(conversation_id: str) -> None:
    """A client message IS presence — the panel must never show 'left' next
    to messages actively arriving. Guarded: only writes when presence isn't
    already online (conversations carry an updated_at trigger; unconditional
    writes on every message would churn list sorting for nothing)."""
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return
        metadata = dict(conv.get("metadata") or {})
        if metadata.get("widget_presence") == "online":
            return
        metadata["widget_open"] = True
        metadata["widget_presence"] = "online"
        metadata["widget_last_event"] = "message"
        metadata["widget_last_event_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_conversation_presence(conversation_id, metadata)
    except Exception as e:
        logger.warning(f"[presence] message-implies-online failed conv={conversation_id}: {e}")


# /chat requests that named a conversation of another visitor (shown in /health).
CHAT_OWNER_HEALTH: dict = {"refused": 0}


# After the closing (shown in /health): what clients wrote and was kept, the widget's resends answered without
# a second row, and the sends that could not be stored (the client was told to try again: 503).
POST_SALE_HEALTH: dict = {"kept": 0, "resends": 0, "unsaved": 0}

# The same send arriving again this soon is the widget's retry (a turn that outlived its wait), not new words.
_RESEND_WINDOW_S = 120
# One post-sale send at a time per conversation: the read that spots a resend and the writes after it run as
# one, in this process and across processes (app.services.conversation_lock, as for lead_service._lead_lock).
_POST_SALE_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = local_locks("post_sale")


def _post_sale_lock(conversation_id: str) -> ConversationLock:
    return conversation_lock("post_sale", conversation_id)


def _resent(row: dict, text: str) -> bool:
    """`row` is the client's `text`, sent under _RESEND_WINDOW_S ago: this send is the widget's retry of it."""
    if (row.get("content") or "").strip() != text:
        return False
    try:
        sent = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - sent).total_seconds() < _RESEND_WINDOW_S
    except (TypeError, ValueError):  # noqa: silent — an unreadable time is no resend: the message is kept and answered like any new one
        return False


def _post_sale_response(conversation_id: str, text: str, row: Optional[dict]) -> ChatResponse:
    """The answer, naming its stored row when there is one: the widget then never draws it twice."""
    return ChatResponse(
        conversation_id=conversation_id, message=text, type="post_sale", model_used="template",
        message_id=(row or {}).get("id"), created_at=(row or {}).get("created_at"),
    )


async def _post_sale_reply(conversation_id: str, message: str) -> ChatResponse:
    """A client who writes once the conversation is done (post-CRM, or the closing sent).

    What they wrote is kept, for the consultant to read: it used to be dropped, and only "Our consultant will
    reach out shortly" was stored (#3323, #3361; 18-19 Nov). A save that fails is a 503, never an ack: the
    widget takes a 200 for the save receipt. The same words again within two minutes are the widget's retry:
    nothing new is stored, and the answer that send got is said again — or the ack, while that send's turn is
    still being written. The ack names the time the team can keep (app.services.closing.when_we_reach_out)."""
    from app.services.closing import when_we_reach_out

    text = (message or "").strip()
    ack = f"Thank you! Your consultant will reach out {when_we_reach_out()}."
    async with _post_sale_lock(conversation_id):
        recent = await db.get_recent_messages(conversation_id, limit=4)
        users = [i for i, m in enumerate(recent) if m.get("role") == "user"]
        if text and users and _resent(recent[users[-1]], text):
            answer = next(
                (m for m in recent[users[-1] + 1:] if m.get("role") == "ai" and (m.get("content") or "").strip()),
                None,
            )
            POST_SALE_HEALTH["resends"] += 1
            return _post_sale_response(conversation_id, answer["content"] if answer else ack, answer)
        if text:
            if not await conversation_service.add_message(conversation_id, "user", text):
                POST_SALE_HEALTH["unsaved"] += 1
                raise HTTPException(status_code=503, detail="Message could not be saved")
            POST_SALE_HEALTH["kept"] += 1
        row = await conversation_service.add_message(conversation_id, "ai", ack, model_used="template", cost=0.0)
    return _post_sale_response(conversation_id, ack, row)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    _rate: None = Depends(check_rate_limit),
    x_visitor_token: Optional[str] = Header(None),
) -> ChatResponse:
    """Handle a single chat message from the widget."""
    # PR-12: a message into an existing conversation carries the visitor's
    # signed token (counted only until VISITOR_TOKEN_ENFORCE). A first
    # message that creates the conversation has none yet.
    if req.conversation_id and req.visitor_id:
        from app.security.visitor_token import check as _check_token
        _check_token(x_visitor_token, req.visitor_id)

    # 0. Validate conversation_id format if provided — widgets from a prior
    # backend bug may persist literal 'unknown' or other non-UUID values.
    if req.conversation_id is not None:
        try:
            _uuid_mod.UUID(req.conversation_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                f"[chat] Rejecting non-UUID conversation_id from widget: "
                f"{req.conversation_id!r}"
            )
            raise HTTPException(
                status_code=422,
                detail="Invalid conversation_id format",
            )

    # 1. Sanitize
    clean_message = sanitize_message(req.message)
    if is_suspicious(req.message):
        logger.warning(f"Suspicious message detected (conv={req.conversation_id})")

    # 1.5. Blocklist — identity chokepoint on the contact the widget sends.
    # Phone/email only; a blocked IP alone never refuses (shared IPs).
    if await is_blocked(
        phone=getattr(req.visitor, "phone", None),
        email=getattr(req.visitor, "email", None),
    ):
        logger.warning(f"[chat] Blocked visitor refused (conv={req.conversation_id})")
        raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)

    # Rule 3: read by the reopen check and by the PR-15 doors below.
    conv_info: Optional[dict] = None

    # 2. Check message count limit
    if req.conversation_id:
        count = await db.count_messages(req.conversation_id)
        if count >= settings.max_messages_per_conversation:
            raise HTTPException(
                status_code=429,
                detail="Conversation message limit reached. Please start a new conversation.",
            )

    # 2.5. Reopen closed conversation if client writes again within session window
    if req.conversation_id:
        conv_info = await db.get_conversation_simple(req.conversation_id)
        # The conversation's owner only (require_visitor_ownership's rule): a chat that has a visitor id is
        # written to by that visitor alone, before anything here can change it — a reopen, a button's
        # contact, the model's turn. A legacy chat without one stays open as before (CodeRabbit #270).
        _owner = (conv_info or {}).get("visitor_id")
        if _owner and (req.visitor_id or "").strip() != _owner:
            CHAT_OWNER_HEALTH["refused"] += 1
            logger.warning(
                f"[chat] Ownership denied for {req.conversation_id[:8]}: expected={_owner[:8]} "
                f"got={(req.visitor_id or 'NONE')[:8]}"
            )
            raise HTTPException(status_code=403, detail="Access denied")
        # Blocklist — contact already stored on the conversation (the visitor
        # submitted it earlier and may now send messages with an empty visitor
        # payload). Reuses the row already fetched above: no extra roundtrip.
        if conv_info and await is_blocked(
            phone=conv_info.get("visitor_phone"),
            email=conv_info.get("visitor_email"),
        ):
            logger.warning(
                f"[chat] Blocked visitor refused (stored contact, conv={req.conversation_id})"
            )
            raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)
        if conv_info and conv_info.get('status') in ('closed', 'completed'):
            # 'completed' = post-CRM, never reopen; a closing already sent = post-sale too:
            # a polite ack instead of a reopen.
            _meta = dict(conv_info.get("metadata") or {})
            if conv_info.get('status') == 'completed' or _meta.get("closing_sent_at"):
                return await _post_sale_reply(req.conversation_id, clean_message)
            logger.info(f"[reopen] Conv {req.conversation_id} closed — client wrote again, reopening")
            reopen_mode = "human" if conv_info.get("assigned_agent_id") else "ai"
            await db.update_conversation(req.conversation_id, {
                'status': 'active',
                'mode': reopen_mode,
            })
            # Fall through: if assigned agent exists, message is queued for human mode.

    # A client message IS presence (fire-and-forget; guarded write inside).
    if req.conversation_id:
        _fire_and_forget(_mark_client_active(req.conversation_id))

    # PR-15: the chat lock (someone using the bot for something else) and the
    # frustration lane. Both shadow until the owner turns them on; on, they
    # answer without the model, after saving what the client wrote.
    if req.conversation_id:
        from app.security import behavior as _bh
        _site = ((conv_info or {}).get("metadata") or {}).get("site")
        _door = await _bh.chat_door(
            conversation_id=req.conversation_id, raw_message=req.message,
            clean_message=clean_message, ip=client_ip(request) or "",
            visitor_id=req.visitor_id, site=_site,
            metadata=(conv_info or {}).get("metadata"),
            human_handled=_bh._human_handled(conv_info),
        )
        if _door is None:
            _door = await _bh.frustration_door(
                conversation_id=req.conversation_id, message=clean_message, site=_site,
                conv=conv_info,
            )
        if _door is not None:
            return ChatResponse(
                conversation_id=req.conversation_id, message=_door,
                type="template", model_used="template", cost=0.0,
            )

    # ── POST-CRM: template response + re-close (no handoff) ──
    # Intercept ONLY when collection is COMPLETE. created_in_crm means
    # "submitted early so the consultant can call" — NOT "done talking".
    # If route/dates/pax are still missing, fall through to the pipeline
    # so the bot keeps collecting (real data lands in Supabase + admin
    # panel; the CRM record was created with defaults by design).
    if req.conversation_id:
        _lead = await lead_service.get_or_create_lead(req.conversation_id)
        if _lead and _lead.get("created_in_crm"):
            from app.models.lead import get_missing_fields as _gmf_postcrm
            _conv_row = await db.get_conversation_simple(req.conversation_id) or {}
            _contact_ctx = {
                "visitor_name": _conv_row.get("visitor_name")
                    or getattr(req.visitor, "name", None),
                "visitor_email": _conv_row.get("visitor_email")
                    or getattr(req.visitor, "email", None),
                "visitor_phone": _conv_row.get("visitor_phone")
                    or getattr(req.visitor, "phone", None),
            }
            _still_collecting = bool(_gmf_postcrm(_lead, _contact_ctx))
            # Don't close with template if client hasn't confirmed summary.
            # Abandoned cron may submit CRM without confirmation; client
            # returning with "yes" should go through pipeline, not template.
            if not _still_collecting and not (_conv_row.get("metadata") or {}).get("confirmed_at"):
                _still_collecting = True
            _post_crm_mode = await db.get_conversation_mode(req.conversation_id)
            if _post_crm_mode == "ai" and not _still_collecting:
                from app.services.closing import compute_closing_text, has_closing_been_sent, claim_closing_sent
                _meta = dict(_conv_row.get("metadata") or {})

                # Save user message
                await conversation_service.add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,)

                if has_closing_been_sent(_meta):
                    # Post-sale: closing already sent — discriminate message type
                    _msg_lower = clean_message.strip().lower().rstrip(".!,")
                    _polite = _msg_lower in (
                        "thank you", "thanks", "ok", "great", "perfect",
                        "yes", "bye", "goodbye", "awesome", "sounds good",
                        "appreciate it", "thx", "ty",
                    )
                    if _polite:
                        from app.services.closing import when_we_reach_out

                        _ack = f"You're welcome! Our team will be in touch {when_we_reach_out()}."
                    else:
                        _ack = (
                            "Noted — we'll include that in your request. "
                            "For any changes, please call "
                            + compute_closing_text(_meta.get("site")).split("call ")[-1].rstrip(".")
                            + "."
                        )
                    await conversation_service.add_message(
                        req.conversation_id, "ai", _ack,
                        model_used="template", cost=0.0,
                    )
                    await db.update_conversation(req.conversation_id, {
                        "status": "completed",
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "mode": "ai",
                        "assigned_agent_id": None,
                    })
                    return ChatResponse(
                        conversation_id=req.conversation_id,
                        message=_ack, type="post_sale", model_used="template",
                    )

                # First post-CRM closing (PR-A claim handles dedup)
                _claimed = await claim_closing_sent(req.conversation_id)
                _closing = compute_closing_text(_meta.get("site"))
                from app.services.closing import when_we_reach_out

                _msg = _closing if _claimed else f"You're all set! Our consultant will reach out {when_we_reach_out()}."
                await conversation_service.add_message(
                    req.conversation_id, "ai", _msg,
                    model_used="template", cost=0.0,
                )
                await db.update_conversation(req.conversation_id, {
                    "status": "completed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[{req.conversation_id}] Post-CRM: template + re-close (no handoff)")
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_msg, type="closing", model_used="template",
                )

    # 3. If existing conversation in 'human' mode
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
            # H1: Check if assigned agent is effectively offline before queuing
            from app.services.handoff import is_agent_effectively_offline, fall_back_to_ai
            # FIX-A: Silent reservation (announce_pending) — the operator was auto-assigned
            # but has NOT engaged yet. The orchestrator is designed to let AI keep serving
            # until the operator sends their first real message. Do NOT queue here (that
            # returned an ephemeral "One moment…" and skipped the pipeline, killing the
            # visitor's message). Fall through to the AI pipeline; if the operator engages
            # mid-flight, the orchestrator's mode=='human' guard discards the AI response.
            _conv_res = await db.get_conversation_simple(req.conversation_id)
            _is_silent_reservation = bool(
                ((_conv_res or {}).get("metadata") or {}).get("announce_pending")
            )
            if _is_silent_reservation:
                pass  # fall through to the AI pipeline below (Step 3.5+)
            elif await is_agent_effectively_offline(req.conversation_id):
                logger.info(
                    f"[H1] Conv {req.conversation_id}: agent offline in human mode "
                    f"→ falling back to AI"
                )
                from app.services.conversation_service import add_message
                await add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,
                )
                await fall_back_to_ai(req.conversation_id)
                from app.services.handoff import _FALLBACK_MSG
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_FALLBACK_MSG,
                    type="fallback",
                    model_used="none",
                )
            else:
                # Agent is still online — queue the message for human handling
                from app.services.conversation_service import add_message
                await add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,
                )
                # PR-7: the client often gives the operator the route/date/pax.
                # Extract it and push the operator card; never speak to the client.
                # In the background: the message is saved, and the client's
                # answer never waits on the lead writes and the push.
                from app.pipeline.operator_card import extract_and_push
                _fire_and_forget(extract_and_push(req.conversation_id, clean_message))
                # An email, a phone or a name the client gives the operator lands on the
                # conversation as it does in a chat with Alex (operator_contact), flag or not.
                from app.pipeline.operator_contact import record_contact
                _fire_and_forget(record_contact(req.conversation_id, clean_message))
                from app.services.handoff import _handoff_phrase_recently_sent

                already = await _handoff_phrase_recently_sent(req.conversation_id)
                queued_msg = (
                    "Your message has been sent to the specialist."
                    if already
                    else "One moment please, connecting you with a specialist..."
                )
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=queued_msg,
                    type="queued",
                    model_used="none",
                )

    # ── First-message routing: operator-first, AI fallback ──
    # Visitor writes → is an operator free right now?
    #   YES → handoff immediately (live-chat from message 1)
    #   NO  → fall-through to AI pipeline (collect → CRM → closed)
    # Restored from #70 (disabled in #61 due to phantom agents);
    # now safe: 8-min deadline (#101), notifications (#102),
    # persist-fallback (#103), internal scheduler (#104).
    # Gate: first user message on a fresh AI conversation.
    if req.conversation_id:
        _conv_check = await db.get_conversation(req.conversation_id)
        if (
            _conv_check
            and _conv_check.get("mode") == "ai"
            and not _conv_check.get("assigned_agent_id")
        ):
            # Count CLIENT messages only — the AI greeting (#170) is message
            # #1 and must not close this gate. The un-scoped count killed
            # first-contact routing AND the Tyke no-agents email for six
            # days (assignments flatlined 2026-08-06 23:41 UTC).
            _msg_count = await db.count_messages(req.conversation_id, role="user")
            if _msg_count == 0:  # first user message — not yet saved
                _route = await route_conversation(
                    req.tunnel,
                    visitor=req.visitor,
                    visitor_id=req.visitor_id,
                )
                if _route and _route.get("agent_id"):
                    await perform_handoff_to_agent(
                        conversation_id=req.conversation_id,
                        agent_id=_route["agent_id"],
                        agent_name=_route.get("agent_name", "A specialist"),
                        tunnel=req.tunnel,
                        emit_messages=False,
                        handoff_reason="first_message",
                    )
                    logger.info(
                        f"[{req.conversation_id}] First-message silent handoff → "
                        f"{_route.get('agent_name')} (30s to respond, AI pipeline continues)"
                    )
                    # Fall through — pipeline saves user message and generates AI greeting.
                else:
                    # Nobody was picked for this conversation — by design now
                    # (spec v2.4 §2ter/D1). It goes into the shared line, where
                    # everyone eligible sees it and the first click wins. The AI
                    # pipeline continues below either way, so the visitor is
                    # never left waiting for a human who may not come (D3).
                    # route_conversation's signature is untouched: the id lives
                    # here, at the call site, not inside routing.
                    await db.enqueue_conversation(req.conversation_id)
                    logger.info(
                        f"[{req.conversation_id}] First-message routing → "
                        f"no operator, shared queue + AI pipeline"
                    )
                    # Management rule (Tyke, 02-Jul): email super@ whenever a
                    # client starts chatting and NO agents are logged in —
                    # not only on explicit handoff. Distinct from "all busy":
                    # busy agents are logged in and see the chat, so no email.
                    # Fire-and-forget + 24h throttle (claim_super_alert).
                    _cid_alert = req.conversation_id
                    _tunnel_alert = req.tunnel
                    _visitor_alert = req.visitor
                    _first_msg_alert = clean_message

                    async def _send_first_message_super_alert():
                        from app.services.closing import claim_super_alert
                        from app.services.email import send_super_alert_email

                        online = await db.get_available_agents(
                            tunnel=_tunnel_alert,
                            timeout_seconds=settings.agent_timeout_seconds,
                        )
                        if online:
                            return  # agents logged in (just busy) — no alert
                        claimed = await claim_super_alert(
                            _cid_alert, settings.super_alert_cooldown_minutes
                        )
                        if claimed:
                            await send_super_alert_email(
                                conversation_id=_cid_alert,
                                visitor_name=getattr(_visitor_alert, "name", None),
                                visitor_phone=getattr(_visitor_alert, "phone", None),
                                visitor_email=getattr(_visitor_alert, "email", None),
                                tunnel=_tunnel_alert,
                                last_message=_first_msg_alert,
                            )

                    _fire_and_forget(_send_first_message_super_alert())

    # 4. AI mode or new conversation → run pipeline
    # Enrich metadata with client IP + User-Agent
    _meta = allowed_widget_metadata(req.metadata)
    _client_ip = client_ip(request)
    if _client_ip:
        _meta.setdefault("client_ip", _client_ip)
    _ua = request.headers.get("user-agent")
    if _ua:
        _meta.setdefault("user_agent", _ua)
    _referer = request.headers.get("referer") or request.headers.get("referrer")
    if _referer:
        _meta.setdefault("referrer", _referer)
    _cf_country = (request.headers.get("cf-ipcountry") or "").strip().upper()
    if _cf_country and _cf_country not in ("XX", "T1"):
        _meta.setdefault("ip_country", _cf_country)
        _meta.setdefault("origin_country_hint", _cf_country)
    if req.visitor_id:
        _meta.setdefault("visitor_id", req.visitor_id)
    from app.services import agent_first_test as _agent_first_test
    _agent_first_test.tag(_meta, req.visitor_id)

    # A button after the card (yes, SMS, email, "I'll wait here"): answered at
    # once, without the model (app.pipeline.card_buttons). None: answered as typed.
    if req.button and req.conversation_id:
        from app.pipeline.card_buttons import answer as _answer_button

        _tapped = await _answer_button(req, clean_message, _meta)
        if _tapped is not None:
            return _tapped

    # Response cache check (Sprint 3)
    from app.services.response_cache import get_cached, set_cached
    from app.pipeline.intent import detect_intent

    _cache_site = (_meta or {}).get("site", "bbc")
    _cached_response = get_cached(clean_message, site=_cache_site, tunnel=req.tunnel)
    if _cached_response and req.conversation_id:
        await conversation_service.add_message(
            conversation_id=req.conversation_id,
            role="user",
            content=clean_message,
        )
        await conversation_service.add_message(
            conversation_id=req.conversation_id,
            role="ai",
            content=_cached_response,
            model_used="cache",
            cost=0.0,
        )
        logger.info(f"[{req.conversation_id}] Cache HIT — skipped pipeline")
        return ChatResponse(
            conversation_id=req.conversation_id,
            message=_cached_response,
            streaming=False,
            type="ai",
            model_used="cache",
            cost=0.0,
        )

    response = await process_message(
        conversation_id=req.conversation_id,
        message=clean_message,
        tunnel=req.tunnel,
        visitor=req.visitor,
        metadata=_meta or None,
        visitor_id=req.visitor_id,
    )

    # Conversation created inside the pipeline (no id on the request) — the
    # message that created it still implies presence.
    if not req.conversation_id and response.conversation_id:
        _fire_and_forget(_mark_client_active(response.conversation_id))

    # Cache store for FAQ/greeting (Sprint 3) — skip streaming (message empty until SSE ends)
    _result_intent = detect_intent(clean_message, _meta, use_llm=False).value
    if response.message and not response.streaming and _result_intent:
        set_cached(
            clean_message,
            response.message,
            _result_intent,
            site=_cache_site,
            tunnel=req.tunnel,
            model_used=response.model_used,
        )

    return response


# ── Typing indicator endpoints (public — widget reports typing status) ─────

@router.post("/chat/typing/{conversation_id}")
async def set_typing_status(
    conversation_id: str,
    body: TypingBody,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget reports client is actively typing.
    Stored in Redis with 10s TTL — never saved to DB.
    Called every ~500ms while client types (debounced on client side)."""
    from app.realtime.typing_indicator import typing_manager
    text = body.text.strip()
    if text:
        await typing_manager.set_typing(conversation_id, text)
    else:
        await typing_manager.clear_typing(conversation_id)
    # The panel used to poll this every 500ms per open chat. Now it subscribes;
    # the Redis write stays as the fallback/polling source of truth.
    from app.realtime.manager import manager
    await manager.push(conversation_id, {"event": "typing", "is_typing": bool(text), "text": text})
    return {"success": True}


@router.delete("/chat/typing/{conversation_id}")
async def clear_typing_status(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget reports client sent message or cleared input."""
    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_typing(conversation_id)
    # The panel stops polling typing while its stream is live. Without this
    # event the "typing…" indicator stayed on until reconnect: set_typing
    # published, clear did not.
    from app.realtime.manager import manager
    await manager.push(conversation_id, {"event": "typing", "is_typing": False, "text": ""})
    return {"success": True}


@router.post("/chat/session/{conversation_id}/open")
async def mark_chat_session_open(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Client opened widget chat UI but did not necessarily send a message yet."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"success": False, "data": None}
    # Merged, not written back whole: a beat that lands between this read and
    # this write keeps its pres_seen_at (a whole-blob write would undo it, and
    # the presence rule could then read "left" for a client who just came back).
    await db.patch_conversation_presence(conversation_id, {
        "widget_open": True,
        "widget_presence": "online",
        "widget_last_event": "open",
        "widget_last_event_at": datetime.now(timezone.utc).isoformat(),
    })
    # Self-healing dispatch: a conversation stuck in needs_agent (queued when
    # nobody was ready, or a dispatch that died mid-flight) retries the
    # moment the client is back at the widget.
    if conv.get("status") == "needs_agent":
        from app.services.routing import dispatch_needs_agent
        _fire_and_forget(dispatch_needs_agent(conversation_id))
    return {"success": True}


@router.post("/chat/session/{conversation_id}/before-you-go")
async def before_you_go(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """B1 (ADR-6d): a client with no contact is leaving (the tab went out of view, the page went
    quiet, the chat was closed) and no agent has written yet: one message asks where the agent should
    write, with the buttons. Once per conversation; {"show": false, "why"} otherwise."""
    from app.pipeline.card_buttons import before_you_go as _before_you_go

    return await _before_you_go(conversation_id)


@router.post("/chat/session/{conversation_id}/ping")
async def ping_chat_session(
    conversation_id: str,
    v: Optional[int] = None,
    kind: Optional[Literal["beat", "hidden", "leaving"]] = None,
    chat: Optional[Literal["open", "closed"]] = None,
    idle: Optional[int] = None,
    m: Optional[int] = None,
    _owner: None = Depends(require_visitor_ownership),
):
    """The widget tells the server the client is really there.

    9 of the 34 vanish-census conversations still read `online` with no
    close event ever recorded: `keepalive` beacons die with a killed tab
    or a suspended phone, and nothing ever contradicted the stored value.
    An operator then opens the chat believing someone is on the other end.

    Without `v` (a widget bundle from before presence v2): the chat is open
    and the tab is visible, as it always meant. With v=2 it is one of three
    signals (presence.signal_patch): a beat every 30 s while the page is in view,
    chat open or closed, with the seconds since the visitor last touched the
    page (`idle`) and whether it is a phone (`m`); `hidden`; `leaving`.

    A PATCH, not a read-modify-write: at 120 pings/hour a full-blob
    rewrite would eventually revert a flag another request claimed in
    between (closing_sent_at, confirmed_at, summary state) and would
    cost a whole conversation read each time. Only the signal's keys are
    merged, server-side, under 029's no-touch discipline — a heartbeat
    must never resurrect a conversation to the top of the panel's list."""
    now = datetime.now(timezone.utc)
    signal = kind or "beat"
    if v == 2:
        patch = signal_patch(signal, now, chat, idle, m)
    else:
        signal = "beat"
        patch = {
            "widget_open": True,
            "widget_presence": "online",
            "widget_last_event": "ping",
            "widget_last_event_at": now.isoformat(),
            # Proof this conversation has a PINGING widget: without it the
            # server cannot tell "silent because gone" from "silent because
            # the client runs an older bundle that never pings".
            "widget_pings": True,
            # An older tab of a visitor whose newer tab speaks v2: the same
            # conversation reads the v2 rule, so this ping must count there too.
            # It means the chat is open, the tab in view and the chat touched in
            # the last 15 min. Without pres_v it changes nothing for the rest.
            "pres_seen_at": now.isoformat(),
            "pres_chat_open": True,
            "pres_active_at": now.isoformat(),
        }
    ok = await db.patch_conversation_presence(conversation_id, patch)
    if not ok:
        return {"success": False}
    from app.realtime.manager import manager
    if signal == "leaving":
        # As /close does for the first rule: no "typing…" left behind. The
        # presence line itself does not move yet — a page change sends a
        # beat from the next page within seconds — but the panel is told when
        # to read it again: when the grace runs out, not at the deadline of an
        # earlier signal (`hidden` comes first when a tab is closed).
        from app.realtime.typing_indicator import typing_manager
        await typing_manager.clear_typing(conversation_id)
        await manager.push(conversation_id, {"event": "typing", "is_typing": False, "text": ""})
        await manager.push(conversation_id, {
            "event": "presence",
            "widget_presence_changes_in": db.PRESENCE_LEAVE_GRACE_SECONDS + 1,
            "widget_last_event_at": now.isoformat(),
        })
        return {"success": True}
    # The signal carries everything the rule needs right now, so it is derived
    # from the patch alone, no read: the same shape GET /conversations/{id}/presence
    # returns, so the panel can setQueryData(['presence', id]) with it.
    effective, age = db.derive_effective_presence(patch, now)
    changes_in = db.presence_changes_in(patch, now)
    if signal == "hidden" and m is None:
        # Phone or computer is not in this signal (the stored one decides):
        # the panel reads it again at the earlier of the two deadlines.
        as_phone = db.presence_changes_in({**patch, "pres_mobile": True}, now)
        changes_in = min(filter(None, (changes_in, as_phone)), default=None)
    event = {
        "event": "presence",
        "widget_presence_effective": effective,
        "widget_presence_age_seconds": age,
        "widget_presence_changes_in": changes_in,
        "widget_last_event_at": now.isoformat(),
    }
    if signal == "beat":
        event["widget_open"] = patch["widget_open"]
        event["widget_presence"] = patch["widget_presence"]
    await manager.push(conversation_id, event)
    return {"success": True}


@router.post("/chat/session/{conversation_id}/close")
async def mark_chat_session_close(
    conversation_id: str,
    reason: str = Query("minimized", pattern="^(minimized|left)$"),
    _owner: None = Depends(require_visitor_ownership),
):
    """Client stopped active chat session.

    reason=minimized -> chat collapsed with X while still on page
    reason=left -> browser tab/page closed or navigated away
    """
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"success": False, "data": None}
    metadata = dict(conv.get("metadata") or {})
    metadata["widget_open"] = False
    metadata["widget_presence"] = reason
    metadata["widget_last_event"] = "close"
    metadata["widget_last_close_reason"] = reason
    metadata["widget_last_event_at"] = datetime.now(timezone.utc).isoformat()
    await db.update_conversation_presence(conversation_id, metadata)
    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_typing(conversation_id)
    # Publish the SAME keys ping_chat_session publishes for a beat, derived
    # by the SAME rule GET /presence uses — the panel MERGES this over
    # the last ping, so any key left out keeps its old value. A close payload
    # without widget_open=False left the client "online" until reconnect.
    from app.realtime.manager import manager
    _effective, _age = db.derive_effective_presence(
        metadata, last_user_message_at=conv.get("last_user_message_at")
    )
    await manager.push(conversation_id, {
        "event": "presence",
        "widget_open": False,
        "widget_presence": reason,
        "widget_presence_effective": _effective,
        "widget_presence_age_seconds": _age,
        "widget_presence_changes_in": db.presence_changes_in(
            metadata, last_user_message_at=conv.get("last_user_message_at")
        ),
        "widget_last_close_reason": reason,
        "widget_last_event_at": metadata["widget_last_event_at"],
    })
    await manager.push(conversation_id, {"event": "typing", "is_typing": False, "text": ""})
    return {"success": True}


# ── Public widget endpoints (no auth required — conv_id UUID is the secret) ──

@router.get("/chat/visitor/{visitor_id}/active-conversation")
async def get_active_conversation_for_visitor(
    visitor_id: str,
    x_visitor_token: Optional[str] = Header(None),
):
    """Widget calls this on mount to find a visitor's open conversation.
    Returns conversation_id if one exists, else null — so the widget can
    reconnect across tabs/sessions without a stale localStorage ID.

    Open = active or needs_agent: a client who asked for a person keeps the
    chat on the next page. A null here makes the widget drop its cached chat,
    so a failed read answers 503 instead (the widget keeps the chat on any
    non-2xx) — "no conversation" and "couldn't look" are different answers."""
    from app.security.visitor_token import check as _check_token
    _check_token(x_visitor_token, visitor_id)
    try:
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("conversations")
            .select("id")
            .eq("visitor_id", visitor_id)
            .in_("status", list(db.OPEN_STATUSES))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        conv_id = res.data[0]["id"] if res.data else None
        return {"success": True, "conversation_id": conv_id}
    except Exception as e:
        # Type and database code only: an error's text can quote what it
        # was looking at, and nothing a visitor typed belongs in a log.
        logger.error(
            f"get_active_conversation_for_visitor error: {type(e).__name__} "
            f"(code={db._loggable_code(db._error_code(e)) or 'none'})"
        )
        raise HTTPException(status_code=503, detail="Conversation lookup unavailable") from e


@router.get("/chat/status/{conversation_id}")
async def public_get_conversation_status(conversation_id: str):
    """Session restore check for widget. Returns only status and mode.
    Fixes the 401 bug where widget incorrectly reset sessions on every open."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "data": None}
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return {"success": False, "data": None}
        return {
            "success": True,
            "data": {
                "status": conv.get("status"),
                "mode": conv.get("mode"),
            },
        }
    except Exception as e:
        logger.error(f"public_get_conversation_status error: {type(e).__name__}: {e}")
        return {"success": False, "data": None, "error": "status unavailable"}


@router.get("/chat/messages/{conversation_id}")
async def public_get_messages(
    conversation_id: str,
    after: Optional[str] = Query(None),
    _owner: None = Depends(require_visitor_ownership),
):
    """Public incremental message polling for widget.
    Used as SSE fallback and for catch-up after reconnect."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "data": [], "error": "invalid conversation_id"}
    try:
        msgs = await db.get_messages_after(conversation_id, after)
        return {"success": True, "data": await _with_agent_identity(conversation_id, msgs)}
    except Exception as e:
        logger.error(f"public_get_messages error: {type(e).__name__}: {e}")
        return {"success": False, "data": [], "error": "messages unavailable"}


async def _with_agent_identity(conversation_id: str, msgs: list) -> list:
    """Attach the operator's name + avatar to operator messages.

    The messages table stores no identity columns, so live SSE pushes carry it
    and this read path re-attaches it — that's what makes the photo and name
    survive a widget reload. Only role='agent' rows are touched; AI and system
    messages stay exactly as they are. One lookup per batch, and only when the
    batch actually contains an operator message (incremental polls usually
    return nothing).
    """
    if not any((m.get("role") == "agent") for m in msgs):
        return msgs
    identity = await db.get_conversation_agent_identity(conversation_id)
    if not identity:
        return msgs
    for m in msgs:
        if m.get("role") == "agent":
            m["agent_name"] = identity.get("name") or "Consultant"
            m["agent_avatar_url"] = identity.get("avatar_url")
    return msgs


@router.get("/chat/agent-typing/{conversation_id}")
async def public_get_agent_typing(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget polls this to show "<operator> is typing…".

    No operator login required (the conversation id plus visitor ownership is
    the guard, same as the other widget endpoints). Returns the operator's name
    only — the draft text never leaves the admin panel.
    """
    from app.realtime.typing_indicator import typing_manager
    state = await typing_manager.get_agent_typing(conversation_id)
    return {
        "success": True,
        "data": state or {"is_typing": False, "name": ""},
    }


@router.get("/chat/stream/{conversation_id}")
async def sse_stream(
    conversation_id: str,
    request: Request,
    _owner: None = Depends(require_visitor_ownership),
):
    """SSE real-time stream for widget.
    Pushes agent messages instantly (~50ms vs 1s polling).
    X-Accel-Buffering: no disables Railway/nginx proxy buffering — required for SSE.
    Widget falls back to polling if SSE fails 3 times."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "error": "invalid conversation_id"}

    # ?v=2: the widget knows the `typing` event (PR-10). Older cached widgets
    # do not send it and never receive typing events.
    _v2 = request.query_params.get("v") == "2"

    async def event_generator():
        queue = await manager.connect(conversation_id, v2=_v2)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    payload = json.dumps(msg)
                    msg_id = msg.get("id", "")
                    yield f"id: {msg_id}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping every 25s — prevents proxy from closing idle connections
                    yield ": keepalive\n\n"
        finally:
            manager.disconnect(conversation_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
