"""Conversations list payload + honest detail errors.

Measured on production before this change: the list selected "*", shipping the
`metadata` jsonb (~816 B/row, two thirds of a row) that the list never renders —
61.9 kB per 50-row page vs 5.2 kB for the columns actually used. And the detail
endpoint answered HTTP 200 {data: null} for BOTH a missing row and a failed
fetch, so a transient Supabase blip told the operator a live conversation had
been deleted.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db
from app.security.auth import get_current_user

AGENT_ID = "11111111-1111-1111-1111-111111111111"
ENGAGED_ID = "22222222-2222-2222-2222-222222222222"
CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _FakeQuery:
    """Records the supabase-py chain and returns canned rows."""

    def __init__(self, rows=None, count=0):
        self.calls: list[tuple] = []
        self._rows = rows if rows is not None else []
        self._count = count

    def select(self, *a, **k):
        self.calls.append(("select", a, k))
        return self

    def order(self, *a, **k):
        return self

    def eq(self, col, val):
        return self

    def in_(self, col, vals):
        return self

    def is_(self, col, val):
        return self

    def or_(self, filters: str):
        return self

    def limit(self, n):
        return self

    def range(self, start, end):
        return self

    def execute(self):
        res = MagicMock()
        res.data = self._rows
        res.count = self._count
        return res

    @property
    def select_arg(self) -> str:
        return next(c[1][0] for c in self.calls if c[0] == "select")


def _client(role: str = "sales", tunnel_scope: str = "sales") -> TestClient:
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": AGENT_ID,
        "email": f"{role}@bbc.com",
        "name": "Op",
        "role": role,
        "tunnel_scope": tunnel_scope,
    }
    return TestClient(app)


# ── 1-3. The list selects only what it renders ───────────────────────


@pytest.mark.asyncio
async def test_list_does_not_select_star_or_metadata():
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock,
                     side_effect=lambda rows: rows),
    ):
        await db.get_conversations(limit=50, offset=0)

    select = fq.select_arg
    assert select != "*"
    # The whole jsonb blob must not be shipped; only the one lifted key.
    assert "metadata->>engaged_agent_id" in select
    assert ",metadata," not in f",{select},"


@pytest.mark.asyncio
async def test_list_selects_every_column_the_ui_renders():
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock,
                     side_effect=lambda rows: rows),
    ):
        await db.get_conversations()

    select = fq.select_arg
    for column in (
        "id", "tunnel", "status", "mode", "visitor_name", "message_count",
        "updated_at", "has_flagged_content", "flagged_reason", "assigned_agent_id",
    ):
        assert column in select, f"list UI renders {column}, but it is not selected"


@pytest.mark.asyncio
async def test_engaged_agent_id_is_reshaped_so_agent_state_still_works():
    """The fallback label ("<name> → AI") depends on metadata.engaged_agent_id."""
    rows = [{
        "id": CONV_ID,
        "mode": "ai",
        "assigned_agent_id": None,
        "engaged_agent_id": ENGAGED_ID,
    }]
    fq = _FakeQuery(rows=rows, count=1)
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "get_users_by_ids", new_callable=AsyncMock,
                     return_value={ENGAGED_ID: {"id": ENGAGED_ID, "name": "Roman"}}),
    ):
        out, total = await db.get_conversations()

    assert total == 1
    row = out[0]
    assert row["metadata"] == {"engaged_agent_id": ENGAGED_ID}
    # The lifted alias is not left lying around at the top level
    assert "engaged_agent_id" not in {k for k in row if k != "metadata"}
    assert row["agent_state"] == "fallback"
    assert row["engaged_agent_name"] == "Roman"


@pytest.mark.asyncio
async def test_missing_engaged_key_yields_ai_only_not_a_crash():
    rows = [{"id": CONV_ID, "mode": "ai", "assigned_agent_id": None,
             "engaged_agent_id": None}]
    fq = _FakeQuery(rows=rows, count=1)
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "get_users_by_ids", new_callable=AsyncMock, return_value={}),
    ):
        out, _ = await db.get_conversations()

    assert out[0]["agent_state"] == "ai_only"
    assert out[0]["engaged_agent_name"] is None


# ── 4-7. Missing row vs failed fetch ─────────────────────────────────


def test_missing_conversation_returns_404():
    from app.api import conversations as mod

    with patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                      return_value=None):
        resp = _client().get(f"/api/conversations/{CONV_ID}")

    assert resp.status_code == 404, resp.text


def test_backend_failure_returns_5xx_not_200_with_null():
    """The bug: a fetch failure used to look exactly like a deleted conversation."""
    from app.api import conversations as mod

    with patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                      side_effect=RuntimeError("supabase timeout")):
        resp = _client().get(f"/api/conversations/{CONV_ID}")

    assert resp.status_code >= 500, resp.text
    assert resp.status_code != 200


def test_detail_route_asks_for_strict_error_reporting():
    from app.api import conversations as mod

    with patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                      return_value=None) as fetch:
        _client().get(f"/api/conversations/{CONV_ID}")

    assert fetch.await_args.kwargs.get("raise_on_error") is True


def test_wrong_tunnel_still_403():
    """The tunnel guard must not be swallowed by the new error handling."""
    from app.api import conversations as mod

    conv = {"id": CONV_ID, "tunnel": "support", "status": "active", "messages": []}
    with patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                      return_value=conv):
        resp = _client(role="sales", tunnel_scope="sales").get(
            f"/api/conversations/{CONV_ID}"
        )

    assert resp.status_code == 403, resp.text


def test_successful_fetch_still_returns_the_envelope():
    from app.api import conversations as mod

    conv = {"id": CONV_ID, "tunnel": "sales", "status": "active", "messages": []}
    with patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                      return_value=conv):
        resp = _client().get(f"/api/conversations/{CONV_ID}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == CONV_ID


# ── 8-9. The db layer keeps its lenient default for existing callers ──


@pytest.mark.asyncio
async def test_get_conversation_swallows_errors_by_default():
    """chat.py / orchestrator callers rely on None rather than an exception."""
    with patch.object(db, "get_client", side_effect=RuntimeError("boom")):
        assert await db.get_conversation(CONV_ID) is None


@pytest.mark.asyncio
async def test_get_conversation_raises_when_asked():
    with patch.object(db, "get_client", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            await db.get_conversation(CONV_ID, raise_on_error=True)


@pytest.mark.asyncio
async def test_get_conversation_returns_none_for_empty_result_even_when_strict():
    """An absent row is an ordinary empty result, not an error."""
    fq = _FakeQuery(rows=[])
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with patch.object(db, "get_client", return_value=fake_db):
        assert await db.get_conversation(CONV_ID, raise_on_error=True) is None
