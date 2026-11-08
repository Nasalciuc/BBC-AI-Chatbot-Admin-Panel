"""The access log on Users & access: every change to who can sign in and
what they can do, from rows the server already writes.

  * user_access_audit — role, tunnel and active changes made in the panel;
  * audit_log         — "Receives chats" switches and manual password resets;
  * invite_tokens     — every invite sent from the panel (created_by set).

Profile edits (name, phone, photo) and team moves are not recorded anywhere,
so they are not listed. Pure: the rows are already read.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Optional

from app.services.agent_day import parse_ts

# An invite sent within this long of the account's creation is the first one.
_FIRST_INVITE_WINDOW = timedelta(minutes=10)


def _name(users: dict[str, dict], uid: Optional[str]) -> Optional[str]:
    if not uid:
        return None
    u = users.get(uid) or {}
    return u.get("name") or u.get("email") or "A former user"


def build_access_log(
    *, audit: list[dict], toggles: list[dict], invites: list[dict], users: dict[str, dict], limit: int = 200
) -> list[dict]:
    events: list[dict[str, Any]] = []
    role_changes: dict[str, list[tuple[Any, str]]] = defaultdict(list)

    def add(at_raw, kind, actor, target, **extra):
        at = parse_ts(at_raw)
        if not at:
            return
        events.append({
            "at": at.isoformat(), "kind": kind,
            "actor_id": actor, "actor_name": _name(users, actor),
            "target_id": target, "target_name": _name(users, target),
            **extra,
        })

    for a in audit:
        fields = a.get("changed_fields") or []
        actor, target = a.get("changed_by_user_id"), a.get("target_user_id")
        if "is_active" in fields:
            add(a.get("created_at"), "reactivated" if a.get("new_is_active") else "deactivated", actor, target)
        if "role" in fields:
            add(a.get("created_at"), "role", actor, target, old=a.get("old_role"), new=a.get("new_role"))
            at = parse_ts(a.get("created_at"))
            if at:
                role_changes[target].append((at, a.get("old_role")))
        elif "tunnel_scope" in fields:
            add(a.get("created_at"), "tunnel", actor, target,
                old=a.get("old_tunnel_scope"), new=a.get("new_tunnel_scope"))

    for t in toggles:
        details = t.get("details") or {}
        if t.get("action") == "chat_enabled_toggle":
            add(t.get("created_at"), "chats_on" if details.get("to") else "chats_off", t.get("user_id"), t.get("target_id"))
        elif t.get("action") == "password_reset_manual":
            add(t.get("created_at"), "password_reset", t.get("user_id"), t.get("target_id"))

    for inv in invites:
        target, at = inv.get("user_id"), parse_ts(inv.get("created_at"))
        if not target or not at:
            continue
        # The role at the time: the old role of the first change after it,
        # or the role they still have.
        later = sorted((c for c in role_changes.get(target, []) if c[0] > at), key=lambda c: c[0])
        role = later[0][1] if later else (users.get(target) or {}).get("role")
        created = parse_ts((users.get(target) or {}).get("created_at"))
        first = bool(created and abs(at - created) <= _FIRST_INVITE_WINDOW)
        add(inv.get("created_at"), "invited" if first else "invite_again", inv.get("created_by"), target, role=role)

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
