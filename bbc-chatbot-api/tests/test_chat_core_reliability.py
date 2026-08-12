"""Chat-core reliability — Wave 4 PR 1.

F1  First-contact routing counted ALL messages, but since #170 the AI
    greeting is message #1, so `count == 0` was unreachable and
    assignments flatlined (2026-08-06 23:41 UTC). Pin: the gate counts
    role="user" only, and count_messages(role=...) filters correctly.
F2  Presence writes (/open, /close, message⇒online) used the plain
    UPDATE, so trg_conversations_updated_at bumped updated_at and ghost
    conversations resurfaced. Pin: migration 029 GUC guard + rpc path +
    exactly the three presence sites switched, with a touching fallback
    when 029 isn't applied yet.
F3  Twin /start requests raced past Path B and inserted two active
    conversations per visitor. Pin: 029's partial unique index + the
    unique-violation re-select in get_or_create_conversation.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

API_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations", "029_touch_guard.sql")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ════════════════════════════════════════════════════════════
# F1 — routing gate counts client messages only
# ════════════════════════════════════════════════════════════

class TestRoleScopedCount:
    @pytest.mark.asyncio
    async def test_role_filter_applied_when_given(self):
        from app.db import supabase as sb

        q = MagicMock()
        q.eq.return_value = q
        q.execute.return_value = SimpleNamespace(count=3)
        client = MagicMock()
        client.table.return_value.select.return_value = q

        with patch.object(sb, "get_client", return_value=client):
            n = await sb.count_messages("conv-1", role="user")

        assert n == 3
        # both filters present: conversation_id AND role
        eq_calls = [c.args for c in q.eq.call_args_list]
        assert ("conversation_id", "conv-1") in eq_calls
        assert ("role", "user") in eq_calls

    @pytest.mark.asyncio
    async def test_no_role_filter_by_default(self):
        from app.db import supabase as sb

        q = MagicMock()
        q.eq.return_value = q
        q.execute.return_value = SimpleNamespace(count=7)
        client = MagicMock()
        client.table.return_value.select.return_value = q

        with patch.object(sb, "get_client", return_value=client):
            n = await sb.count_messages("conv-1")

        assert n == 7
        eq_calls = [c.args for c in q.eq.call_args_list]
        assert ("conversation_id", "conv-1") in eq_calls
        assert not any(a[0] == "role" for a in eq_calls)

    @pytest.mark.asyncio
    async def test_errors_still_fail_open_to_zero(self):
        from app.db import supabase as sb

        with patch.object(sb, "get_client", side_effect=RuntimeError("down")):
            assert await sb.count_messages("conv-1", role="user") == 0

    def test_gate_counts_user_messages_only(self):
        """The first-contact routing gate must count role="user" — an
        unscoped count includes the AI greeting and never equals 0."""
        src = _read(os.path.join(API_DIR, "api", "chat.py"))
        assert 'count_messages(req.conversation_id, role="user")' in src


# ════════════════════════════════════════════════════════════
# F2 — presence writes stop bumping updated_at
# ════════════════════════════════════════════════════════════

class TestTouchGuard:
    def test_migration_029_contract(self):
        sql = _read(MIGRATION)
        assert "app.skip_touch" in sql
        assert "update_conv_presence" in sql
        assert "set_config('app.skip_touch', '1', true)" in sql
        assert "uq_conv_active_visitor" in sql
        # guard must run BEFORE the touch inside the shared trigger fn
        guard = sql.index("current_setting('app.skip_touch', true)")
        touch = sql.index("NEW.updated_at = NOW()")
        assert guard < touch

    def test_exactly_three_presence_sites_switched(self):
        src = _read(os.path.join(API_DIR, "api", "chat.py"))
        assert src.count("db.update_conversation_presence(") == 3

    @pytest.mark.asyncio
    async def test_rpc_path_used(self):
        from app.db import supabase as sb

        rpc = MagicMock()
        rpc.execute.return_value = SimpleNamespace(data=None)
        client = MagicMock()
        client.rpc.return_value = rpc

        with patch.object(sb, "get_client", return_value=client):
            ok = await sb.update_conversation_presence("conv-1", {"widget_presence": "online"})

        assert ok is True
        client.rpc.assert_called_once_with(
            "update_conv_presence",
            {"p_conversation_id": "conv-1", "p_metadata": {"widget_presence": "online"}},
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_touching_update_when_rpc_missing(self):
        """029 lands in Supabase AFTER the deploy — presence must not
        break in the gap. rpc failure → plain update_conversation."""
        from app.db import supabase as sb

        client = MagicMock()
        client.rpc.side_effect = RuntimeError("function update_conv_presence does not exist")

        with patch.object(sb, "get_client", return_value=client), \
             patch.object(sb, "update_conversation", new_callable=AsyncMock) as upd:
            upd.return_value = {"id": "conv-1"}
            ok = await sb.update_conversation_presence("conv-1", {"widget_presence": "online"})

        assert ok is True
        upd.assert_awaited_once_with("conv-1", {"metadata": {"widget_presence": "online"}})


# ════════════════════════════════════════════════════════════
# F3 — atomic /start: twin loses the race, gets the winner's row
# ════════════════════════════════════════════════════════════

class TestStartDedup:
    @pytest.mark.asyncio
    async def test_unique_violation_reselects_winner(self):
        from app.db import supabase as sb

        winner = {"id": "conv-winner", "status": "active", "visitor_id": "v-1"}
        calls = {"n": 0}

        async def fake_run_sync(fn, **kwargs):  # accepts idempotent=...
            calls["n"] += 1
            if calls["n"] == 1:  # Path B select — nothing committed yet
                return SimpleNamespace(data=[])
            if calls["n"] == 2:  # insert — loses the race
                raise RuntimeError(
                    'duplicate key value violates unique constraint "uq_conv_active_visitor" (23505)'
                )
            return SimpleNamespace(data=[winner])  # re-select

        visitor = SimpleNamespace(name=None, email=None, phone=None, country_code=None)
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", side_effect=fake_run_sync):
            row = await sb.get_or_create_conversation(
                None, "sales", visitor, visitor_id="v-1"
            )

        assert row == winner

    @pytest.mark.asyncio
    async def test_other_insert_errors_still_return_none(self):
        from app.db import supabase as sb

        calls = {"n": 0}

        async def fake_run_sync(fn, **kwargs):  # accepts idempotent=...
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(data=[])
            raise RuntimeError("connection reset")

        visitor = SimpleNamespace(name=None, email=None, phone=None, country_code=None)
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", side_effect=fake_run_sync):
            row = await sb.get_or_create_conversation(
                None, "sales", visitor, visitor_id="v-1"
            )

        assert row is None
