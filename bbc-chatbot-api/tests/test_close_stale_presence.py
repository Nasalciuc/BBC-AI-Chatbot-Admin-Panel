"""The "client left the site" sweep closes a chat the way every close does.

11 Nov 2026, production: 204 of the 1,154 chats closed in ten weeks (18%)
had status "closed" and no closed_at, 179 of them with the client gone from
the site. Every other close writes both; this job wrote the status alone. The
panel then showed "This chat is closed." with no time, and anything that reads
closed_at skipped those chats.

The same job wrote the client's name in its INFO line (rule 7: no names at
INFO or above), and counted a chat as closed without looking at whether the
write happened.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron  # noqa: E402

_C1 = "11111111-aaaa-bbbb-cccc-000000000001"
_C2 = "11111111-aaaa-bbbb-cccc-000000000002"
_NAME = "Annabelle Example"


def _left(cid: str) -> dict:
    return {"id": cid, "visitor_name": _NAME, "metadata": {"widget_presence": "left"}}


@pytest.mark.asyncio
async def test_the_sweep_writes_when_the_chat_closed():
    update = AsyncMock(return_value={"id": _C1, "status": "closed"})
    before = datetime.now(timezone.utc)
    with patch.object(cron.db, "get_stale_presence_left_conversations", AsyncMock(return_value=[_left(_C1)])), \
            patch.object(cron.db, "update_conversation", update):
        out = await cron.run_close_stale_presence()
    after = datetime.now(timezone.utc)

    assert out == {"closed": 1}
    update.assert_awaited_once()
    cid, payload = update.await_args.args
    assert cid == _C1
    assert payload["status"] == "closed"
    # A real instant, with its zone, taken while the job ran.
    stamp = datetime.fromisoformat(payload["closed_at"])
    assert stamp.tzinfo is not None
    assert before - timedelta(seconds=1) <= stamp <= after + timedelta(seconds=1)
    # Nothing else is touched: the agent and the mode stay as they were, as before.
    assert set(payload) == {"status", "closed_at"}


@pytest.mark.asyncio
async def test_the_log_line_names_the_chat_not_the_client(caplog):
    with patch.object(cron.db, "get_stale_presence_left_conversations", AsyncMock(return_value=[_left(_C1)])), \
            patch.object(cron.db, "update_conversation", AsyncMock(return_value={"id": _C1})), \
            caplog.at_level(logging.DEBUG):
        await cron.run_close_stale_presence()

    lines = [r.getMessage() for r in caplog.records]
    assert any(_C1 in line and "Auto-closed" in line for line in lines)
    for line in lines:
        assert _NAME not in line and "Annabelle" not in line, line


@pytest.mark.asyncio
async def test_a_write_that_did_not_happen_is_not_a_closed_chat(caplog):
    """update_conversation says None when the write failed or matched nothing.
    The first chat is still open; the second one is closed and counted."""
    update = AsyncMock(side_effect=[None, {"id": _C2, "status": "closed"}])
    with patch.object(cron.db, "get_stale_presence_left_conversations", AsyncMock(return_value=[_left(_C1), _left(_C2)])), \
            patch.object(cron.db, "update_conversation", update), \
            caplog.at_level(logging.DEBUG):
        out = await cron.run_close_stale_presence()

    assert out == {"closed": 1}
    assert update.await_count == 2
    said_closed = [r.getMessage() for r in caplog.records if "Auto-closed" in r.getMessage()]
    assert len(said_closed) == 1 and _C2 in said_closed[0]
    not_closed = [r for r in caplog.records if _C1 in r.getMessage()]
    assert [r.levelno for r in not_closed] == [logging.WARNING]
    assert "still open" in not_closed[0].getMessage()


@pytest.mark.asyncio
async def test_a_write_that_raises_is_logged_with_its_reason_and_the_rest_go_on(caplog):
    update = AsyncMock(side_effect=[RuntimeError("connection reset"), {"id": _C2, "status": "closed"}])
    with patch.object(cron.db, "get_stale_presence_left_conversations", AsyncMock(return_value=[_left(_C1), _left(_C2)])), \
            patch.object(cron.db, "update_conversation", update), \
            caplog.at_level(logging.DEBUG):
        out = await cron.run_close_stale_presence()

    assert out == {"closed": 1}
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and _C1 in errors[0] and "connection reset" in errors[0]


@pytest.mark.asyncio
async def test_nobody_left_nothing_written():
    update = AsyncMock()
    with patch.object(cron.db, "get_stale_presence_left_conversations", AsyncMock(return_value=[])), \
            patch.object(cron.db, "update_conversation", update):
        out = await cron.run_close_stale_presence()

    assert out == {"closed": 0}
    update.assert_not_awaited()


def test_every_close_in_the_backend_writes_closed_at():
    """Whoever adds a close that writes the status alone fails here."""
    import re

    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    bare = []
    for folder, _dirs, files in os.walk(app_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            # A dict written to the database with status closed …
            for m in re.finditer(r"""update_conversation\(\s*\w+,\s*\{[^{}]*["']status["']\s*:\s*["']closed["'][^{}]*\}""", src):
                if "closed_at" not in m.group(0):
                    bare.append(f"{os.path.relpath(path, app_dir)}: {m.group(0)[:80]}")
    assert bare == [], bare
