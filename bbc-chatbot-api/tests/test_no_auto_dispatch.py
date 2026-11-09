"""D4 — one distribution mechanism. The three automatic drains are off.

If any of them stayed alive next to the queue, "first click wins" would become
"whichever browser pinged first wins" — and the others would watch a row vanish
with no explanation, which is the behaviour this wave removed.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from config.settings import settings
from app.services import routing
from app.services.routing import dispatch_needs_agent


def test_the_flag_ships_off():
    assert settings.auto_dispatch_enabled is False


# ══════════════════════════════════════════════════════════════
# Drain 1 — close auto-assign
# ══════════════════════════════════════════════════════════════

def test_close_auto_assign_is_behind_the_flag():
    import inspect

    from app.api import conversations as capi

    src = inspect.getsource(capi.close_conversation)
    assert "settings.auto_dispatch_enabled and role not in db._MANAGEMENT_ROLES" in src


# ══════════════════════════════════════════════════════════════
# Drain 2 — heartbeat assign
# ══════════════════════════════════════════════════════════════

def test_heartbeat_assign_is_behind_the_flag():
    import inspect

    from app.api import agent as agent_api

    src = inspect.getsource(agent_api)
    assert "settings.auto_dispatch_enabled" in src
    i = src.index("settings.auto_dispatch_enabled")
    assert "is_ready" in src[i:i + 200], "the flag guards the same condition chain"


# ══════════════════════════════════════════════════════════════
# Drain 3 — dispatch_needs_agent refuses, but still shouts
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dispatch_returns_false_and_assigns_nothing():
    conv = {"id": "c1", "status": "needs_agent", "assigned_agent_id": None,
            "tunnel": "sales", "visitor_name": "V", "visitor_phone": None,
            "visitor_email": None, "chat_number": 9, "metadata": {}}
    alert = AsyncMock()
    handoff = AsyncMock()
    with patch.object(routing.db, "get_conversation_simple",
                      new=AsyncMock(return_value=conv)), \
         patch.object(routing, "_needs_agent_super_alert", alert), \
         patch("app.services.handoff.perform_handoff_to_agent", handoff):
        out = await dispatch_needs_agent("c1")
    assert out is False
    handoff.assert_not_awaited(), "no assignment"
    alert.assert_awaited_once(), "the shout survives the cut"


@pytest.mark.asyncio
async def test_the_queue_is_untouched_by_a_refused_dispatch():
    """No write reaches the conversation: queued_at intact, still unassigned."""
    conv = {"id": "c1", "status": "needs_agent", "assigned_agent_id": None,
            "tunnel": "sales", "metadata": {}}
    upd = AsyncMock()
    with patch.object(routing.db, "get_conversation_simple",
                      new=AsyncMock(return_value=conv)), \
         patch.object(routing, "_needs_agent_super_alert", new=AsyncMock()), \
         patch.object(routing.db, "update_conversation", upd):
        await dispatch_needs_agent("c1")
    upd.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_flag_restores_the_old_path():
    """auto_dispatch_enabled=True: dispatch tries to route again (smoke)."""
    conv = {"id": "c1", "status": "needs_agent", "assigned_agent_id": None,
            "tunnel": "sales", "visitor_name": "V", "visitor_phone": None,
            "visitor_email": None, "visitor_id": None, "chat_number": 9,
            "metadata": {}}
    route = AsyncMock(return_value=None)
    with patch.object(settings, "auto_dispatch_enabled", True), \
         patch.object(routing.db, "get_conversation_simple",
                      new=AsyncMock(return_value=conv)), \
         patch.object(routing, "route_conversation", route), \
         patch.object(routing, "_needs_agent_super_alert", new=AsyncMock()):
        out = await dispatch_needs_agent("c1")
    route.assert_awaited_once(), "the old path is one flag away, no revert needed"
    assert out is False


def test_the_five_callers_are_untouched():
    """The function refuses by itself; every caller stays as it was.

    Since 052 the four that set needs_agent first reach it through one door,
    queue_for_person (dispatch on by default), which fires it only once the
    write held; the fifth, /open's self-heal, still calls it directly. The
    two that never dispatched (nobody free in the pipeline, the frustration
    lane) still don't."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent

    def _src(rel):
        return (root / rel).read_text(encoding="utf-8")

    def _door_calls(rel):
        """(dispatching, quiet) calls to queue_for_person in one file."""
        on = off = 0
        for node in ast.walk(ast.parse(_src(rel))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "queue_for_person":
                quiet = any(
                    k.arg == "dispatch" and isinstance(k.value, ast.Constant) and k.value.value is False
                    for k in node.keywords
                )
                off += quiet
                on += not quiet
        return on, off

    assert _src("app/api/chat.py").count("_fire_and_forget(dispatch_needs_agent(") == 1
    assert _src("app/services/routing.py").count("_fire_and_forget(dispatch_needs_agent(") == 1
    assert _door_calls("app/pipeline/orchestrator.py") == (3, 1)
    assert _door_calls("app/services/handoff.py") == (1, 0)
    assert _door_calls("app/security/behavior.py") == (0, 1)
