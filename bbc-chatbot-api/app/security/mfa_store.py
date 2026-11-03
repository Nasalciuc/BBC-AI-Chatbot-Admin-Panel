"""Rows behind the second factor (PR-13): the user's encrypted secret, the
recovery codes, the places an account logged in from. Every call degrades:
a missing 043 table is logged once and treated as "nothing stored"."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.supabase import _run_sync, get_client

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def set_pending_secret(user_id: str, secret_enc: str) -> bool:
    """Start (or restart) an enrolment: the secret is stored, not yet active."""
    try:
        await _run_sync(
            lambda: get_client().table("users")
            .update({"totp_secret_enc": secret_enc, "mfa_enrolled_at": None})
            .eq("id", user_id).execute(),
            label="mfa",
        )
        return True
    except Exception as e:
        logger.warning(f"[mfa] storing the pending secret failed: {type(e).__name__}: {e}")
        return False


async def mark_enrolled(user_id: str) -> bool:
    try:
        await _run_sync(
            lambda: get_client().table("users")
            .update({"mfa_enrolled_at": _now()}).eq("id", user_id).execute(),
            label="mfa",
        )
        return True
    except Exception as e:
        logger.warning(f"[mfa] marking enrolment failed: {type(e).__name__}: {e}")
        return False


async def replace_recovery_codes(user_id: str, hashes: list[str]) -> bool:
    try:
        await _run_sync(
            lambda: get_client().table("mfa_recovery_codes").delete().eq("user_id", user_id).execute(),
            label="mfa",
        )
        rows = [{"user_id": user_id, "code_hash": h} for h in hashes]
        await _run_sync(
            lambda: get_client().table("mfa_recovery_codes").insert(rows).execute(),
            idempotent=False,
            label="mfa",
        )
        return True
    except Exception as e:
        logger.warning(f"[mfa] storing recovery codes failed: {type(e).__name__}: {e}")
        return False


async def unused_recovery_codes(user_id: str) -> list[dict]:
    try:
        res = await _run_sync(
            lambda: get_client().table("mfa_recovery_codes")
            .select("id,code_hash").eq("user_id", user_id).is_("used_at", "null").execute(),
            label="mfa",
        )
        return getattr(res, "data", None) or []
    except Exception as e:
        logger.warning(f"[mfa] reading recovery codes failed: {type(e).__name__}: {e}")
        return []


async def use_recovery_code(code_id: str) -> bool:
    """Mark used — only if still unused, so a code can never work twice."""
    try:
        res = await _run_sync(
            lambda: get_client().table("mfa_recovery_codes")
            .update({"used_at": _now()}).eq("id", code_id).is_("used_at", "null").execute(),
            label="mfa",
        )
        return bool(getattr(res, "data", None))
    except Exception as e:
        logger.warning(f"[mfa] marking a recovery code used failed: {type(e).__name__}: {e}")
        return False


async def note_login_place(user_id: str, place: str) -> Optional[bool]:
    """True when this place is new for an account that has logged in before;
    False when known (or the first ever login); None when it could not be read."""
    if not place:
        return None
    try:
        res = await _run_sync(
            lambda: get_client().table("login_locations")
            .select("place").eq("user_id", user_id).execute(),
            label="mfa",
        )
        places = {r.get("place") for r in (getattr(res, "data", None) or [])}
        if place in places:
            await _run_sync(
                lambda: get_client().table("login_locations")
                .update({"last_seen_at": _now()}).eq("user_id", user_id).eq("place", place).execute(),
                label="mfa",
            )
            return False
        await _run_sync(
            lambda: get_client().table("login_locations")
            .insert({"user_id": user_id, "place": place}).execute(),
            idempotent=False,
            label="mfa",
        )
        return bool(places)
    except Exception as e:
        logger.warning(f"[mfa] login place check failed: {type(e).__name__}: {e}")
        return None
