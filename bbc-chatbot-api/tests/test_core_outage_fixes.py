"""Wave 5 PR 1 — the log-proven outage fixes (Railway, Aug 12).

E1  `messages.0: user messages must have non-empty content` on every
    mid-conversation turn. Two layers pinned here: the orchestrator
    precedence bug that erased the client's message on every
    non-correction turn (`a + b if c else ""` binds `(a+b) if c else ""`),
    and the claude.py guard that never lets an empty user turn reach the
    API. Model fallback can't fix an invalid payload — 400 now skips the
    cross-model retry and is counted in /health.ai_fallback.payload_errors.
E3  `Server disconnected` bursts — _run_sync retries ONCE on a dead
    pooled connection, second failure raises.
E2  blocklist PGRST205 — fail-open False + ONE warning per process.
1f  claude-opus-4-8 rejects `temperature` → every opus call 400'd and the
    fallback silently ran the decisive tiers on Haiku. Per-model param
    policy strips it for opus, keeps it for sonnet/haiku.
"""

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import anthropic

from app.ai import claude
from app.ai.claude import (
    AI_FALLBACK_HEALTH,
    _EMPTY_USER_STUB,
    _build_messages,
    _call_model,
    _call_model_with_tools,
    _sampling_params,
)
from config.settings import settings

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _read(*parts: str) -> str:
    with open(os.path.join(APP, *parts), encoding="utf-8") as f:
        return f.read()


class _FakeBadRequest(anthropic.BadRequestError):
    def __init__(self, msg: str):
        Exception.__init__(self, msg)
        self.status_code = 400
        self.message = msg


class _FakeOverloaded(anthropic.APIError):
    def __init__(self, msg: str = "overloaded"):
        Exception.__init__(self, msg)
        self.status_code = 529
        self.message = msg


