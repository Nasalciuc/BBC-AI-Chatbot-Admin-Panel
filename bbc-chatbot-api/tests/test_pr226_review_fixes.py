"""CodeRabbit review of #226: the downgrade helpers read only their own column,
a failed session bump is not a clean success, and the cost report reads every
page of the ledger and skips a row it could not read."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as sb  # noqa: E402


# ── a missing column downgrades only the filter that reads it ─────────────

@pytest.fixture
def _clean_columns(monkeypatch):
    monkeypatch.setattr(sb, "_decoy_ok", {})
    monkeypatch.setattr(sb, "_decoy_downgraded_at", {})
    monkeypatch.setattr(sb, "_chat_enabled_ok", None)
    monkeypatch.setattr(sb, "_chat_enabled_downgraded_at", None)


def test_a_missing_chat_enabled_does_not_unhide_decoys(_clean_columns):
    err = RuntimeError("column users.chat_enabled does not exist (42703)")
    sb._downgrade_decoy("users", err)
    assert sb._decoy_ok.get("users") is not False
    sb._downgrade_chat_enabled(err)
    assert sb._chat_enabled_ok is False


def test_a_missing_030_column_does_not_unhide_lead_decoys(_clean_columns):
    sb._downgrade_decoy("leads", RuntimeError("column leads.crm_push_gate_reason does not exist (42703)"))
    assert sb._decoy_ok.get("leads") is not False


def test_a_missing_is_decoy_downgrades_only_the_decoy_filter(_clean_columns):
    err = RuntimeError("column users.is_decoy does not exist (42703)")
    sb._downgrade_decoy("users", err)
    assert sb._decoy_ok["users"] is False
    sb._downgrade_chat_enabled(err)
    assert sb._chat_enabled_ok is not False


# ── bump_token_version: a generic failure raises ──────────────────────────

@pytest.mark.asyncio
async def test_bump_token_version_raises_on_a_generic_failure():
    with patch.object(sb, "_run_sync", new=AsyncMock(side_effect=RuntimeError("connection reset"))), \
         patch.object(sb, "get_client"):
        with pytest.raises(RuntimeError):
            await sb.bump_token_version("u1")


@pytest.mark.asyncio
async def test_bump_token_version_tolerates_the_missing_column():
    err = RuntimeError("column users.token_version does not exist (42703)")
    with patch.object(sb, "_run_sync", new=AsyncMock(side_effect=err)), patch.object(sb, "get_client"):
        await sb.bump_token_version("u1")  # no raise: inert until 039


# ── /api/cron/ai-cost-report: pages, half-open day, skip on failure ───────

@pytest.mark.asyncio
async def test_ledger_sum_reads_every_page_over_a_half_open_day(monkeypatch):
    from app.api import cron

    monkeypatch.setattr(cron, "_LEDGER_PAGE", 2)
    pages = [[{"cost_usd": 1.0}, {"cost_usd": 2.0}], [{"cost_usd": 3.0}, {"cost_usd": 4.0}], [{"cost_usd": 0.5}]]
    client = MagicMock()
    q = client.table.return_value.select.return_value.gte.return_value.lt.return_value
    q = q.eq.return_value.order.return_value
    q.range.return_value.execute.side_effect = [MagicMock(data=p) for p in pages]

    async def _run(fn, **kw):
        return fn()

    monkeypatch.setattr(cron.db, "_run_sync", _run)
    monkeypatch.setattr(cron.db, "get_client", lambda: client)
    assert await cron._ledger_cost_for("2026-11-02", "claude-sonnet-5-5") == 10.5
    sel = client.table.return_value.select.return_value
    assert sel.gte.call_args.args == ("created_at", "2026-11-02T00:00:00+00:00")
    assert sel.gte.return_value.lt.call_args.args == ("created_at", "2026-11-03T00:00:00+00:00")
    assert [c.args for c in q.range.call_args_list] == [(0, 1), (2, 3), (4, 5)]


@pytest.mark.asyncio
async def test_a_failed_ledger_read_skips_the_row_instead_of_storing_zero(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import cron
    from config.settings import settings

    with patch("app.db.supabase.get_client"):
        from app.main import app

    monkeypatch.setattr(cron, "_require_cron", lambda r: None)
    monkeypatch.setattr(settings, "anthropic_api_key_id", "key-bot", raising=False)
    monkeypatch.setattr(cron, "_ledger_cost_for", AsyncMock(side_effect=RuntimeError("timeout")))
    upserts = []

    async def _run(fn, **kw):
        upserts.append(kw.get("label"))
        return MagicMock(data=[])

    monkeypatch.setattr(cron.db, "_run_sync", _run)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/cron/ai-cost-report", json={"rows": [
            {"day": "2026-11-02", "api_key_id": "key-bot", "model": "claude-sonnet-5-5", "billed_cost_usd": 10.0},
        ]})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"stored": 0, "ledger_failed": 1, "max_diff_pct": 0.0}
    assert "ai_cost_daily" not in upserts, "a false ledger_cost_usd = 0 was written"


# ── PATCH /admin/users/{id}: a failed bump says so ────────────────────────

@pytest.mark.asyncio
async def test_a_demotion_whose_sessions_were_not_revoked_says_so():
    from app.api import users as users_api
    from app.models.admin import UserUpdate
    from app.security import token_version

    token_version._reset_for_tests()
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="10.0.0.1")
    with patch.object(users_api.db, "get_user_by_id",
                      new=AsyncMock(return_value={"id": "u9", "role": "admin", "is_active": True})), \
         patch.object(users_api.db, "update_user",
                      new=AsyncMock(return_value={"id": "u9", "role": "sales", "is_active": True})), \
         patch.object(users_api.db, "create_user_access_audit", new=AsyncMock()), \
         patch.object(users_api.db, "bump_token_version", new=AsyncMock(side_effect=RuntimeError("down"))), \
         patch("app.security.decoys.alert_role_change", new=AsyncMock()), \
         patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as ops:
        res = await users_api.update_user("u9", UserUpdate(role="sales"), req, user={"id": "u0", "role": "owner"})
    assert res["success"] is True and res["error"] == "sessions_not_invalidated"
    assert ops.await_count == 1
    assert token_version.TOKEN_VERSION_HEALTH["bump_failed"] == 1


# ── Second round (#226 full review): sessions end on time ─────────────────

@pytest.mark.asyncio
async def test_sessions_are_revoked_before_the_access_change_email():
    # The e-mail can take its 10 s timeout; the old role must not live that long.
    from app.api import users as users_api
    from app.models.admin import UserUpdate

    order = []
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="10.0.0.1")
    with patch.object(users_api.db, "get_user_by_id",
                      new=AsyncMock(return_value={"id": "u9", "role": "admin", "is_active": True})), \
         patch.object(users_api.db, "update_user",
                      new=AsyncMock(return_value={"id": "u9", "role": "sales", "is_active": True})), \
         patch.object(users_api.db, "create_user_access_audit", new=AsyncMock()), \
         patch.object(users_api.db, "bump_token_version", new=AsyncMock(side_effect=lambda *_: order.append("bump"))), \
         patch("app.security.decoys.alert_role_change", new=AsyncMock(side_effect=lambda *_: order.append("email"))):
        res = await users_api.update_user("u9", UserUpdate(role="sales"), req, user={"id": "u0", "role": "owner"})
    assert order == ["bump", "email"] and res == {"success": True, "data": res["data"], "count": 1}


class _Users:
    """users table stand-in: records the update's filters."""

    def __init__(self, version):
        self.version, self.filters, self.payload = version, [], None

    def table(self, _name):
        return self

    def select(self, *_a):
        self.payload = None
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, col, val):
        if self.payload is not None:
            self.filters.append((col, val))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.payload is None:
            return MagicMock(data=[{"token_version": self.version}] if self.version is not None else [])
        return MagicMock(data=[])  # matched nothing: another bump got there first


