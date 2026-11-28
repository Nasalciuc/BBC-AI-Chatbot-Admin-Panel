"""Lead service — creation and updates. Score calculated EXCLUSIVELY in Python."""
import asyncio
import logging
import re
import weakref
from datetime import date
from typing import Optional

from app.pipeline.callback_intent import notes_without_call
from app.pipeline.entity_extractor import DREAM_OUTCOMES, merge_contact_channel, merge_must_haves, turns_down_calls
from app.services.conversation_lock import ConversationLock, conversation_lock, local_locks

logger = logging.getLogger(__name__)


def _validate_future_date(iso_str: Optional[str]) -> Optional[str]:
    """Final gate for BOTH extraction paths (regex + Claude tool): a lead date
    must be today or later. Past dates are corrected forward to the next
    occurrence; stale/unsalvageable dates return None. Never raises — a dropped
    date must NOT block lead creation (route/pax/contact still form; AI re-asks).
    """
    if not iso_str:
        return None
    try:
        d = date.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None
    if d >= date.today():
        return iso_str
    # Past date — correct to the next occurrence of (month, day).
    from app.pipeline.entity_extractor import _resolve_year
    try:
        corrected = date(_resolve_year(d.month, d.day), d.month, d.day)
        return corrected.isoformat() if corrected >= date.today() else None
    except (ValueError, OverflowError):
        return None


def _calculate_score(lead: dict, conv: Optional[dict] = None) -> int:
    """
    Calculate lead score 0-100.
    Mirrors the formula in app_settings (score_weight_*).
    EXCLUSIVELY calculated in Python — NOT in a SQL trigger.
    """
    score = 0
    has_name  = bool(lead.get("visitor_name")  or (conv and conv.get("visitor_name")))
    has_email = bool(lead.get("visitor_email") or (conv and conv.get("visitor_email")))
    has_phone = bool(lead.get("visitor_phone") or (conv and conv.get("visitor_phone")))
    has_route = bool(lead.get("origin_code") and lead.get("destination_code"))
    has_dates = bool(lead.get("departure_date"))
    has_pax   = bool(lead.get("passengers"))
    if has_name:   score += 20
    if has_phone:  score += 15
    if has_email:  score += 10
    if has_route:  score += 15
    if has_dates:  score += 10
    if has_pax:    score += 10
    return min(score, 100)


# Money-path observability: lead writes must never fail silently. Surfaced
# in /health as lead_writes so ops sees breakage without log diving.
# departure_kept: a departure read from a message that was the lead's return (_departure_is_the_return).
# page_place_held: turns whose AI reading gave the page's place as the route before the client said it
# (app.pipeline.page_place).
LEAD_WRITE_HEALTH: dict = {"errors_since_boot": 0, "last_error_at": None, "departure_kept": 0, "page_place_held": 0}


def _departure_is_the_return(dep: str, entities: dict, lead: dict) -> bool:
    """A departure read from the client's message that is the return the lead already holds, after its
    departure: the message named the return again, and writing it would make the trip leave on the day it
    comes back. #3830 (27 Nov 2026): "7-22 August" was read as the 22nd alone and the lead ended 22 → 22 Aug.

    Only for a message whose reader says whether it spoke of leaving (`_departure_said`, a bool, set by
    the orchestrator and the operator card): "we'd rather leave on the 22nd" moves the departure. So do a
    new return in the same message (not the one the lead holds, read again with it: CodeRabbit #291), a
    cleared return and a one-way."""
    if entities.get("_departure_said") is not False:
        return False
    held_dep = str(lead.get("departure_date") or "")[:10]
    held_ret = str(lead.get("return_date") or "")[:10]
    new_ret = str(entities.get("return_date") or "")[:10]
    if (new_ret and new_ret != held_ret) or entities.get("_clear_return_date"):
        return False
    if "one_way" in (entities.get("trip_type"), lead.get("trip_type")):
        return False
    return bool(held_dep and held_ret and held_dep < held_ret and dep == held_ret)


def _record_lead_write_error(conversation_id: str, context: str, exc: Exception) -> None:
    from datetime import datetime, timezone

    LEAD_WRITE_HEALTH["errors_since_boot"] += 1
    LEAD_WRITE_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat()
    logger.error(
        f"[{conversation_id}] LEAD WRITE FAILED ({context}): "
        f"{type(exc).__name__}: {exc}",
        exc_info=True,
    )


