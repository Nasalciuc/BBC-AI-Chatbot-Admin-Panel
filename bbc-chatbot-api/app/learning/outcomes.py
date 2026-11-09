"""The reward loop (PR-8): which question phrasing actually got the detail.

When an AI reply asks exactly one slot, record_ask() writes a question_outcomes
row with answered=NULL. On the client's next message, after extraction,
resolve_outcomes() sets answered=true if that slot is now filled on the lead,
else false. That true/false is what feeds the Thompson bandit.

A conversation is in the control group (bucket < CONTROL_GROUP_PERCENT, R11):
it always gets the seed variant and no lessons, and its outcomes are marked
control=true so the report can compare treatment vs control.

All writes are best-effort and gated by LEARNING_ENABLED; a missing table or a
failed write never disturbs a chat turn. A failure is not raised into the turn,
so it is counted (OUTCOMES_HEALTH, in /health/detail) and logged at WARNING;
only the table that is not there yet (041 unapplied) stays at DEBUG.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.experiments import bucket
from app.flags import flag

logger = logging.getLogger(__name__)

# Rule 7: record_ask returns nothing and resolve_outcomes 0 on a failure, so
# the counters are what tell "nothing to do" from "it broke".
OUTCOMES_HEALTH: dict[str, int] = {"record_failed": 0, "resolve_failed": 0, "table_missing": 0}
_TABLE_MISSING_CODES = ("42P01", "PGRST205")


def _table_missing(e: Exception) -> bool:
    """Migration 041 not applied: the database's code for a missing table,
    never the error's text."""
    from app.db.supabase import _error_code

    return _error_code(e) in _TABLE_MISSING_CODES


# The database's codes, and only those, reach a log line: PostgREST's own
# (PGRST and three digits) or a SQLSTATE: five characters, a class from
# PostgreSQL's list (Appendix A) and at least one digit, as every SQLSTATE
# has. An exception that isn't the database's could carry a name in .code
# ("MARIA" has the shape of a SQLSTATE, but no class and no digit).
_SQLSTATE_CLASSES = frozenset(
    "00 01 02 03 08 09 0A 0B 0F 0L 0P 0Z 20 21 22 23 24 25 26 27 28 2B 2D 2F "
    "34 38 39 3B 3D 3F 40 42 44 53 54 55 57 58 72 F0 HV P0 XX".split()
)
_POSTGREST_CODE = re.compile(r"PGRST\d{3}")
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")


def _loggable_code(code: Optional[str]) -> str:
    if not code:
        return ""
    if _POSTGREST_CODE.fullmatch(code):
        return code
    if _SQLSTATE.fullmatch(code) and code[:2] in _SQLSTATE_CLASSES and any(c.isdigit() for c in code):
        return code
    return "unrecognised-code"


def _what(e: Exception) -> str:
    """The exception's type and the database's code, nothing else: a message
    can carry the submitted values ("invalid input syntax …: \"Ana\""), and
    the details can quote the whole row (CLAUDE.md: no PII in logs at INFO or above)."""
    from app.db.supabase import _error_code

    return f"{type(e).__name__} {_loggable_code(_error_code(e))}".strip()

_SLOT_LEAD_FIELD = {
    "route": ("origin_code", "destination_code"),
    "departure_date": ("departure_date",),
    "return_date": ("return_date",),
    "passengers": ("passengers",),
    "trip_type": ("trip_type",),
}


def is_control(conversation_id: Optional[str]) -> bool:
    b = bucket("learning_control", conversation_id)
    if b is None:
        return False
    return b < int(flag("CONTROL_GROUP_PERCENT", 10))


def _slot_filled(slot: str, lead: dict) -> bool:
    fields = _SLOT_LEAD_FIELD.get(slot, ())
    if not fields:
        return False
    if slot == "trip_type":
        # The column defaults to 'round_trip', so a stored value is not an
        # answer: use the slot policy's own rule (the client said it).
        from app.pipeline.slots import _trip_type_from_client

        return _trip_type_from_client(lead or {})
    return all((lead or {}).get(f) for f in fields)


async def record_ask(conversation_id: str, message_id: Optional[str], slot: str,
                     variant_id: Optional[str], segment: Optional[str] = None) -> None:
    if not flag("LEARNING_ENABLED", True) or not slot:
        return
    try:
        from app.db import supabase as db
        row = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "slot": slot,
            "variant_id": variant_id,
            "segment": segment,
            "control": is_control(conversation_id),
            "answered": None,
        }
        await db._run_sync(
            lambda: db.get_client().table("question_outcomes").insert(row).execute(),
            idempotent=False, label="question_outcomes",
        )
    except Exception as e:
        if _table_missing(e):
            OUTCOMES_HEALTH["table_missing"] += 1
            logger.debug(f"[outcomes] record_ask skipped, question_outcomes not created yet (041): {_what(e)}")
        else:
            OUTCOMES_HEALTH["record_failed"] += 1
            logger.warning(f"[outcomes] record_ask failed, no reward row: {_what(e)}")


async def resolve_outcomes(conversation_id: str, lead: Optional[dict] = None) -> int:
    """Resolve this conversation's open asks against the current lead. Returns
    how many were resolved; 0 on a failure too, which OUTCOMES_HEALTH counts
    (the only caller schedules it in the background and never reads it)."""
    if not flag("LEARNING_ENABLED", True):
        return 0
    try:
        from app.db import supabase as db
        client = db.get_client()
        if lead is None:
            from app.services import lead_service
            lead = await lead_service.get_or_create_lead(conversation_id)
        res = await db._run_sync(
            lambda: client.table("question_outcomes").select("id,slot")
            .eq("conversation_id", conversation_id).is_("answered", "null").execute(),
            label="question_outcomes",
        )
        rows = getattr(res, "data", None) or []
        resolved = 0
        for r in rows:
            answered = _slot_filled(r["slot"], lead or {})
            await db._run_sync(
                lambda rid=r["id"], a=answered: client.table("question_outcomes")
                .update({"answered": a}).eq("id", rid).execute(),
                idempotent=False, label="question_outcomes",
            )
            resolved += 1
        return resolved
    except Exception as e:
        if _table_missing(e):
            OUTCOMES_HEALTH["table_missing"] += 1
            logger.debug(f"[outcomes] resolve skipped, question_outcomes not created yet (041): {_what(e)}")
        else:
            OUTCOMES_HEALTH["resolve_failed"] += 1
            logger.warning(f"[outcomes] resolve failed, open asks left unresolved: {_what(e)}")
        return 0


def _reset_for_tests() -> None:
    for k in OUTCOMES_HEALTH:
        OUTCOMES_HEALTH[k] = 0
