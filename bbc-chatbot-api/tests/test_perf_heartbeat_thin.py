"""The heartbeat stops sweeping, and the sweep stops asking per row.

31 Aug: three operators lost the panel at once. The heartbeat ran the
stale+deadline sweep from EVERY agent every 5s, and the deadline check issued
one query per active human conversation before checking any deadline — so the
cost was O(agents × open chats) and every chat left open multiplied the load
for everyone. These tests pin the shape that cannot come back:

  * the heartbeat writes presence (plus what the operator is viewing) and
    nothing else;
  * the deadline loop issues ZERO queries;
  * the sweep runs once and does both passes;
  * an SSL EOF is a retryable disconnect, and the retry no longer throws away
    the global client (which orphaned socket pools instead of helping);
  * /health can see saturation coming, and the alert fires before the panel
    breaks.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import agent as agent_api  # noqa: E402
from app.db import supabase as db  # noqa: E402

_CONV = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_AGENT = "agent-1"
_USER = {"id": _AGENT, "email": "op@bbc.test", "role": "sales"}
_SSL_EOF = "EOF occurred in violation of protocol (_ssl.c:2427)"


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


@pytest.fixture(autouse=True)
def _reset_counters():
    """DB_HEALTH is a module global — a leaked count from one test is a lie in
    the next."""
    db.DB_HEALTH.update(
        {"in_flight": 0, "peak_in_flight": 0, "calls": 0, "errors": 0,
         "disconnects": 0, "retries_ok": 0, "retries_failed": 0}
    )
    db.DB_HEALTH["latency_ms"].clear()
    db.DB_HEALTH["by_label"].clear()
    agent_api.HEARTBEAT_HEALTH["calls"] = 0
    agent_api.HEARTBEAT_HEALTH["latency_ms"].clear()
    agent_api.DEADLINE_HEALTH.update(
        {"skipped_no_assigned_at": 0, "skipped_bad_timestamp": 0}
    )
    yield


# ══════════════════════════════════════════════════════════════
# 1-2 — the heartbeat only writes presence
# ══════════════════════════════════════════════════════════════

def _heartbeat_patches(last_seen_mock, sweep_mock):
    return [
        patch.object(agent_api.db, "update_user_last_seen", new=last_seen_mock),
        patch("app.services.presence.record_presence_tick", new=AsyncMock()),
        patch.object(agent_api, "_cleanup_stale_conversations", new=sweep_mock),
        patch.object(agent_api.db, "get_user_by_id",
                     new=AsyncMock(return_value={**_USER, "is_ready": False,
                                                 "tunnel_scope": "sales",
                                                 "is_active": True,
                                                 "chat_enabled": True})),
        patch.object(agent_api.db, "get_queue_for_operator",
                     new=AsyncMock(return_value=[])),
    ]


async def _beat(body=None):
    from contextlib import ExitStack

    last_seen = AsyncMock()
    sweep = AsyncMock(return_value=7)
    with ExitStack() as stack:
        for p in _heartbeat_patches(last_seen, sweep):
            stack.enter_context(p)
        out = await agent_api.heartbeat(
            body=body or agent_api.HeartbeatBody(), user=dict(_USER)
        )
    return out, last_seen, sweep


@pytest.mark.asyncio
async def test_heartbeat_no_longer_sweeps():
    out, _last_seen, sweep = await _beat()
    sweep.assert_not_awaited()
    assert out["cleaned"] == 0
    assert out["success"] is True


@pytest.mark.asyncio
async def test_heartbeat_records_what_the_operator_is_viewing():
    out, last_seen, _sweep = await _beat(
        agent_api.HeartbeatBody(viewing_conversation_id=_CONV)
    )
    assert out["success"] is True
    assert last_seen.await_args.kwargs["viewing_conversation_id"] == _CONV

    _out, last_seen_none, _s = await _beat()
    assert last_seen_none.await_args.kwargs["viewing_conversation_id"] is None


@pytest.mark.asyncio
async def test_heartbeat_latency_is_measured():
    await _beat()
    snap = agent_api.heartbeat_health_snapshot()
    assert snap["calls"] == 1
    assert snap["p95_ms"] >= 0.0


# ══════════════════════════════════════════════════════════════
# 3-6 — the deadline loop asks nothing
# ══════════════════════════════════════════════════════════════

def _conv(conv_id, assigned_ago, last_agent=None, users=None):
    row = {
        "id": conv_id,
        "assigned_agent_id": _AGENT,
        "metadata": {"agent_assigned_at": _iso(assigned_ago)},
        "last_agent_message_at": last_agent,
    }
    if users is not None:
        row["users"] = users
    return row


async def _deadline(convs, **kwargs):
    with (
        patch.object(agent_api.db, "get_active_human_conversations",
                     new=AsyncMock(return_value=convs)),
        patch.object(agent_api.db, "has_agent_message_since",
                     new=AsyncMock(return_value=False)) as per_row,
        patch("app.services.handoff.fall_back_to_ai", new=AsyncMock()) as fb,
    ):
        count = await agent_api._enforce_response_deadline(**kwargs)
    return count, per_row, fb


@pytest.mark.asyncio
async def test_deadline_issues_zero_queries_in_the_loop():
    convs = [_conv(f"conv-{i}", 95) for i in range(5)]
    count, per_row, fb = await _deadline(convs)
    per_row.assert_not_awaited()          # the O(agents × chats) query is gone
    assert count == 5
    assert fb.await_count == 5


@pytest.mark.asyncio
async def test_reassignment_to_b_ignores_as_earlier_reply():
    """A replied at T-10s, then the conversation went to B at T. B is silent."""
    count, _per_row, fb = await _deadline([_conv(_CONV, 95, last_agent=_iso(105))])
    assert count == 1
    fb.assert_awaited_once_with(_CONV)


@pytest.mark.asyncio
async def test_a_reply_after_assignment_is_engagement():
    count, _per_row, fb = await _deadline([_conv(_CONV, 95, last_agent=_iso(90))])
    assert count == 0
    fb.assert_not_awaited()


@pytest.mark.asyncio
async def test_viewing_extension_from_the_joined_user_row():
    viewing = {"last_seen_at": _iso(3), "viewing_conversation_id": _CONV}
    count, _p, fb = await _deadline([_conv(_CONV, 95, users=viewing)])
    assert count == 0, "95s < the 120s viewing grace"
    fb.assert_not_awaited()

    count, _p, fb = await _deadline([_conv(_CONV, 130, users=viewing)])
    assert count == 1, "past 120s even a watched conversation falls back"


@pytest.mark.asyncio
async def test_a_closed_tab_cannot_shield_a_conversation():
    stale = {"last_seen_at": _iso(60), "viewing_conversation_id": _CONV}
    count, _p, fb = await _deadline([_conv(_CONV, 95, users=stale)])
    assert count == 1
    fb.assert_awaited_once_with(_CONV)


@pytest.mark.asyncio
async def test_explicit_params_still_win():
    count, _p, fb = await _deadline(
        [_conv(_CONV, 95)],
        viewing_conversation_id=_CONV,
        viewing_user_id=_AGENT,
    )
    assert count == 0
    fb.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_postgrest_list_join_is_read_too():
    """PostgREST returns the embedded row as a list in some shapes."""
    viewing = [{"last_seen_at": _iso(2), "viewing_conversation_id": _CONV}]
    count, _p, _fb = await _deadline([_conv(_CONV, 95, users=viewing)])
    assert count == 0


# ══════════════════════════════════════════════════════════════
# 7 — one sweep, both passes
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sweep_runs_both_passes_exactly_once():
    from app.api.cron import run_agent_sweep

    with (
        patch.object(agent_api.db, "get_stale_agent_conversations",
                     new=AsyncMock(return_value=[])) as stale,
        patch.object(agent_api.db, "get_active_human_conversations",
                     new=AsyncMock(return_value=[])) as active,
        patch("app.services.handoff.fall_back_to_ai", new=AsyncMock()),
    ):
        out = await run_agent_sweep()

    assert out["success"] is True
    assert stale.await_count == 1
    assert active.await_count == 1


# ══════════════════════════════════════════════════════════════
# 8-10 — the retry list, the client, the counters
# ══════════════════════════════════════════════════════════════

def test_ssl_eof_is_a_disconnect():
    import ssl

    assert db._is_disconnect(ssl.SSLEOFError(_SSL_EOF))
    assert db._is_disconnect(RuntimeError(_SSL_EOF))
    assert not db._is_disconnect(ValueError("column x does not exist"))


@pytest.mark.asyncio
async def test_ssl_eof_is_retried_and_counted():
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_SSL_EOF)
        return "ok"

    assert await db._run_sync(_flaky, label="messages") == "ok"
    assert db.DB_HEALTH["disconnects"] == 1
    assert db.DB_HEALTH["retries_ok"] == 1
    assert db.DB_HEALTH["retries_failed"] == 0


@pytest.mark.asyncio
async def test_an_insert_is_never_retried():
    def _boom():
        raise RuntimeError(_SSL_EOF)

    with pytest.raises(RuntimeError):
        await db._run_sync(_boom, False, label="messages")
    assert db.DB_HEALTH["retries_ok"] == 0
    assert db.DB_HEALTH["errors"] == 1


@pytest.mark.asyncio
async def test_a_disconnect_storm_does_not_rebuild_the_client():
    def _boom():
        raise RuntimeError(_SSL_EOF)

    created = {"n": 0}

    def _fake_create(*_a, **_k):
        created["n"] += 1
        return MagicMock()

    saved = db._client
    db._client = None
    try:
        with patch.object(db, "create_client", side_effect=_fake_create):
            db.get_client()
            for _ in range(100):
                with pytest.raises(RuntimeError):
                    await db._run_sync(_boom, label="messages")
                db.get_client()
    finally:
        db._client = saved

    assert created["n"] == 1, "the old reset orphaned one socket pool per storm"
    assert db.DB_HEALTH["retries_failed"] == 100


@pytest.mark.asyncio
async def test_in_flight_unwinds_and_labels_count():
    def _boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await db._run_sync(_boom, label="heartbeat")
    await db._run_sync(lambda: "ok", label="heartbeat")

    assert db.DB_HEALTH["in_flight"] == 0
    assert len(db.DB_HEALTH["latency_ms"]) == 2
    assert db.DB_HEALTH["by_label"]["heartbeat"] == {"calls": 2, "errors": 1}


def test_snapshot_shape_and_worker_size():
    from config.settings import settings

    snap = db.db_health_snapshot()
    for key in ("in_flight", "peak_in_flight", "workers", "p50_ms", "p95_ms",
                "disconnects", "retries_ok", "retries_failed", "by_label"):
        assert key in snap
    assert snap["workers"] == settings.db_executor_workers


# ══════════════════════════════════════════════════════════════
# 11 — the alert shouts before the panel breaks
# ══════════════════════════════════════════════════════════════

def _reset_db_alert():
    from app.api import cron
    cron._DB_ALERT.update({"last_sent_at": None, "over_streak": 0, "episode_open": False})


@pytest.mark.asyncio
async def test_saturation_alerts_once_then_suppresses():
    from app.api import cron
    from config.settings import settings

    _reset_db_alert()
    with patch("app.services.email.send_ops_alert_email",
               new=AsyncMock(return_value=True)) as mail:
        states = []
        for _ in range(3):
            db.DB_HEALTH["peak_in_flight"] = settings.db_executor_workers - 2
            db.DB_HEALTH["in_flight"] = 3
            states.append((await cron.run_db_saturation_alert())["state"])
        assert states[:2] == ["watching", "watching"]
        assert states[2] == "alerted"
        assert mail.await_count == 1
        assert db.DB_HEALTH["peak_in_flight"] == 3

        db.DB_HEALTH["peak_in_flight"] = settings.db_executor_workers - 2
        second = await cron.run_db_saturation_alert()
        assert second["state"] == "suppressed"
        assert mail.await_count == 1

    _reset_db_alert()


@pytest.mark.asyncio
async def test_a_quiet_executor_says_ok():
    from app.api import cron

    _reset_db_alert()
    db.DB_HEALTH["peak_in_flight"] = 2
    with patch("app.services.email.send_ops_alert_email",
               new=AsyncMock(return_value=True)) as mail:
        out = await cron.run_db_saturation_alert()
    assert out["state"] == "ok"
    mail.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_slow_heartbeat_alone_is_enough():
    from app.api import cron

    _reset_db_alert()
    db.DB_HEALTH["peak_in_flight"] = 1
    agent_api.HEARTBEAT_HEALTH["latency_ms"].extend([4000.0] * 20)
    with patch("app.services.email.send_ops_alert_email",
               new=AsyncMock(return_value=True)) as mail:
        states = []
        for _ in range(3):
            db.DB_HEALTH["peak_in_flight"] = 1
            states.append((await cron.run_db_saturation_alert())["state"])
    assert states[:2] == ["watching", "watching"]
    assert states[2] == "alerted"
    assert mail.await_count == 1
    assert "heartbeat slow" in mail.await_args.kwargs["subject"]
    assert "[sustained 3×60s]" in mail.await_args.kwargs["subject"]
    _reset_db_alert()


# ══════════════════════════════════════════════════════════════
# 035 — an unapplied migration must not break the heartbeat
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_row_we_cannot_judge_is_counted_not_swallowed():
    """No agent_assigned_at: the stale pass only sees OFFLINE agents, so this
    row hangs on an online one. Skipping is safe; being silent is not."""
    conv = {"id": _CONV, "assigned_agent_id": _AGENT, "metadata": {}}
    count, _p, fb = await _deadline([conv])
    assert count == 0
    fb.assert_not_awaited()
    assert agent_api.DEADLINE_HEALTH["skipped_no_assigned_at"] == 1
    assert agent_api.DEADLINE_HEALTH["skipped_bad_timestamp"] == 0


@pytest.mark.asyncio
async def test_an_unreadable_timestamp_is_counted_separately():
    conv = {
        "id": _CONV,
        "assigned_agent_id": _AGENT,
        "metadata": {"agent_assigned_at": "not-a-date"},
    }
    count, _p, fb = await _deadline([conv])
    assert count == 0
    fb.assert_not_awaited()
    assert agent_api.DEADLINE_HEALTH["skipped_bad_timestamp"] == 1
    assert agent_api.DEADLINE_HEALTH["skipped_no_assigned_at"] == 1


@pytest.mark.asyncio
async def test_a_readable_row_never_touches_the_skip_counters():
    await _deadline([_conv(_CONV, 95)])
    assert agent_api.DEADLINE_HEALTH == {
        "skipped_no_assigned_at": 0,
        "skipped_bad_timestamp": 0,
    }


# ══════════════════════════════════════════════════════════════
# Settings floors — a bad env must fail loudly, not at import
# ══════════════════════════════════════════════════════════════

def _settings_with(**env):
    from config.settings import Settings

    return Settings(**env)


def test_zero_workers_is_refused_before_it_can_kill_the_process():
    from pydantic import ValidationError

    # ThreadPoolExecutor(max_workers=0) raises at IMPORT of app/db/supabase.py.
    with pytest.raises(ValidationError):
        _settings_with(db_executor_workers=0)
    with pytest.raises(ValidationError):
        _settings_with(db_executor_workers=3)
    assert _settings_with(db_executor_workers=4).db_executor_workers == 4


def test_a_sweep_interval_below_the_floor_is_refused():
    from pydantic import ValidationError

    # 0 would be a hot loop against the database — the thing this branch fixes.
    with pytest.raises(ValidationError):
        _settings_with(agent_sweep_interval_seconds=0)
    with pytest.raises(ValidationError):
        _settings_with(agent_sweep_interval_seconds=1)
    assert _settings_with(agent_sweep_interval_seconds=5).agent_sweep_interval_seconds == 5


# ══════════════════════════════════════════════════════════════
# The alert says what it is
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_alert_does_not_arrive_as_a_waiting_chat():
    from app.api import cron
    from config.settings import settings

    _reset_db_alert()
    with (
        patch("app.services.email.send_ops_alert_email",
              new=AsyncMock(return_value=True)) as ops,
        patch("app.services.email.send_super_alert_email",
              new=AsyncMock()) as customer,
    ):
        out = None
        for _ in range(3):
            db.DB_HEALTH["peak_in_flight"] = settings.db_executor_workers - 2
            out = await cron.run_db_saturation_alert()

    assert out["state"] == "alerted"
    customer.assert_not_awaited(), "an infra alert must not borrow a chat subject"
    subject = ops.await_args.kwargs["subject"]
    assert "DB saturation" in subject
    assert "executor saturated" in subject
    assert "[sustained 3×60s]" in subject
    assert "no agents online" not in subject
    assert "by_label" in ops.await_args.kwargs["body"]
    assert ops.await_args.kwargs["cc_super"] is True
    _reset_db_alert()


@pytest.mark.asyncio
async def test_a_quiet_window_closes_the_episode():
    from app.api import cron
    from config.settings import settings

    _reset_db_alert()
    agent_api.HEARTBEAT_HEALTH["latency_ms"].clear()
    with patch("app.services.email.send_ops_alert_email",
               new=AsyncMock(return_value=True)):
        for _ in range(3):
            db.DB_HEALTH["peak_in_flight"] = settings.db_executor_workers - 2
            db.DB_HEALTH["in_flight"] = 3
            await cron.run_db_saturation_alert()
        db.DB_HEALTH["peak_in_flight"] = 1
        db.DB_HEALTH["in_flight"] = 1
        out = await cron.run_db_saturation_alert()
    assert out["state"] == "ok"
    assert cron._DB_ALERT["over_streak"] == 0
    assert cron._DB_ALERT["episode_open"] is False
    _reset_db_alert()


@pytest.mark.asyncio
async def test_ops_alert_to_ops_address_ccs_super_only_on_first():
    from app.services import email
    from config.settings import settings

    captured = []

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def post(self, url, headers=None, json=None):
            captured.append(json)
            return _Resp()

    with (
        patch("app.services.email.httpx.AsyncClient", lambda **k: _Client()),
        patch.object(email.settings, "postmark_token", "tok"),
        patch.object(email.settings, "ops_alert_email", "it@x"),
    ):
        await email.send_ops_alert_email("s", "b", cc_super=True)
        await email.send_ops_alert_email("s", "b", cc_super=False)
    assert captured[0]["To"] == "it@x"
    assert captured[0]["Cc"] == settings.super_alert_email
    assert captured[1]["To"] == "it@x"
    assert "Cc" not in captured[1]

    captured.clear()
    with (
        patch("app.services.email.httpx.AsyncClient", lambda **k: _Client()),
        patch.object(email.settings, "postmark_token", "tok"),
        patch.object(email.settings, "ops_alert_email", ""),
    ):
        await email.send_ops_alert_email("s", "b", cc_super=True)
    assert captured[0]["To"] == settings.super_alert_email
    assert "Cc" not in captured[0]


@pytest.mark.asyncio
async def test_ops_alert_reuses_the_super_transport():
    """One transport to super@ — two copies of the credentials is how they
    drift apart."""
    from app.services import email

    with patch.object(email, "_send_super_inbox_email",
                      new=AsyncMock(return_value=True)) as send:
        assert await email.send_ops_alert_email("subj", "line one\nline two")

    kwargs = send.await_args.kwargs
    assert kwargs["subject"] == "subj"
    assert kwargs["text_body"] == "line one\nline two"


@pytest.mark.asyncio
async def test_ops_alert_body_is_escaped_in_html():
    from app.services import email

    with patch.object(email, "_send_super_inbox_email",
                      new=AsyncMock(return_value=True)) as send:
        await email.send_ops_alert_email("s", "by_label={'a': '<b>'}")

    assert "<b>" not in send.await_args.kwargs["html_body"]
    assert "&lt;b&gt;" in send.await_args.kwargs["html_body"]


@pytest.mark.asyncio
async def test_presence_write_survives_an_unapplied_035():
    payloads = []

    class _Tbl:
        def update(self, payload):
            payloads.append(payload)
            self._payload = payload
            return self

        def eq(self, *_a):
            return self

        def execute(self):
            if "viewing_conversation_id" in self._payload:
                raise RuntimeError(
                    'column users.viewing_conversation_id does not exist'
                )
            return MagicMock(data=[{"id": _AGENT}])

    client = MagicMock()
    client.table.return_value = _Tbl()
    with (
        patch.object(db, "get_client", return_value=client),
        patch.object(db, "_run_sync",
                     new=AsyncMock(side_effect=lambda fn, *a, **k: fn())),
    ):
        db._VIEWING_COLUMN_WARNED = False
        await db.update_user_last_seen(_AGENT, viewing_conversation_id=_CONV)

    assert len(payloads) == 2
    assert "viewing_conversation_id" in payloads[0]
    assert "viewing_conversation_id" not in payloads[1]
    assert "last_seen_at" in payloads[1]