async def ensure_lead(conversation_id: str, visitor=None) -> Optional[dict]:
    """SELECT the lead for this conversation; INSERT it if missing.

    Does what the name says — every completed form becomes a visible lead row
    immediately (contact lives on the TOP-LEVEL conversation columns, written
    at conversation creation; the lead row carries the travel fields).
    intent_signals starts as {} — the DB default is a legacy [] and every
    merge downstream expects a dict.
    """
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()
        res = await _run_sync(
            lambda: db_client.table("leads")
            .select("*")
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]  # type: ignore[index]
        # Leads are a SALES concept — never create rows for support chats.
        conv_res = await _run_sync(
            lambda: db_client.table("conversations")
            .select("tunnel")
            .eq("id", conversation_id)
            .limit(1)
            .execute()
        )
        if conv_res.data and conv_res.data[0].get("tunnel") == "support":
            return None
        try:
            ins = await _run_sync(
                lambda: db_client.table("leads")
                .insert({"conversation_id": conversation_id, "intent_signals": {}})
                .execute(),
                idempotent=False,
            )
            if ins.data:
                logger.info(f"[{conversation_id}] ensure_lead: created lead row")
                return ins.data[0]  # type: ignore[index]
        except Exception:
            # Conflict (unique conversation_id, concurrent create) → re-SELECT.
            res2 = await _run_sync(
                lambda: db_client.table("leads")
                .select("*")
                .eq("conversation_id", conversation_id)
                .limit(1)
                .execute()
            )
            if res2.data:
                return res2.data[0]  # type: ignore[index]
            raise
        return None
    except Exception as e:
        _record_lead_write_error(conversation_id, "ensure_lead", e)
        return None


async def get_or_create_lead(conversation_id: str) -> Optional[dict]:
    """Thin wrapper over ensure_lead — every call site gains real creation."""
    return await ensure_lead(conversation_id)


# ── Sales-methodology signals ─────────────────────────────────────
# Read the client once, keep what they volunteer, hand it to the consultant.
SIGNAL_KEYS = (
    "occasion",
    "persona",
    "persona_source",
    "persona_confidence",
    "dream_outcome",
    "date_flexible",
    "must_haves",
    "booking_for",
    "airline_preference",
    "airline_avoid",
    "nonstop_only",
    "budget_hint",
    "price_seen",
    "best_call_time",
    "contact_channel",
    "returning_client",
)

_SIGNAL_LABELS: tuple[tuple[str, str], ...] = (
    ("occasion", "Occasion={}"),
    ("persona", "Type={}"),
    ("booking_for", "Booking for={}"),
    ("must_haves", "Must-have={}"),
    ("airline_preference", "Prefers={}"),
    ("airline_avoid", "Avoid={}"),
    ("budget_hint", "Budget~{}"),
    ("price_seen", "Seen: ~{}"),
    ("best_call_time", "Call {}"),
    ("contact_channel", "Reach by: {}"),
)

_CONTACT_IN_TEXT_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?:\+?\d[\d\-.\s()]{7,}\d)"
)


def _mask_contact_details(text: str) -> str:
    """Strip contact data a client typed into a free-text answer."""
    return _CONTACT_IN_TEXT_RE.sub("[contact]", text)


def signals_from_entities(entities: dict) -> dict:
    """The methodology signals this turn carries — empty dict when none."""
    signals: dict = {}
    for key in SIGNAL_KEYS:
        value = entities.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, str):
            value = value.strip()[:200]
            if key == "must_haves":
                value = _mask_contact_details(value)
        signals[key] = value

    persona = signals.get("persona")
    if persona and "dream_outcome" not in signals:
        outcome = DREAM_OUTCOMES.get(persona)
        if outcome:
            signals["dream_outcome"] = outcome
    return signals


def build_chat_context_line(signals: dict | None) -> Optional[str]:
    """One human-readable line of what the chat learned, for notes and CRM."""
    if not signals:
        return None

    parts: list[str] = []
    for key, template in _SIGNAL_LABELS:
        value = signals.get(key)
        if value:
            parts.append(template.format(value))

    flexible = signals.get("date_flexible")
    if flexible is True:
        parts.append("Dates=flexible")
    elif flexible is False:
        parts.append("Dates=fixed")

    if signals.get("nonstop_only"):
        parts.append("Non-stop only")
    if signals.get("returning_client"):
        parts.append("Returning client — prior agent on file")

    if not parts:
        return None
    return "Chat: " + " | ".join(parts)


