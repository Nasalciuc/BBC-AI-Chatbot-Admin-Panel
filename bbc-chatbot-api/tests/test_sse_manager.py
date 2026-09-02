"""SSE fan-out: terminal events must always land.

A 500-token reply on a slow client used to fill the subscriber queue with
chunks the panel does not render, then drop stream_end — the one event
carrying the complete message — with the stream still open, so polling
never stepped in. Chunks now stop at 80% of the queue.

set_typing published; clear and close did not. Both publish now.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.realtime.manager import ConnectionManager  # noqa: E402
from app.db.supabase import derive_effective_presence  # noqa: E402

_CONV = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _queue(m: ConnectionManager, maxsize: int = 10) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    m._subs.setdefault(_CONV, set()).add(q)
    return q


@pytest.mark.asyncio
async def test_chunks_stop_at_high_water_stream_end_still_lands():
    m = ConnectionManager()
    q = _queue(m, maxsize=10)
    for i in range(9):
        await m.push_chunk(_CONV, f"c{i}")
    # 0.8 * 10 = 8. The ninth chunk is refused so the terminal always fits.
    assert q.qsize() == 8
    await m.push_stream_end(_CONV, {"id": "end", "content": "done"})
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert sum(1 for e in events if e.get("event") == "stream_chunk") == 8
    assert events[-1]["event"] == "stream_end"
    assert events[-1]["id"] == "end"


@pytest.mark.asyncio
async def test_full_queue_drops_stream_end_with_warning_other_subscriber_gets_it(caplog):
    m = ConnectionManager()
    slow = _queue(m, maxsize=10)
    fast = _queue(m, maxsize=10)
    while not slow.full():
        slow.put_nowait({"filler": True})

    with caplog.at_level(logging.WARNING, logger="app.realtime.manager"):
        await m.push_stream_end(_CONV, {"id": "end"})

    assert "terminal event dropped" in caplog.text
    assert fast.get_nowait()["event"] == "stream_end"
    dumped = []
    while not slow.empty():
        dumped.append(slow.get_nowait())
    assert all(item.get("event") != "stream_end" for item in dumped)


@pytest.mark.asyncio
async def test_clear_typing_status_publishes_typing_false():
    from app.api.chat import clear_typing_status

    typing = MagicMock()
    typing.clear_typing = AsyncMock()
    with (
        patch("app.realtime.typing_indicator.typing_manager", typing),
        patch("app.realtime.manager.manager.push", new=AsyncMock()) as push,
    ):
        out = await clear_typing_status(_CONV, _owner=None)

    assert out["success"] is True
    typing.clear_typing.assert_awaited_once_with(_CONV)
    push.assert_awaited_once()
    assert push.await_args.args[1] == {
        "event": "typing", "is_typing": False, "text": "",
    }


@pytest.mark.asyncio
async def test_mark_chat_session_close_publishes_the_five_presence_keys_then_typing():
    from app.api.chat import mark_chat_session_close

    conv = {
        "id": _CONV,
        "last_user_message_at": None,
        "metadata": {
            "widget_open": True,
            "widget_presence": "online",
            "widget_pings": True,
        },
    }
    typing = MagicMock()
    typing.clear_typing = AsyncMock()
    with (
        patch("app.db.supabase.get_conversation", new=AsyncMock(return_value=conv)),
        patch("app.db.supabase.update_conversation_presence", new=AsyncMock()),
        patch("app.realtime.typing_indicator.typing_manager", typing),
        patch("app.realtime.manager.manager.push", new=AsyncMock()) as push,
    ):
        out = await mark_chat_session_close(_CONV, reason="left", _owner=None)

    assert out["success"] is True
    assert push.await_count == 2
    presence, typing_ev = push.await_args_list[0].args[1], push.await_args_list[1].args[1]
    assert presence["event"] == "presence"
    assert presence["widget_open"] is False
    assert presence["widget_presence"] == "left"
    assert "widget_presence_effective" in presence
    assert "widget_presence_age_seconds" in presence
    assert "widget_last_event_at" in presence
    assert presence["widget_last_close_reason"] == "left"
    expected_eff, expected_age = derive_effective_presence(
        {
            "widget_open": False,
            "widget_presence": "left",
            "widget_last_close_reason": "left",
            "widget_last_event_at": presence["widget_last_event_at"],
        },
        last_user_message_at=None,
    )
    assert presence["widget_presence_effective"] == expected_eff
    assert presence["widget_presence_age_seconds"] == expected_age
    assert typing_ev == {"event": "typing", "is_typing": False, "text": ""}


def test_no_drop_silently_anywhere_in_app():
    root = Path(__file__).resolve().parent.parent / "app"
    hits = [str(p.relative_to(root.parent)) for p in root.rglob("*.py") if "drop_silently" in p.read_text(encoding="utf-8")]
    assert hits == [], f"drop_silently must be gone: {hits}"
