"""The panel listens instead of asking.

Three polls per open chat — typing every 500ms, messages and presence every 2s
— asked about events the server already knew the moment they happened. One
reason the panel could not just subscribe: the SSE manager kept ONE queue per
conversation and connect() overwrote it, so the moment the panel subscribed the
visitor's widget silently lost its stream. These tests pin the fan-out, the
authorisation, and the two writes that now also travel as events.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import agent as agent_api  # noqa: E402
from app.realtime.manager import ConnectionManager  # noqa: E402

_CONV = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_SALES = {"id": "u1", "role": "sales", "tunnel_scope": "sales"}
_SUPERVISOR = {"id": "u2", "role": "supervisor"}


# ══════════════════════════════════════════════════════════════
# Fan-out: the widget and the panel are both real subscribers
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_two_subscribers_both_receive():
    m = ConnectionManager()
    widget = await m.connect(_CONV)
    panel = await m.connect(_CONV)
    assert m.subscriber_count(_CONV) == 2

    await m.push(_CONV, {"id": "m1", "content": "hello"})

    assert widget.get_nowait()["id"] == "m1"
    assert panel.get_nowait()["id"] == "m1", "the panel must not steal the widget's stream"


@pytest.mark.asyncio
async def test_one_subscriber_leaving_does_not_take_the_other_with_it():
    m = ConnectionManager()
    q1 = await m.connect(_CONV)
    q2 = await m.connect(_CONV)

    m.disconnect(_CONV, q1)
    assert m.subscriber_count(_CONV) == 1

    await m.push(_CONV, {"id": "m2"})
    assert q2.get_nowait()["id"] == "m2"
    assert q1.empty()

    m.disconnect(_CONV, q2)
    assert m.subscriber_count(_CONV) == 0
    assert m.subscriber_count() == 0


@pytest.mark.asyncio
async def test_disconnecting_an_unknown_queue_is_harmless():
    m = ConnectionManager()
    q = await m.connect(_CONV)
    m.disconnect("some-other-conv", q)
    m.disconnect(_CONV, q)
    m.disconnect(_CONV, q)  # twice: a double cleanup must not raise
    assert m.subscriber_count() == 0


@pytest.mark.asyncio
async def test_push_with_no_subscribers_is_a_noop():
    m = ConnectionManager()
    await m.push(_CONV, {"id": "m3"})
    await m.push_chunk(_CONV, "hi")
    await m.push_stream_end(_CONV, {"id": "m3"})
    assert m.subscriber_count() == 0


@pytest.mark.asyncio
async def test_a_full_queue_never_blocks_the_other_subscriber():
    m = ConnectionManager()
    slow = await m.connect(_CONV)
    fast = await m.connect(_CONV)
    while not slow.full():
        slow.put_nowait({"filler": True})

    await m.push(_CONV, {"id": "late"})
    # The fast subscriber still got it; the slow one simply dropped it.
    assert fast.get_nowait()["id"] == "late"


@pytest.mark.asyncio
async def test_stream_end_carries_the_event_name():
    m = ConnectionManager()
    q = await m.connect(_CONV)
    await m.push_stream_end(_CONV, {"id": "m4", "content": "done"})
    out = q.get_nowait()
    assert out["event"] == "stream_end"
    assert out["id"] == "m4"


# ══════════════════════════════════════════════════════════════
# The widget stream unsubscribes itself (no leak)
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_widget_stream_releases_its_own_subscription():
    from app.api.chat import sse_stream
    from app.realtime.manager import manager

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=True)

    before = manager.subscriber_count()
    res = await sse_stream(conversation_id=_CONV, request=request, _owner=None)
    async for _chunk in res.body_iterator:
        pass
    assert manager.subscriber_count() == before
    assert manager.subscriber_count(_CONV) == 0


# ══════════════════════════════════════════════════════════════
# /agent/stream: same gate as GET /conversations/{id}
# ══════════════════════════════════════════════════════════════

async def _stream(user, conv):
    from fastapi import HTTPException

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=True)
    with patch.object(agent_api.db, "get_conversation_simple",
                      new=AsyncMock(return_value=conv)):
        try:
            return await agent_api.agent_stream(_CONV, request, user=user), None
        except HTTPException as e:
            return None, e


@pytest.mark.asyncio
async def test_missing_conversation_is_404():
    res, err = await _stream(_SALES, None)
    assert res is None and err.status_code == 404


@pytest.mark.asyncio
async def test_the_wrong_tunnel_is_refused():
    res, err = await _stream(_SALES, {"id": _CONV, "tunnel": "support"})
    assert res is None and err.status_code == 403


@pytest.mark.asyncio
async def test_a_supervisor_outside_the_team_gets_nothing():
    """Events carry message text — an out-of-team supervisor gets 403, not a
    metadata-only stream."""
    conv = {"id": _CONV, "tunnel": "sales", "team_id": "team-b"}
    with patch("app.api.conversations._resolve_team_scope",
               new=AsyncMock(return_value=["team-a"])):
        res, err = await _stream(_SUPERVISOR, conv)
    assert res is None and err.status_code == 403


@pytest.mark.asyncio
async def test_a_supervisor_with_no_team_fails_closed():
    conv = {"id": _CONV, "tunnel": "sales", "team_id": "team-a"}
    with patch("app.api.conversations._resolve_team_scope",
               new=AsyncMock(return_value=[])):
        res, err = await _stream(_SUPERVISOR, conv)
    assert res is None and err.status_code == 403


@pytest.mark.asyncio
async def test_a_supervisor_inside_the_team_gets_the_stream():
    from app.realtime.manager import manager

    conv = {"id": _CONV, "tunnel": "sales", "team_id": "team-a"}
    with patch("app.api.conversations._resolve_team_scope",
               new=AsyncMock(return_value=["team-a"])):
        res, err = await _stream(_SUPERVISOR, conv)
    assert err is None
    assert res.media_type == "text/event-stream"
    assert res.headers["x-accel-buffering"] == "no"
    async for _chunk in res.body_iterator:
        pass
    assert manager.subscriber_count(_CONV) == 0, "the panel must release its queue too"


@pytest.mark.asyncio
async def test_the_operator_on_their_own_tunnel_gets_the_stream():
    res, err = await _stream(_SALES, {"id": _CONV, "tunnel": "sales"})
    assert err is None
    async for _chunk in res.body_iterator:
        pass


# ══════════════════════════════════════════════════════════════
# Typing and presence writes also travel as events
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_typing_write_fans_out_as_an_event():
    from app.api.chat import set_typing_status, TypingBody

    typing = MagicMock()
    typing.set_typing = AsyncMock()
    typing.clear_typing = AsyncMock()
    with (
        patch("app.realtime.typing_indicator.typing_manager", typing),
        patch("app.realtime.manager.manager.push", new=AsyncMock()) as push,
    ):
        await set_typing_status(_CONV, TypingBody(text="looking for LHR"), _owner=None)
        assert push.await_args.args[1] == {
            "event": "typing", "is_typing": True, "text": "looking for LHR",
        }

        await set_typing_status(_CONV, TypingBody(text="  "), _owner=None)
        assert push.await_args.args[1]["is_typing"] is False


@pytest.mark.asyncio
async def test_presence_ping_fans_out_in_the_shape_the_panel_already_reads():
    from app.api.chat import ping_chat_session

    with (
        patch("app.db.supabase.patch_conversation_presence",
              new=AsyncMock(return_value=True)),
        patch("app.realtime.manager.manager.push", new=AsyncMock()) as push,
    ):
        out = await ping_chat_session(_CONV, _owner=None)

    assert out["success"] is True
    payload = push.await_args.args[1]
    assert payload["event"] == "presence"
    # Same keys GET /conversations/{id}/presence returns, so the panel can
    # setQueryData(['presence', id]) with it.
    for key in ("widget_open", "widget_presence", "widget_presence_effective",
                "widget_presence_age_seconds", "widget_last_event_at"):
        assert key in payload


@pytest.mark.asyncio
async def test_a_failed_presence_write_pushes_nothing():
    """A push that says 'online' when the write failed is the panel lying."""
    from app.api.chat import ping_chat_session

    with (
        patch("app.db.supabase.patch_conversation_presence",
              new=AsyncMock(return_value=False)),
        patch("app.realtime.manager.manager.push", new=AsyncMock()) as push,
    ):
        out = await ping_chat_session(_CONV, _owner=None)

    assert out["success"] is False
    push.assert_not_awaited()


def test_health_reports_live_subscribers():
    from app.realtime.manager import manager

    assert isinstance(manager.subscriber_count(), int)
