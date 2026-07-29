"""Lead service — creation and updates. Score calculated EXCLUSIVELY in Python."""
import asyncio
import logging
import re
from datetime import date
from typing import Optional

from app.pipeline.entity_extractor import DREAM_OUTCOMES

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


async def get_or_create_lead(conversation_id: str) -> Optional[dict]:
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()
        res = await _run_sync(lambda: db_client.table("leads").select("*").eq("conversation_id", conversation_id).limit(1).execute())
        return res.data[0] if res.data else None  # type: ignore[index]
    except Exception as e:
        logger.error(f"get_or_create_lead error: {e}")
        return None


# ── Sales-methodology signals ─────────────────────────────────────
# Read the client once, keep what they volunteer, hand it to the consultant.
SIGNAL_KEYS = (
    "occasion",
    "persona",
    "persona_source",
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


async def update_lead_from_entities(conversation_id: str, entities: dict) -> None:
    """Update lead with entities extracted by AI. Optimized: 3-4 RT instead of 7."""
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()

        # RT 1: Select lead (travel fields only — visitor_* live on conversations)
        res = await _run_sync(
            lambda: db_client.table("leads")
            .select(
                "id,score,origin_code,destination_code,departure_date,"
                "passengers,cabin_class,return_date,trip_type,"
                "children_count,infant_count,intent_signals,notes"
            )
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
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
                return
            # RT 2: Insert lead
            lead_res = await _run_sync(
                lambda: db_client.table("leads")
                .insert({"conversation_id": conversation_id})
                .execute()
            )
            if not lead_res.data:
                return
            lead_id = lead_res.data[0]["id"]  # type: ignore[index]
            lead_data = lead_res.data[0]  # type: ignore[index]
            logger.info(f"[{conversation_id}] Lead INSERT success: {lead_id}")
        else:
            lead_id = res.data[0]["id"]  # type: ignore[index]
            lead_data = res.data[0]  # type: ignore[index]

        conv_payload: dict = {}
        if entities.get("name"):
            conv_payload["visitor_name"] = entities["name"]
        if entities.get("email"):
            conv_payload["visitor_email"] = entities["email"]
        if entities.get("phone"):
            from app.services.crm import format_phone_international
            conv_payload["visitor_phone"] = format_phone_international(entities["phone"])

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
            if _dep:
                lead_payload["departure_date"] = _dep
        if entities.get("return_date"):
            _ret = _validate_future_date(entities["return_date"])
            if _ret:
                lead_payload["return_date"] = _ret
        if entities.get("trip_type"):
            lead_payload["trip_type"] = entities["trip_type"]
        if entities.get("_children_count"):
            lead_payload["children_count"] = entities["_children_count"]
        if entities.get("_infant_count"):
            lead_payload["infant_count"] = entities["_infant_count"]
        if entities.get("origin") and entities.get("destination"):
            lead_payload["route_display"] = f"{entities['origin']} → {entities['destination']}"

        # Methodology signals ride the same UPDATE: merged into intent_signals
        # so other keys survive, and written only when something changed.
        signals = signals_from_entities(entities)
        if signals:
            existing_signals = lead_data.get("intent_signals")
            if not isinstance(existing_signals, dict):
                existing_signals = {}
            merged_signals = {**existing_signals, **signals}
            if merged_signals != existing_signals:
                lead_payload["intent_signals"] = merged_signals
                context_line = build_chat_context_line(merged_signals)
                if context_line:
                    notes = _append_note(lead_data.get("notes"), context_line)
                    if notes is not None:
                        lead_payload["notes"] = notes

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
            await asyncio.gather(*tasks)

        # Score from merged local state (no re-select lead + conv)
        merged_lead = {**lead_data, **lead_payload}
        merged_conv = {
            "visitor_name": conv_payload.get("visitor_name") or entities.get("name"),
            "visitor_email": conv_payload.get("visitor_email") or entities.get("email"),
            "visitor_phone": conv_payload.get("visitor_phone"),
        }
        if not merged_conv.get("visitor_phone") and entities.get("phone"):
            from app.services.crm import format_phone_international
            merged_conv["visitor_phone"] = format_phone_international(entities["phone"])

        new_score = _calculate_score(merged_lead, merged_conv)
        new_tier = "gold" if new_score >= 80 else "silver" if new_score >= 50 else "bronze"

        if new_score != lead_data.get("score"):
            await _run_sync(
                lambda: db_client.table("leads")
                .update({"score": new_score, "tier": new_tier})
                .eq("id", lead_id)
                .execute()
            )

    except Exception as e:
        logger.error(
            f"[{conversation_id}] update_lead_from_entities FAILED: {type(e).__name__}: {e}"
        )
        try:
            logger.error(f"[{conversation_id}] Lead payload was: {lead_payload}")
        except NameError:
            pass
