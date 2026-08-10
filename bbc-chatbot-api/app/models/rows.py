"""TypeAdapter contracts at the Supabase read boundary.

Rows come back from PostgREST as bare dicts; every consumer downstream
trusts their shape blindly. These contracts mirror the REAL columns
(migrations 001/023/024): chat_number / visitor_email / visitor_phone are
TOP-LEVEL columns on conversations (never metadata keys), and
intent_signals is `dict | list` until the 93% legacy array-shaped rows are
migrated.

OBSERVE-FIRST MODE: validation failure logs at WARNING with the row id and
returns the raw dict unchanged — production never breaks on a surprise
shape. Tightening to raise comes later, once the logs run quiet.
"""

import logging
from typing import Any, Optional

# NOT typing.TypedDict: pydantic's TypeAdapter over typing.TypedDict RAISES
# at import time on Python < 3.12 ("Please use typing_extensions.TypedDict").
# Railway runs python:3.11 — typing.TypedDict here 500'd every conversation
# read while local 3.13 test runs passed. typing_extensions ships with
# pydantic, so this import is always available.
from typing_extensions import TypedDict

from pydantic import TypeAdapter, ValidationError

logger = logging.getLogger(__name__)


class ConversationRow(TypedDict, total=False):
    id: str
    tunnel: str
    status: str
    mode: Optional[str]
    visitor_id: Optional[str]
    # TOP-LEVEL columns — NOT metadata keys (schema fact, migrations 001/023).
    visitor_name: Optional[str]
    visitor_email: Optional[str]
    visitor_phone: Optional[str]
    chat_number: Optional[int]
    assigned_agent_id: Optional[str]
    message_count: Optional[int]
    metadata: Optional[dict]
    last_user_message_at: Optional[str]
    last_agent_message_at: Optional[str]
    last_reply_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    closed_at: Optional[str]


class LeadRow(TypedDict, total=False):
    id: str
    conversation_id: str
    trip_type: Optional[str]
    cabin_class: Optional[str]
    passengers: Optional[int]
    flexible_dates: Optional[bool]
    origin_code: Optional[str]
    destination_code: Optional[str]
    departure_date: Optional[str]
    return_date: Optional[str]
    total_nights: Optional[int]
    route_display: Optional[str]
    score: Optional[int]
    tier: Optional[str]
    status: Optional[str]
    # dict on new rows, list on the 93% legacy rows — both are legal until
    # the backfill migration lands.
    intent_signals: Optional[dict | list]
    status_history: Optional[list]
    notes: Optional[str]
    children_count: Optional[int]
    infant_count: Optional[int]
    created_in_crm: Optional[bool]
    created_at: Optional[str]
    updated_at: Optional[str]


CONVERSATION_ROW = TypeAdapter(ConversationRow)
LEAD_ROW = TypeAdapter(LeadRow)


def observe_row(adapter: TypeAdapter, row: Any, kind: str) -> Any:
    """Validate a DB row against its contract — observe-first.

    Always returns the row unchanged; a mismatch logs at WARNING with the
    row id so shape drift is visible without breaking production. This
    function must be INFALLIBLE: no input — None, non-dict, adapter
    misbehavior — may ever raise out of an observation.
    """
    if not isinstance(row, dict):
        return row
    try:
        adapter.validate_python(row)
    except ValidationError as e:
        row_id = row.get("id", "?") if isinstance(row, dict) else "?"
        logger.warning(
            f"[rows] {kind} row {row_id} failed its contract "
            f"({e.error_count()} issue(s)): {e.errors()[:3]}"
        )
    except Exception as e:  # noqa: BLE001 — observation never breaks a read
        logger.warning(f"[rows] {kind} observation failed: {type(e).__name__}: {e}")
    return row


def observe_conversation_row(row: Any) -> Any:
    return observe_row(CONVERSATION_ROW, row, "conversation")


def observe_lead_row(row: Any) -> Any:
    return observe_row(LEAD_ROW, row, "lead")