@pytest.mark.asyncio
async def test_a_bump_writes_over_the_version_it_read_and_may_be_retried():
    users = _Users(3)
    flags = []

    async def _run(fn, idempotent=True, **_k):
        flags.append(idempotent)
        return fn()

    with patch.object(sb, "get_client", return_value=users), patch.object(sb, "_run_sync", new=_run):
        await sb.bump_token_version("u1")  # a write that matches nothing is still a bump
    assert users.payload == {"token_version": 4}
    assert users.filters == [("id", "u1"), ("token_version", 3)]
    assert flags == [True, True]  # safe to retry: it can never step back over a newer bump


def test_a_token_for_a_user_whose_row_is_gone_is_refused(monkeypatch):
    from app.security import token_version

    token_version._reset_for_tests()
    monkeypatch.setattr(token_version, "_read", token_version._read.real)
    with patch.object(sb, "get_client", return_value=_Users(None)):
        assert token_version.matches("u-gone", 0) is False
        assert token_version.matches("u-gone", None) is False
    token_version._reset_for_tests()
    with patch.object(sb, "get_client", return_value=_Users(2)):
        assert token_version.matches("u1", 2) is True
    token_version._reset_for_tests()


# ── get_users: a retry drops only the missing column ──────────────────────

class _UserList:
    def __init__(self, errors):
        self.errors, self.selects = list(errors), []

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def table(self, _name):
        return self

    def select(self, cols, **_k):
        self.selects.append(cols)
        return self

    def execute(self):
        if self.errors:
            raise self.errors.pop(0)
        return MagicMock(data=[{"id": "u1"}], count=1)


@pytest.mark.asyncio
async def test_get_users_surfaces_a_real_error_with_decoys_off(_clean_columns, monkeypatch):
    # Decoys off made the old guard skip its raise: a timeout was retried
    # without chat_enabled, and every chat toggle read as on.
    from app import flags

    flags._reset_for_tests()
    monkeypatch.setenv("DECOYS_ENABLED", "false")
    q = _UserList([RuntimeError("connection reset")])

    async def _run(fn, *a, **k):
        return fn()

    with patch.object(sb, "get_client", return_value=q), patch.object(sb, "_run_sync", new=_run):
        with pytest.raises(RuntimeError):
            await sb.get_users()
    assert len(q.selects) == 1
    flags._reset_for_tests()


@pytest.mark.asyncio
async def test_get_users_without_is_decoy_keeps_chat_enabled(_clean_columns):
    q = _UserList([RuntimeError("column users.is_decoy does not exist (42703)")])

    async def _run(fn, *a, **k):
        return fn()

    with patch.object(sb, "get_client", return_value=q), patch.object(sb, "_run_sync", new=_run):
        rows, total = await sb.get_users()
    assert rows == [{"id": "u1"}] and total == 1
    assert "chat_enabled" in q.selects[-1]
