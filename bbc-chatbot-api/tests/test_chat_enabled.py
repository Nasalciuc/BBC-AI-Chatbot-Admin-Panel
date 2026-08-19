"""033 — chat_enabled: the management-controlled right to be in the chat system.

About 5 of the 63 accounts are senior sellers who have not worked a chat in
months. That is a permanent state, not a pause. One column, checked in four
places: legacy routing (get_available_agents), heartbeat-assign, the CRM
presence gate, and sticky routing.

And A1/A2 land here too: is_ready leaves the CRM gate and sticky. The pulse
already says "I am here"; the defence against a lying pulse is the response
deadline plus the idempotent fallback from #211, not a button.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from datetime import datetime, timedelta, timezone

from app.api.integration import evaluate_presence
from app.db import supabase as sb
from app.services import routing

WINDOW = 90


def _user(role="sales", ready=True, seen_seconds_ago=5, active=True, chat_enabled=True):
    seen = (
        None if seen_seconds_ago is None
        else (datetime.now(timezone.utc) - timedelta(seconds=seen_seconds_ago)).isoformat()
    )
    return {"id": "u1", "email": "agent@bbc.com", "role": role, "is_ready": ready,
            "last_seen_at": seen, "is_active": active, "chat_enabled": chat_enabled}


class _UsersTable:
    """A users table whose select/filter chain records the filters applied."""

    def __init__(self, rows, raise_missing_column=False):
        self.rows = rows
        self.filters = []
        self.selected = ""
        self.raise_missing = raise_missing_column

    def table(self, _name):
        return self

    def select(self, cols, **_kw):
        self.selected = cols
        # A new query chain starts here — keep the last chain's filters in
        # `last_filters` for assertions, but judge execute() on THIS chain.
        self.last_filters = list(self.filters)
        self.filters = []
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def gt(self, *a):
        return self

    def or_(self, *a):
        return self

    def in_(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a):
        return self

    def execute(self):
        if self.raise_missing and ("chat_enabled", True) in self.filters:
            raise RuntimeError('column users.chat_enabled does not exist (42703)')
        rows = self.rows
        if ("chat_enabled", True) in self.filters:
            rows = [r for r in rows if r.get("chat_enabled", True)]
        if ("is_ready", True) in self.filters:
            rows = [r for r in rows if r.get("is_ready")]
        return MagicMock(data=rows, count=len(rows))


# ══════════════════════════════════════════════════════════════
# 1-2 · legacy routing and heartbeat respect the right
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_disabled_operator_absent_from_get_available_agents():
    table = _UsersTable([
        _user(chat_enabled=True) | {"id": "a"},
        _user(chat_enabled=False) | {"id": "b"},
    ])
    with patch.object(sb, "get_client", return_value=table), \
         patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        sb._chat_enabled_ok = None
        agents = await sb.get_available_agents("sales")
    assert [a["id"] for a in agents] == ["a"]
    assert ("chat_enabled", True) in table.filters


def test_heartbeat_gate_requires_chat_enabled():
    import inspect

    from app.api import agent as agent_api

    src = inspect.getsource(agent_api)
    # Commit 5 folded the same condition chain behind auto_dispatch_enabled;
    # the chat_enabled gate is still part of it.
    assert "and is_ready" in src and "and chat_enabled" in src
    assert 'get("chat_enabled", True)' in src, "absent column must read as True"


# ══════════════════════════════════════════════════════════════
# 3-5 · the CRM gate: chat_disabled vs wrong_role, and A1
# ══════════════════════════════════════════════════════════════

def test_disabled_sales_reads_chat_disabled_not_wrong_role():
    out = evaluate_presence(_user(chat_enabled=False), WINDOW)
    assert out["exempt"] is True
    assert out["ready"] is False
    assert out["reason"] == "chat_disabled", "their role is right; their right was withdrawn"


def test_admin_still_reads_wrong_role():
    out = evaluate_presence(_user(role="admin"), WINDOW)
    assert out["exempt"] is True
    assert out["reason"] == "wrong_role"


def test_a1_fresh_pulse_with_ready_button_off_is_ready():
    """The button is gone from this gate: presence is the pulse."""
    out = evaluate_presence(_user(ready=False, seen_seconds_ago=3), WINDOW)
    assert out["ready"] is True
    assert out["reason"] == "ok"


def test_not_ready_is_no_longer_a_reason():
    import inspect

    from app.api import integration

    assert '"not_ready"' not in inspect.getsource(integration)


def test_offline_and_inactive_still_block():
    assert evaluate_presence(_user(seen_seconds_ago=500), WINDOW)["reason"] == "offline"
    assert evaluate_presence(_user(active=False), WINDOW)["reason"] == "inactive"


# ══════════════════════════════════════════════════════════════
# 6-7 · sticky: A2 in, chat_enabled respected
# ══════════════════════════════════════════════════════════════

def _sticky_agent(**kw):
    base = {"id": "op1", "is_active": True, "is_ready": False, "role": "sales",
            "chat_enabled": True,
            "last_seen_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_sticky_returns_operator_with_ready_button_off():
    """A2: the returning client reaches the operator who knows them if that
    operator is at their desk — a forgotten button must not reroute them."""
    import inspect

    src = inspect.getsource(routing._find_sticky_operator)
    assert 'agent.get("is_ready")' not in src, "is_ready deliberately not checked"
    assert 'agent.get("chat_enabled", True)' in src


@pytest.mark.asyncio
async def test_sticky_refuses_disabled_operator():
    import inspect

    src = inspect.getsource(routing._find_sticky_operator)
    # The chat_enabled check must come before the management-roles check,
    # mirroring the spec order.
    assert src.index("chat_enabled") < src.index("_MANAGEMENT_ROLES")


# ══════════════════════════════════════════════════════════════
# 8-9 · compatibility and the untouched legacy gate
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_missing_column_downgrades_once_and_retries_without_filter(caplog):
    import logging

    table = _UsersTable([_user() | {"id": "a"}], raise_missing_column=True)
    sb._chat_enabled_ok = None
    sb._chat_enabled_downgraded_at = None
    with patch.object(sb, "get_client", return_value=table), \
         patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())), \
         patch.object(sb, "_is_missing_column_error", return_value=True), \
         caplog.at_level(logging.ERROR):
        agents = await sb.get_available_agents("sales")
        assert [a["id"] for a in agents] == ["a"], "query reruns without the filter"
        first_errors = sum(1 for r in caplog.records if "chat_enabled is missing" in r.message)
        assert first_errors == 1
        # Second call inside the TTL: no second scream.
        await sb.get_available_agents("sales")
        total_errors = sum(1 for r in caplog.records if "chat_enabled is missing" in r.message)
        assert total_errors == 1
    sb._chat_enabled_ok = None
    sb._chat_enabled_downgraded_at = None


@pytest.mark.asyncio
async def test_legacy_routing_still_requires_is_ready():
    """The old first-message routing keeps its gate — 032 exists precisely
    because is_ready still guards it."""
    table = _UsersTable([
        _user(ready=True) | {"id": "a"},
        _user(ready=False) | {"id": "b"},
    ])
    with patch.object(sb, "get_client", return_value=table), \
         patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        sb._chat_enabled_ok = None
        agents = await sb.get_available_agents("sales")
    assert [a["id"] for a in agents] == ["a"]
    assert ("is_ready", True) in table.filters


# ══════════════════════════════════════════════════════════════
# 10-11 · the toggle: server-side guard and the audit trail
# ══════════════════════════════════════════════════════════════

def test_userupdate_carries_chat_enabled():
    from app.models.admin import UserUpdate

    u = UserUpdate(chat_enabled=False)
    assert u.model_dump(exclude_none=True) == {"chat_enabled": False}


def test_sales_patching_chat_enabled_is_403_by_the_existing_guard():
    """A non-privileged actor with chat_enabled in the payload falls through
    the pm_team_only branch and hits the 403 — pinned at source level."""
    import inspect

    from app.api import users as users_api

    src = inspect.getsource(users_api.update_user)
    assert "Only owner/admin can modify access rights" in src
    assert 'set(payload.keys()) == {"team_id"}' in src


def test_toggle_writes_audit_log():
    import inspect

    from app.api import users as users_api

    src = inspect.getsource(users_api.update_user)
    assert 'action="chat_enabled_toggle"' in src
    assert 'target_table="users"' in src
    assert '"from": bool(existing.get("chat_enabled", True))' in src


def test_log_audit_never_raises():
    import inspect

    src = inspect.getsource(sb.log_audit)
    assert "except Exception" in src and "logger.warning" in src
