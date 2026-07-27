"""Abuse blocklist — block a bad actor by phone / email, record their IP.

Matching policy (deliberate, see migrations/022_blocklist.sql):

  * phone matches a blocked phone  -> BLOCKED
  * email matches a blocked email  -> BLOCKED
  * ip matches a blocked ip        -> NOT blocked on its own

IP never refuses anyone by itself. IPs are shared (offices, hotels, mobile
carriers, CGNAT), so an IP-only rule would refuse innocent visitors who happen
to sit behind the same address as an abuser. The IP row is still recorded when
staff hit Block, for audit, for the management view, and so it is available if
identity correlation is ever added. IP entries expire
(settings.blocklist_ip_ttl_days) so a stale address can never resurface.

Honest limitation: a determined abuser changes IP with a VPN in seconds and can
fake contact details. This raises the cost of abuse; it is not a wall.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import settings
from app.db import supabase as db

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = "We're unable to process your request."


def _norm_phone(v: Optional[str]) -> str:
    """Digits only, so '+1 844-770-4910' and '18447704910' compare equal."""
    return "".join(c for c in (v or "") if c.isdigit())


def _norm_email(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _norm_ip(v: Optional[str]) -> str:
    return (v or "").strip()


async def is_blocked(
    *,
    ip: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> bool:
    """True only when a blocked PHONE or EMAIL is presented.

    `ip` is accepted so callers can pass everything they know without caring
    about the policy, but a blocked IP alone never produces True — that is the
    guarantee that protects innocent visitors sharing an address.
    """
    p = _norm_phone(phone)
    if p and await db.blocklist_has_active("phone", p):
        logger.info("Blocklist HIT (phone)")
        return True

    e = _norm_email(email)
    if e and await db.blocklist_has_active("email", e):
        logger.info("Blocklist HIT (email)")
        return True

    return False


async def block_from_conversation(
    conv: dict,
    blocked_by_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> list[str]:
    """Block a conversation's visitor: IP + phone + email in one action.

    Phone/email are permanent; the IP entry expires after
    settings.blocklist_ip_ttl_days. Returns the kinds actually recorded.
    """
    if not conv:
        return []

    conv_id = conv.get("id")
    meta = conv.get("metadata") or {}

    ip = _norm_ip(meta.get("client_ip"))
    phone = _norm_phone(conv.get("visitor_phone"))
    email = _norm_email(conv.get("visitor_email"))

    blocked: list[str] = []

    if phone and await db.blocklist_upsert(
        "phone", phone, reason=reason, blocked_by=blocked_by_id,
        conversation_id=conv_id, expires_at=None,
    ):
        blocked.append("phone")

    if email and await db.blocklist_upsert(
        "email", email, reason=reason, blocked_by=blocked_by_id,
        conversation_id=conv_id, expires_at=None,
    ):
        blocked.append("email")

    if ip:
        expires = (
            datetime.now(timezone.utc)
            + timedelta(days=settings.blocklist_ip_ttl_days)
        ).isoformat()
        if await db.blocklist_upsert(
            "ip", ip, reason=reason, blocked_by=blocked_by_id,
            conversation_id=conv_id, expires_at=expires,
        ):
            blocked.append("ip")

    logger.info(
        f"Blocklist ADD conv={conv_id} kinds={blocked} by={blocked_by_id}"
    )
    return blocked


async def unblock_from_conversation(conv: dict) -> list[str]:
    """Remove this conversation's IP / phone / email from the blocklist."""
    if not conv:
        return []

    meta = conv.get("metadata") or {}
    removed: list[str] = []

    for kind, value in (
        ("phone", _norm_phone(conv.get("visitor_phone"))),
        ("email", _norm_email(conv.get("visitor_email"))),
        ("ip", _norm_ip(meta.get("client_ip"))),
    ):
        if value and await db.blocklist_delete(kind, value):
            removed.append(kind)

    logger.info(
        f"Blocklist REMOVE conv={conv.get('id')} kinds={removed}"
    )
    return removed
