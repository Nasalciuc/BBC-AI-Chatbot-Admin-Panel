"""Sticky supervisor-columns flag must not poison tags on a transient error.

Proven by conv #1140: a generic except flipped `_supervisor_columns_ok` False
for the life of the process → activity clocks froze → every new chat kept
`last_user_message_at = NULL` → derived tag = no_engagement for engaged clients.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.db.supabase as sb


class _Err(Exception):
    """Exception that can carry a Postgres/PostgREST code attribute."""

    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


@pytest.fixture(autouse=True)
def _reset_flag():
    """Each test starts from a clean (unprobed) flag."""
    original_ok = sb._supervisor_columns_ok
    original_at = sb._supervisor_columns_downgraded_at
    sb._supervisor_columns_ok = None
    sb._supervisor_columns_downgraded_at = None
    yield
    sb._supervisor_columns_ok = original_ok
    sb._supervisor_columns_downgraded_at = original_at


class TestIsMissingColumnError:
    def test_pg_code_42703(self):
        assert sb._is_missing_column_error(_Err("whatever", code="42703")) is True

    def test_message_match(self):
        assert sb._is_missing_column_error(
            Exception('column "chat_number" does not exist')
        ) is True

    def test_postgrest_dict_arg(self):
        err = Exception({"code": "42703", "message": "column conversations.x does not exist"})
        assert sb._is_missing_column_error(err) is True

    def test_timeout_is_not_missing_column(self):
        assert sb._is_missing_column_error(TimeoutError("timed out")) is False

    def test_http_503_is_not_missing_column(self):
        assert sb._is_missing_column_error(Exception("HTTP 503 Service Unavailable")) is False

    def test_connection_reset_is_not_missing_column(self):
        assert sb._is_missing_column_error(ConnectionError("Connection reset by peer")) is False


class TestDowngradeTrigger:
    def test_column_error_flips_the_flag(self):
        assert sb._supervisor_columns_available() is True
        sb._downgrade_supervisor_columns(Exception("column last_user_message_at does not exist"))
        assert sb._supervisor_columns_ok is False
        assert sb._supervisor_columns_available() is False
        assert sb._supervisor_columns_downgraded_at is not None

    def test_generic_error_does_not_flip_the_flag(self):
        sb._downgrade_supervisor_columns(Exception("HTTP 503 Service Unavailable"))
        assert sb._supervisor_columns_ok is None
        assert sb._supervisor_columns_available() is True
        assert sb._supervisor_columns_downgraded_at is None

    @pytest.mark.asyncio
    async def test_get_conversations_generic_error_does_not_downgrade(self):
        """The hair-trigger site: a transient failure must fall through to the
        legacy retry WITHOUT freezing clocks for the rest of the process."""
        calls = {"n": 0}

        async def fake_run(fn):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("upstream timeout")
            # Legacy retry succeeds with an empty page.
            return type("R", (), {"data": [], "count": 0})()

        with (
            patch.object(sb, "_run_sync", fake_run),
            patch.object(sb, "enrich_conversations_agent_info", AsyncMock(return_value=[])),
        ):
            rows, total = await sb.get_conversations(limit=10)

        assert (rows, total) == ([], 0)
        assert sb._supervisor_columns_ok is None
        assert sb._supervisor_columns_available() is True

    @pytest.mark.asyncio
    async def test_get_conversations_column_error_does_downgrade(self):
        calls = {"n": 0}

        async def fake_run(fn):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Err('column "chat_number" does not exist', code="42703")
            return type("R", (), {"data": [], "count": 0})()

        with (
            patch.object(sb, "_run_sync", fake_run),
            patch.object(sb, "enrich_conversations_agent_info", AsyncMock(return_value=[])),
        ):
            await sb.get_conversations(limit=10)

        assert sb._supervisor_columns_ok is False
        assert sb._supervisor_columns_available() is False


class TestReprobeTTL:
    def test_available_again_after_60s(self):
        now = 1_000_000.0
        with patch.object(sb.time, "time", return_value=now):
            sb._downgrade_supervisor_columns(Exception("column x does not exist"))
            assert sb._supervisor_columns_available() is False

        # 59s later — still down.
        with patch.object(sb.time, "time", return_value=now + 59):
            assert sb._supervisor_columns_available() is False

        # 60s later — optimistic re-probe.
        with patch.object(sb.time, "time", return_value=now + 60):
            assert sb._supervisor_columns_available() is True

    def test_real_missing_column_re_downgrades_for_another_60s(self):
        now = 1_000_000.0
        with patch.object(sb.time, "time", return_value=now):
            sb._downgrade_supervisor_columns(Exception("column x does not exist"))

        with patch.object(sb.time, "time", return_value=now + 60):
            assert sb._supervisor_columns_available() is True
            # Probe fails again — another 60s window.
            sb._downgrade_supervisor_columns(Exception("column x does not exist"))
            assert sb._supervisor_columns_available() is False

        with patch.object(sb.time, "time", return_value=now + 119):
            assert sb._supervisor_columns_available() is False
        with patch.object(sb.time, "time", return_value=now + 120):
            assert sb._supervisor_columns_available() is True


class TestHealthSurface:
    def test_payload_shape_when_ok(self):
        status = sb.supervisor_columns_status()
        assert status == {"ok": True, "downgraded_at": None}

    def test_payload_shape_when_downgraded(self):
        with patch.object(sb.time, "time", return_value=1_700_000_000.0):
            sb._downgrade_supervisor_columns(Exception("column x does not exist"))
            status = sb.supervisor_columns_status()
        assert status["ok"] is False
        assert status["downgraded_at"] is not None
        assert status["downgraded_at"].endswith("+00:00") or "T" in status["downgraded_at"]

    def test_health_endpoint_includes_supervisor_columns(self):
        from app.api.health import router

        app = FastAPI()
        app.include_router(router)
        with (
            patch("app.api.health.settings") as fake_settings,
            patch("app.api.health.get_scheduler_health", return_value={"ok": True}),
        ):
            fake_settings.debug = False
            resp = TestClient(app).get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "supervisor_columns" in body
        assert body["supervisor_columns"]["ok"] is True
        assert body["supervisor_columns"]["downgraded_at"] is None