def _append_note(existing: Optional[str], line: str) -> Optional[str]:
    """Add the context line to notes. Returns None when nothing changes.

    Notes written by people are never touched. Only our own earlier "Chat:"
    line is replaced, since each new one is a superset of the last.
    """
    current = (existing or "").strip()
    kept = [ln for ln in current.splitlines() if not ln.strip().startswith("Chat: ")]
    updated = "\n".join([*kept, line]).strip()
    return updated if updated != current else None


_CONTACT_FIELDS = ("visitor_name", "visitor_email", "visitor_phone")

# One lead write at a time per conversation (CodeRabbit #268, round 2). A client's
# message to an operator starts the contact recorder and the operator card side by
# side, and the pipeline writes the lead too: each reads the stored contacts, writes
# its own and scores from what it read, so the last score written could miss the
# other's contact. A lock per conversation orders every writer, in this process and,
# through Redis, across processes (app.services.conversation_lock); a lock nobody
# holds or waits on goes away.
_LEAD_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = local_locks("lead")


def _lead_lock(conversation_id: str) -> ConversationLock:
    return conversation_lock("lead", conversation_id)


async def _stored_contacts(db_client, conversation_id: str) -> Optional[dict]:
    """The contacts the conversation already holds, for the score.

    The contacts live on the conversation, not the lead, and a call often
    carries one of them (an email typed to an operator) or none (a route):
    scored from the call alone, the name and phone points the client already
    earned would drop (CodeRabbit #268). None when the read fails.
    """
    from app.db.supabase import _run_sync

    try:
        res = await _run_sync(
            lambda: db_client.table("conversations")
            .select(",".join(_CONTACT_FIELDS))
            .eq("id", conversation_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        # By type only: the row holds the client's contacts.
        logger.warning(f"[{conversation_id}] stored contacts not read for the score ({type(e).__name__})")
        return None
    rows = res.data if isinstance(res.data, list) else []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    return {k: row.get(k) for k in _CONTACT_FIELDS}


async def _tell_the_panel(conversation_id: str, conv_payload: dict) -> None:
    """The client's name, e-mail or phone just landed on the chat: the agent's open chat reads it again now.
    The panel read the chat once and refreshed it only when the agent did something, so a name the client
    gave showed as "No name yet" until the agent's next message (the owner, 25 Nov 2026). The event names
    the fields, never their values; nobody listening is no work (manager.push)."""
    fields = sorted(k for k in conv_payload if k in _CONTACT_FIELDS)
    if not fields:
        return
    try:
        from app.realtime.manager import manager

        await manager.push(f"op:{conversation_id}", {"event": "contact", "fields": fields})
    except Exception as e:  # noqa: silent — the contact is saved; the panel reads it at its next refresh
        logger.warning(f"[{conversation_id}] contact saved, the panel not told ({type(e).__name__})")


def _is_local_number(raw: str) -> bool:
    """A number typed the local way: its trunk 0, no + or 00, 8+ digits."""
    digits = re.sub(r"\D", "", raw or "")
    return (not (raw or "").strip().startswith("+") and digits.startswith("0")
            and not digits.startswith("00") and len(digits) >= 8)


async def update_lead_from_entities(conversation_id: str, entities: dict) -> bool:
    """Update lead with entities extracted by AI. Optimized: 3-4 RT instead of 7.

    Writes for one conversation run one after another (_lead_lock): the read,
    the contact and lead writes and the score write never interleave.

    True when what there was to save is saved (or there was nothing); False when a write failed
    (logged and counted here): a caller that promises the client something can say so only on True."""
    lock = _lead_lock(conversation_id)
    await lock.acquire()
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()

        # RT 1: Select lead. "*" rather than a column list so trip_notes comes
        # back when migration 040 is applied and is simply absent when it is
        # not (naming a missing column would fail the whole read). The stored
        # contacts are read alongside it, for the score.
        res, stored = await asyncio.gather(
            _run_sync(
                lambda: db_client.table("leads")
                .select("*")
                .eq("conversation_id", conversation_id)
                .limit(1)
                .execute()
            ),
            _stored_contacts(db_client, conversation_id),
        )

        if not res.data:
            has_useful = any(
                entities.get(k)
                for k in [
                    "name", "email", "phone", "origin", "destination",
                    "departure_date", "return_date", "trip_type", "passengers", "cabin_class",
                ]
            )
            if not has_useful:
                return True
            # RT 2: Insert lead
            lead_res = await _run_sync(
                lambda: db_client.table("leads")
                .insert({"conversation_id": conversation_id})
                .execute(),
                idempotent=False,
            )
            if not lead_res.data:
                return False
            lead_id = lead_res.data[0]["id"]  # type: ignore[index]
            lead_data = lead_res.data[0]  # type: ignore[index]
            logger.info(f"[{conversation_id}] Lead INSERT success: {lead_id}")
        else:
            lead_id = res.data[0]["id"]  # type: ignore[index]
            lead_data = res.data[0]  # type: ignore[index]

        conv_payload: dict = {}
        contact_kept = True
        if entities.get("name"):
            conv_payload["visitor_name"] = entities["name"]
        if entities.get("email"):
            conv_payload["visitor_email"] = entities["email"]
        if entities.get("phone"):
            from app.services.crm import format_phone_international, phone_is_ambiguous
            # A number too short to dial formats to "": it must not wipe the
            # phone the conversation already holds. `_phone_country` (the
            # visit's country, app.services.phone_country) places a number
            # typed the local way (#3296: "04…" from Australia).
            _phone = format_phone_international(entities["phone"], entities.get("_phone_country"))
            if _phone:
                conv_payload["visitor_phone"] = _phone
            elif _is_local_number(entities["phone"]) or phone_is_ambiguous(entities["phone"], entities.get("_phone_country")):
                # A local number with no country to place it, or one its country reads two ways, is kept
                # for the agent to check, never dialled as a guess.
                from app.db.supabase import patch_conversation_presence

                if not await patch_conversation_presence(
                    conversation_id, {"phone_to_check": re.sub(r"[^\d ]", "", entities["phone"]).strip()}
                ):
                    # Not kept: the caller must not say it was (card_buttons). The other writes still go.
                    _record_lead_write_error(conversation_id, "phone_to_check", RuntimeError("phone_to_check not saved"))
                    contact_kept = False

        lead_payload: dict = {}
        if entities.get("origin"):
            lead_payload["origin_code"] = entities["origin"]
        if entities.get("destination"):
            lead_payload["destination_code"] = entities["destination"]
        if entities.get("passengers"):
            lead_payload["passengers"] = entities["passengers"]
        if entities.get("cabin_class"):
            lead_payload["cabin_class"] = entities["cabin_class"]
        if entities.get("departure_date"):
            _dep = _validate_future_date(entities["departure_date"])
            if _dep and _departure_is_the_return(_dep, entities, lead_data):
                LEAD_WRITE_HEALTH["departure_kept"] += 1
                logger.info(f"[{conversation_id}] departure {_dep} not written: it is the lead's return, "
                            f"and the message did not speak of leaving (the departure stays {lead_data.get('departure_date')})")
                _dep = None
            if _dep:
                lead_payload["departure_date"] = _dep
        if entities.get("return_date"):
            _ret = _validate_future_date(entities["return_date"])
            if _ret:
                lead_payload["return_date"] = _ret
        elif entities.get("_clear_return_date"):
            # KAZUO: "actually one way" must UNSET the return date. Without
            # this the summary kept showing a return he had just cancelled —
            # writes could only ever add, never take away.
            lead_payload["return_date"] = None
        if entities.get("trip_type"):
            lead_payload["trip_type"] = entities["trip_type"]
            # The slot policy counts trip type as answered only when the
            # client said it; record that, when the column exists.
            if "trip_notes" in lead_data:
                _tn = lead_data.get("trip_notes")
                _tn = dict(_tn) if isinstance(_tn, dict) else {}
                if _tn.get("trip_type_source") != "client":
                    _tn["trip_type_source"] = "client"
                    lead_payload["trip_notes"] = _tn
        # `is not None`: an explicit 0 (a correction) clears a stale count.
        if entities.get("_children_count") is not None:
            lead_payload["children_count"] = entities["_children_count"]
        if entities.get("_infant_count") is not None:
            lead_payload["infant_count"] = entities["_infant_count"]
        if entities.get("origin") and entities.get("destination"):
            lead_payload["route_display"] = f"{entities['origin']} → {entities['destination']}"

        # Methodology signals ride the same UPDATE: merged into intent_signals
        # so other keys survive, and written only when something changed.
        signals = signals_from_entities(entities)
        if signals:
            raw_existing = lead_data.get("intent_signals")
            # 93% of lead rows carry legacy ARRAY-shaped intent_signals — a
            # list must never raise, and non-empty legacy data is preserved.
            existing_signals = (
                raw_existing if isinstance(raw_existing, dict)
                else ({} if not raw_existing else {"legacy": raw_existing})
            )
            merged_signals = {**existing_signals, **signals}
            if signals.get("must_haves"):
                # Every need said so far: "aisle seat", then "travel insurance" keeps both.
                merged_signals["must_haves"] = merge_must_haves(
                    existing_signals.get("must_haves"), signals["must_haves"])
            # "Call me", then "text only" or "no calls": the newest wish wins
            # and the call is no longer owed (CodeRabbit #262).
            call_turned_down = turns_down_calls(signals.get("contact_channel"))
            if signals.get("contact_channel"):
                # "text only", then "please don't text me": the withdrawal
                # replaces the wish instead of both standing, and an earlier
                # "no calls" survives a later "no texts".
                merged_signals["contact_channel"] = merge_contact_channel(
                    existing_signals.get("contact_channel"), signals["contact_channel"],
                )
                if call_turned_down and merged_signals.get("callback_requested"):
                    merged_signals["callback_requested"] = False
            if merged_signals != existing_signals or raw_existing != existing_signals:
                lead_payload["intent_signals"] = merged_signals
                base_notes = lead_data.get("notes")
                if call_turned_down:
                    # The panel reads a CLIENT ASKED TO BE CALLED line as
                    # "Asks: a call back": it goes with the wish.
                    without_call = notes_without_call(
                        base_notes, texts="text" in {p.strip() for p in signals["contact_channel"].split(",")},
                    )
                    if without_call is not None:
                        base_notes = without_call
                        lead_payload["notes"] = without_call
                context_line = build_chat_context_line(merged_signals)
                if context_line:
                    notes = _append_note(base_notes, context_line)
                    if notes is not None:
                        lead_payload["notes"] = notes

        # Multi-city: the full leg chain leads the notes, BEFORE the persona
        # line — the consultant must see the real itinerary first (conv #1244).
        if entities.get("itinerary"):
            _itin_line = f"Itinerary: {entities['itinerary']}"
            _current_notes = lead_payload.get("notes", lead_data.get("notes") or "")
            _kept = [
                ln for ln in _current_notes.splitlines()
                if not ln.strip().startswith("Itinerary: ")
            ]
            _new_notes = "\n".join([_itin_line, *_kept]).strip()
            if _new_notes != (lead_data.get("notes") or "").strip():
                lead_payload["notes"] = _new_notes

        # RT 2-3: Update conv + lead in parallel (when both have data)
        tasks = []
        if conv_payload:
            tasks.append(
                _run_sync(
                    lambda: db_client.table("conversations")
                    .update(conv_payload)
                    .eq("id", conversation_id)
                    .execute()
                )
            )
        if lead_payload:
            tasks.append(
                _run_sync(
                    lambda: db_client.table("leads")
                    .update(lead_payload)
                    .eq("id", lead_id)
                    .execute()
                )
            )
        if tasks:
            # Both writes settle before an error goes on: one still in flight would land after the
            # lock is released, under the next writer's score (CodeRabbit #268, round 3).
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            # The chat's contact saved is told to the agent's open chat even when the lead's write failed
            # (CodeRabbit #282, round 3); the conversation's write is the first task when there is one.
            if conv_payload and not isinstance(outcomes[0], BaseException):
                await _tell_the_panel(conversation_id, conv_payload)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome

        # Score from merged local state (no re-select lead + conv): this
        # call's contacts first, then the ones the conversation already holds.
        merged_lead = {**lead_data, **lead_payload}
        merged_conv = {k: conv_payload.get(k) or (stored or {}).get(k) for k in _CONTACT_FIELDS}

        new_score = _calculate_score(merged_lead, merged_conv)
        new_tier = "gold" if new_score >= 80 else "silver" if new_score >= 50 else "bronze"

        # Stored contacts unread: this score is a floor, not the truth. It may
        # raise the stored score, never lower it.
        floor_below_stored = stored is None and new_score < (lead_data.get("score") or 0)
        if new_score != lead_data.get("score") and not floor_below_stored:
            await _run_sync(
                lambda: db_client.table("leads")
                .update({"score": new_score, "tier": new_tier})
                .eq("id", lead_id)
                .execute()
            )
        return contact_kept

    except Exception as e:
        # The chat reply stays alive, but the money path must SCREAM: full
        # exception + which entities were in flight + a /health counter.
        _entity_keys = sorted(k for k, v in (entities or {}).items() if v and not k.startswith("_"))
        logger.error(f"[{conversation_id}] Entities in flight: {_entity_keys}")
        _record_lead_write_error(conversation_id, "update_lead_from_entities", e)
        try:
            logger.error(f"[{conversation_id}] Lead payload was: {lead_payload}")
        except NameError:
            pass
        return False
    finally:
        lock.release()
