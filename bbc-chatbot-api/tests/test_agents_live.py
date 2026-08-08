"""GET /admin/agents/live — the who's-live source for the Team live card."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.users import get_live_agents


def _row(uid, name, role, ready, last_seen):
    return {
        "id": uid, "name": name, "role": role,
        "is_ready": ready, "last_seen_at": last_seen,
    }


async def _fake_run_sync(fn):
    return fn()


def _client_with(rows):
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.order.return_value = chain
    chain.execute.return_value = MagicMock(data=rows)
    client.table.return_value = chain
    return client


class TestAgentsLive:
    @pytest.mark.asyncio
    async def test_shape_and_online_derivation(self):
        now = datetime.now(timezone.utc)
        rows = [
            _row("u1", "Emma", "sales", True, (now - timedelta(seconds=30)).isoformat()),
            _row("u2", "Dan", "sales", False, (now - timedelta(seconds=30)).isoformat()),
            _row("u3", "Maria", "support", True, (now - timedelta(hours=3)).isoformat()),
            _row("u4", "Lee", "supervisor", False, None),
        ]
        with patch("app.api.users.db.get_client", return_value=_client_with(rows)), \
             patch("app.api.users.db._run_sync", side_effect=_fake_run_sync):
            res = await get_live_agents(user={"role": "supervisor"})

        assert res["success"] is True
        assert res["count"] == 4
        by_id = {a["id"]: a for a in res["data"]}
        assert set(by_id["u1"]) == {"id", "name", "role", "is_ready", "is_online", "last_seen"}
        assert by_id["u1"]["is_online"] and by_id["u1"]["is_ready"]      # 🟢 ready
        assert by_id["u2"]["is_online"] and not by_id["u2"]["is_ready"]  # ⚪ online-not-ready
        assert not by_id["u3"]["is_online"]                              # stale heartbeat
        assert not by_id["u4"]["is_online"] and by_id["u4"]["last_seen"] is None

    @pytest.mark.asyncio
    async def test_operators_can_see_the_queue(self):
        # The whole point: agents stop emailing the owner to ask.
        with patch("app.api.users.db.get_client", return_value=_client_with([])), \
             patch("app.api.users.db._run_sync", side_effect=_fake_run_sync):
            res = await get_live_agents(user={"role": "sales"})
        assert res["success"] is True

    @pytest.mark.asyncio
    async def test_unknown_role_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await get_live_agents(user={"role": "qa"})
        assert exc.value.status_code == 403
