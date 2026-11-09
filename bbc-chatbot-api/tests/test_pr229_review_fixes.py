"""PR-229 review fixes — behaviour traps, the guard report, retention."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.security import ban_guard, ban_store, behavior as bh, client_ip

CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    client_ip._reset_for_tests()
    bh._reset_for_tests()
    monkeypatch.setattr("app.services.email.send_ops_alert_email", AsyncMock())
    yield


def _scope(path, method="GET", headers=None, ip="203.0.113.9"):
    return {
        "type": "http", "path": path, "raw_path": path.encode(), "method": method,
        "client": (ip, 1), "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }


async def _run(mw, scope):
    async def send(_m):
        return None

    async def receive():
        return {"type": "http.request", "body": b""}

    await mw(scope, receive, send)


class _App:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


# ── the chat lock and an operator ─────────────────────────────

@pytest.mark.asyncio
async def test_a_locked_chat_an_operator_took_reaches_the_operator(monkeypatch):
    monkeypatch.setenv("CHAT_ATTACK_ENFORCE", "true")
    add = AsyncMock()
    with patch("app.services.conversation_service.add_message", new=add):
        reply = await bh.chat_door(conversation_id=CID, raw_message="hello?", clean_message="hello?",
                                   ip="1.2.3.4", visitor_id="v1", site="bbc",
                                   metadata={"locked": "abuse"}, human_handled=True)
    assert reply is None and add.await_count == 0
    assert bh.BEHAVIOR_HEALTH["chat_lock_refused_model"] == 0


@pytest.mark.asyncio
async def test_a_locked_chat_without_an_operator_is_still_refused(monkeypatch):
    monkeypatch.setenv("CHAT_ATTACK_ENFORCE", "true")
    with patch("app.services.conversation_service.add_message", new=AsyncMock()):
        reply = await bh.chat_door(conversation_id=CID, raw_message="hello?", clean_message="hello?",
                                   ip="1.2.3.4", visitor_id="v1", site="bbc",
                                   metadata={"locked": "abuse"}, human_handled=False)
    assert reply and bh.BEHAVIOR_HEALTH["chat_lock_refused_model"] == 1


# ── bans go through the guard's path (a row the Bans page shows) ─

@pytest.mark.asyncio
async def test_a_behaviour_ban_writes_its_row_once(monkeypatch):
    rows = AsyncMock()
    monkeypatch.setattr(ban_guard, "record_ban_row", rows)
    await bh._ban_ip("9.9.9.9", "attack_signature", 3600)
    await bh._ban_ip("9.9.9.9", "attack_signature", 3600)   # already held
    assert rows.await_count == 1
    assert rows.await_args.args[:2] == ("9.9.9.9", "attack_signature")
    assert await ban_store.get_store().is_banned("9.9.9.9")


@pytest.mark.asyncio
async def test_no_ip_no_ban():
    with patch.object(ban_guard, "record_ban_row", new=AsyncMock()) as rows:
        await bh._ban_ip("", "attack_signature", 3600)
    assert rows.await_count == 0


# ── operator traps: no email without a session, one per cooldown ─

@pytest.mark.asyncio
async def test_an_anonymous_ops_status_loop_sends_no_email(monkeypatch):
    alert = AsyncMock()
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    mw = ban_guard.BanGuardMiddleware(_App())
    for _ in range(20):
        await _run(mw, _scope("/api/agents/ops-status"))
    assert alert.await_count == 0
    assert bh.BEHAVIOR_HEALTH["operator_traps"]["ops_status"] == 20
    assert bh.BEHAVIOR_HEALTH["operator_traps_anonymous"] == 20


@pytest.mark.asyncio
async def test_a_staff_hit_alerts_once_per_cooldown(monkeypatch):
    import jwt as _jwt
    from config.settings import settings
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-behavior-0123456789abcdef")
    alert = AsyncMock()
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    tok = _jwt.encode({"sub": "op-7"}, settings.jwt_secret, algorithm="HS256")
    other = _jwt.encode({"sub": "op-8"}, settings.jwt_secret, algorithm="HS256")
    mw = ban_guard.BanGuardMiddleware(_App())
    for _ in range(5):
        await _run(mw, _scope("/api/agents/ops-status", headers={"authorization": f"Bearer {tok}"}))
    await _run(mw, _scope("/api/agents/ops-status", headers={"authorization": f"Bearer {other}"}))
    assert alert.await_count == 2        # op-7 once, op-8 once
    assert bh.BEHAVIOR_HEALTH["operator_alerts_suppressed"] == 4


# ── open streams leave the dict at zero ───────────────────────

def test_a_closed_stream_leaves_no_key():
    ban_guard._OPEN_STREAMS["5.5.5.5"] = 2
    ban_guard._stream_closed("5.5.5.5")
    assert ban_guard._OPEN_STREAMS["5.5.5.5"] == 1
    ban_guard._stream_closed("5.5.5.5")
    assert "5.5.5.5" not in ban_guard._OPEN_STREAMS
    ban_guard._stream_closed("6.6.6.6")   # never opened: nothing written
    assert "6.6.6.6" not in ban_guard._OPEN_STREAMS


# ── the guard report ──────────────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


@pytest.mark.asyncio
async def test_every_conversation_is_looked_at_in_chunks(monkeypatch):
    ids = [f"c-{i:04d}" for i in range(450)]
    seen_chunks: list[int] = []

    async def _fake_run(fn, **_kw):
        # The lambda's default arg is the chunk it asks for.
        chunk = fn.__defaults__[0]
        seen_chunks.append(len(chunk))
        return _Res([{"conversation_id": c, "origin_code": "JFK", "destination_code": "LHR"}
                     for c in chunk if c == "c-0300"])

    monkeypatch.setattr("app.db.supabase._run_sync", _fake_run)
    found = await bh._lead_has_trip(ids)
    assert found == {"c-0300"}            # past position 200
    assert seen_chunks == [200, 200, 50]


@pytest.mark.asyncio
async def test_each_ip_is_looked_up_once_from_its_first_hit(monkeypatch):
    look = AsyncMock(return_value=False)
    monkeypatch.setattr(bh, "_ip_went_on_normally", look)
    monkeypatch.setattr(bh, "_lead_has_trip", AsyncMock(return_value=set()))
    for _ in range(60):
        bh.record("scanner_ua", ip="7.7.7.7")
    bh.record("bad_method", ip="8.8.8.8")
    bh.SHADOW_HITS[0]["at"] -= 3600          # the first hit, an hour earlier
    rep = await bh.build_trap_report()
    assert look.await_count == 2              # one per IP, not one per minute
    first_since = [c.args[1] for c in look.await_args_list if c.args[0] == "7.7.7.7"][0]
    from datetime import datetime
    assert datetime.fromisoformat(first_since).timestamp() == pytest.approx(bh.SHADOW_HITS[0]["at"])
    assert rep["scanner_ua"]["verdict"] == "can be turned on"


@pytest.mark.asyncio
async def test_a_failed_lookup_is_unknown_never_turn_on(monkeypatch):
    monkeypatch.setattr(bh, "_ip_went_on_normally", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(bh, "_lead_has_trip", AsyncMock(side_effect=RuntimeError("db down")))
    bh.record("scanner_ua", ip="7.7.7.7")
    bh.record("chat_lock", conversation_id="c-1", ip="1.1.1.1")
    rep = await bh.build_trap_report()
    assert rep["scanner_ua"]["verdict"].startswith("unknown")
    assert rep["chat_lock"]["verdict"].startswith("unknown")
    assert bh.BEHAVIOR_HEALTH["trap_report_lookup_failed"] == 1


@pytest.mark.asyncio
async def test_a_real_client_still_wins_over_a_failed_lookup(monkeypatch):
    monkeypatch.setattr(bh, "_ip_went_on_normally", AsyncMock(side_effect=[True, RuntimeError("x")]))
    monkeypatch.setattr(bh, "_lead_has_trip", AsyncMock(return_value=set()))
    bh.record("scanner_ua", ip="1.1.1.1")
    bh.record("scanner_ua", ip="2.2.2.2")
    rep = await bh.build_trap_report()
    assert rep["scanner_ua"]["verdict"] == "keep off"


def test_a_hit_names_the_user_apart_from_the_ip():
    bh.record("upload_mismatch", ip="203.0.113.9", user_id="u-1")
    hit = bh.SHADOW_HITS[-1]
    assert hit["ip"] == "203.0.113.9" and hit["user_id"] == "u-1"


def test_the_avatar_trap_records_the_client_ip_not_the_user_id():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "auth_routes.py").read_text()
    assert 'bh.record("upload_mismatch", ip=current_user' not in src
    assert 'bh.record("upload_mismatch", ip=client_ip(request' in src