def _fake_response(text: str = "Great choice — when are you flying?"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


@pytest.fixture(autouse=True)
def _reset_counters():
    AI_FALLBACK_HEALTH["activations_since_boot"] = 0
    AI_FALLBACK_HEALTH["payload_errors"] = 0
    yield


# ════════════════════════════════════════════════════════════
# E1 — the payload never carries an empty user turn
# ════════════════════════════════════════════════════════════

class TestNonEmptyFirstTurn:
    def test_empty_message_becomes_stub(self):
        msgs = _build_messages("")
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == _EMPTY_USER_STUB
        assert msgs[0]["content"].strip()

    def test_whitespace_only_becomes_stub(self):
        assert _build_messages("   \n ")[0]["content"] == _EMPTY_USER_STUB

    def test_real_message_untouched(self):
        assert _build_messages("LAX to SYD")[0]["content"] == "LAX to SYD"

    def test_every_api_call_site_uses_the_guard(self):
        src = _read("ai", "claude.py")
        assert 'messages=[{"role": "user"' not in src
        assert src.count("messages=_build_messages(") == 6

    def test_orchestrator_precedence_bug_fixed(self):
        """`message + (...) if cond else ""` erased the client's message on
        every non-correction turn. The directive must be the conditional
        part, never the message."""
        src = _read("pipeline", "orchestrator.py")
        assert '"_raw_message": message + (' in src
        assert '"_raw_message": (\n            message + (' not in src


# ════════════════════════════════════════════════════════════
# 1b — 400 invalid_request never triggers the model fallback
# ════════════════════════════════════════════════════════════

class TestPayloadErrorNoFallback:
    def test_400_single_call_no_second_model(self):
        client = MagicMock()
        client.messages.create.side_effect = _FakeBadRequest(
            "messages.0: user messages must have non-empty content"
        )
        with patch.object(claude, "_get_client", return_value=client):
            text, cost = _call_model("claude-sonnet-4-6", "sys", "hi", 400, 0.6)

        assert text is None
        assert client.messages.create.call_count == 1
        assert AI_FALLBACK_HEALTH["payload_errors"] == 1
        assert AI_FALLBACK_HEALTH["activations_since_boot"] == 0

    def test_overloaded_still_falls_back(self):
        client = MagicMock()
        client.messages.create.side_effect = [
            _FakeOverloaded(),
            _fake_response("rescued"),
        ]
        with patch.object(claude, "_get_client", return_value=client):
            text, cost = _call_model("claude-sonnet-4-6", "sys", "hi", 400, 0.6)

        assert text == "rescued"
        assert client.messages.create.call_count == 2
        assert client.messages.create.call_args_list[1].kwargs["model"] == settings.fallback_model
        assert AI_FALLBACK_HEALTH["activations_since_boot"] == 1
        assert AI_FALLBACK_HEALTH["payload_errors"] == 0

    def test_all_four_error_sites_guard_payload_errors(self):
        src = _read("ai", "claude.py")
        assert src.count("if _is_payload_error(e):") == 4

    def test_health_payload_carries_the_counter(self):
        assert "payload_errors" in AI_FALLBACK_HEALTH


# ════════════════════════════════════════════════════════════
# 1f — per-model sampling params: opus loses temperature, sonnet keeps it
# ════════════════════════════════════════════════════════════

class TestPerModelParams:
    def test_opus_strips_temperature(self):
        assert _sampling_params("claude-opus-4-8", temperature=0.6) == {}

    def test_sonnet_and_haiku_keep_temperature(self):
        assert _sampling_params("claude-sonnet-4-6", temperature=0.6) == {"temperature": 0.6}
        assert _sampling_params("claude-haiku-4-5-20251001", temperature=0.3) == {"temperature": 0.3}

    def test_opus_payload_has_no_temperature_and_succeeds_on_opus(self):
        """The tiering restored: an opus call completes ON opus instead of
        400-ing into Haiku."""
        client = MagicMock()
        client.messages.create.return_value = _fake_response("closing on opus")
        with patch.object(claude, "_get_client", return_value=client):
            text, cost = _call_model("claude-opus-4-8", "sys", "close the deal", 450, 0.6)

        assert text == "closing on opus"
        assert client.messages.create.call_count == 1
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-opus-4-8"
        assert "temperature" not in kwargs

    def test_deprecated_param_400_does_not_cross_model_retry(self):
        client = MagicMock()
        client.messages.create.side_effect = _FakeBadRequest(
            "'temperature' is deprecated for this model"
        )
        with patch.object(claude, "_get_client", return_value=client):
            text, _cost, entities = _call_model_with_tools(
                "claude-opus-4-8", "sys", "objection", 450, 0.6
            )

        assert text is None
        assert client.messages.create.call_count == 1
        assert AI_FALLBACK_HEALTH["payload_errors"] == 1


# ════════════════════════════════════════════════════════════
# Catherine e2e — greet-first turn reaches the API as a valid payload
# ════════════════════════════════════════════════════════════

class TestCatherineFlow:
    def test_mid_conversation_turn_sends_real_text_and_gets_model_reply(self):
        from app.pipeline.generator import _run_tiered_generation
        from app.pipeline.intent import Intent

        client = MagicMock()
        client.messages.create.return_value = _fake_response(
            "Oct 28 LAX to SYD — beautiful timing. Business or first?"
        )
        with patch.object(claude, "_get_client", return_value=client):
            text, cost, entities, tier, reason = _run_tiered_generation(
                Intent.FLIGHT_SEARCH if hasattr(Intent, "FLIGHT_SEARCH") else list(Intent)[0],
                None,
                "sales",
                ("static rules", "history: AI greeted first"),
                "I want to fly LAX to SYD, OCT 28 to NOV 10",
            )

        assert text and "template" not in (tier or "")
        sent = client.messages.create.call_args_list[0].kwargs["messages"]
        assert sent[0]["role"] == "user"
        assert sent[0]["content"] == "I want to fly LAX to SYD, OCT 28 to NOV 10"


# ════════════════════════════════════════════════════════════
# E3 — _run_sync retries once on a dead pooled connection
# ════════════════════════════════════════════════════════════

class TestDbRetry:
    @pytest.mark.asyncio
    async def test_disconnect_once_retried_and_succeeds(self):
        from app.db import supabase as sb

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Server disconnected")
            return "ok"

        assert await sb._run_sync(flaky) == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_disconnect_twice_raises(self):
        from app.db import supabase as sb

        def dead():
            raise RuntimeError("Server disconnected")

        with pytest.raises(RuntimeError):
            await sb._run_sync(dead)

    @pytest.mark.asyncio
    async def test_real_errors_do_not_retry(self):
        from app.db import supabase as sb

        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("column does not exist")

        with pytest.raises(ValueError):
            await sb._run_sync(broken)
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_non_idempotent_calls_never_retry(self):
        """A disconnect can land AFTER the server committed the write —
        re-running an INSERT would duplicate the row (doubled chat
        message). idempotent=False disables the retry."""
        from app.db import supabase as sb

        calls = {"n": 0}

        def insert_like():
            calls["n"] += 1
            raise RuntimeError("Server disconnected")

        with pytest.raises(RuntimeError):
            await sb._run_sync(insert_like, idempotent=False)
        assert calls["n"] == 1

    def test_every_insert_site_is_marked_non_idempotent(self):
        import re

        src = _read("db", "supabase.py")
        inserts = src.count(".insert(")
        # call-site marks only (trailing , or )) — not the docstring mention
        marks = len(re.findall(r"idempotent=False[,)]", src))
        # The presence PATCH rpc is also non-idempotent (a retried merge is
        # harmless, but a retried heartbeat is pointless work), so marks may
        # exceed inserts — every INSERT must still carry one.
        assert marks >= inserts, (
            f"{inserts} .insert( sites but only {marks} idempotent=False marks — "
            "a new INSERT went through _run_sync with the retry enabled"
        )
        assert "patch_conv_presence" in src


# ════════════════════════════════════════════════════════════
# E2 — blocklist PGRST205: fail-open + ONE warning per process
# ════════════════════════════════════════════════════════════

class TestBlocklistGuard:
    @pytest.mark.asyncio
    async def test_missing_table_false_and_single_warning(self, caplog):
        from app.db import supabase as sb

        sb._BLOCKLIST_TABLE_MISSING_WARNED = False

        async def raise_missing(fn):
            raise RuntimeError("Could not find the table 'public.blocklist' (PGRST205)")

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", side_effect=raise_missing):
            with caplog.at_level(logging.WARNING, logger="app.db.supabase"):
                assert await sb.blocklist_has_active("email", "a@b.c") is False
                assert await sb.blocklist_has_active("phone", "+123") is False

        warnings = [r for r in caplog.records if "PGRST205" in r.getMessage()]
        assert len(warnings) == 1
        assert "022_blocklist.sql" in warnings[0].getMessage()

    @pytest.mark.asyncio
    async def test_other_errors_still_logged_as_error(self, caplog):
        from app.db import supabase as sb

        async def raise_other(fn):
            raise RuntimeError("permission denied")

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", side_effect=raise_other):
            with caplog.at_level(logging.ERROR, logger="app.db.supabase"):
                assert await sb.blocklist_has_active("email", "a@b.c") is False

        assert any("blocklist_has_active" in r.getMessage() for r in caplog.records)
