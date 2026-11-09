"""PR-4 — output guard, token_version, metadata allowlist, cache, role gate."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import output_guard
from app.security import gates, token_version
from app.security.metadata import allowed_widget_metadata


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    output_guard._reset_for_tests()
    gates._reset_for_tests()
    token_version._reset_for_tests()
    yield
    flags._reset_for_tests()


# ── output guard ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enforce_strips_foreign_contacts_keeps_brand(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        out = await output_guard.guard_ai_text(
            "Call +1 (888) 322-7999 or the scammer at +1 555 123 4567, see evil.com. "
            "We are IATA #14531683 accredited."
        )
    assert "+1 (888) 322-7999" in out
    assert "14531683" in out
    assert "evil.com" not in out and "555 123 4567" not in out
    assert "[removed]" in out


@pytest.mark.asyncio
async def test_log_mode_changes_nothing(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    text = "Reach me at evil.com or +1 555 999 8888"
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        out = await output_guard.guard_ai_text(text)
    assert out == text
    assert output_guard.OUTPUT_GUARD_HEALTH["would_remove"] >= 1
    assert output_guard.OUTPUT_GUARD_HEALTH["removed"] == 0


@pytest.mark.asyncio
async def test_brand_email_and_domain_kept(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        out = await output_guard.guard_ai_text("Email info@buybusinessclass.com or visit buybusinessclass.com")
    assert "info@buybusinessclass.com" in out and "buybusinessclass.com" in out
    assert "[removed]" not in out


def _stream(guard, chunks):
    return "".join(guard.feed(c) for c in chunks)


def test_enforce_streams_a_sentence_at_a_time_scrubbed_whole(monkeypatch):
    # A contact split across chunks is seen whole: nothing of a sentence goes
    # out before its end, and then it goes out scrubbed.
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    g = output_guard.StreamGuard()
    assert _stream(g, ["Sure! Call the agent at +1 555 ", "123 4567 or visit sca", "m.com today. Thanks"]) == \
        "Sure! Call the agent at [removed] or visit [removed] today. "
    assert g.flush() == "Thanks"
    # The brand's own contacts go out untouched, a line at a time too.
    g = output_guard.StreamGuard()
    assert _stream(g, ["Call +1 (888) 322-", "7999\nor email info@buybusinessclass.com.\n"]) == \
        "Call +1 (888) 322-7999\nor email info@buybusinessclass.com.\n"


def test_enforce_stream_keeps_the_clients_own_contact(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    g = output_guard.StreamGuard(output_guard.own_contacts("+1 555 014 2290", "Michael.R@example.com"))
    assert _stream(g, ["We'll call +1 555 014 2290 and write to michael.r@example.com. "]) == \
        "We'll call +1 555 014 2290 and write to michael.r@example.com. "


def test_log_mode_streams_every_chunk_as_it_comes(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = output_guard.StreamGuard()
    assert [g.feed(c) for c in ["Visit sca", "m.com"]] == ["Visit sca", "m.com"]
    assert g.flush() == ""


@pytest.mark.asyncio
async def test_a_link_with_digits_is_never_the_clients_own_contact(monkeypatch):
    # The client's phone ends in 9; "scam9.com" carries a 9. Only a phone is
    # compared by its digits.
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(return_value={"visitor_phone": "+1 555 014 2299"})), \
         patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        out = await output_guard.guard_ai_text("Pay at scam9.com or call +1 555 014 2299", conversation_id="c1")
    assert out == "Pay at [removed] or call +1 555 014 2299"
    assert output_guard.OUTPUT_GUARD_HEALTH["removed"] == 1


def test_a_broken_guard_keeps_the_reply_and_fails_closed_when_enforcing(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    assert output_guard.guard_failed("Visit evil.com", RuntimeError("boom")) == "Visit [removed]"
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    assert output_guard.guard_failed("Visit evil.com", RuntimeError("boom")) == "Visit evil.com"
    assert output_guard.OUTPUT_GUARD_HEALTH["errors"] == 2


@pytest.mark.asyncio
async def test_add_message_stores_the_reply_when_the_guard_breaks(monkeypatch):
    from app.db import supabase as sb

    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    sent = {}

    class _Tbl:
        def insert(self, payload):
            sent.update(payload)
            return self

        def execute(self):
            return MagicMock(data=[{"id": "m1", **sent}])

    client = MagicMock()
    client.table.return_value = _Tbl()
    with patch.object(sb, "get_client", return_value=client), \
         patch("app.ai.output_guard.guard_ai_text", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(sb, "_touch_conversation_activity", new=AsyncMock()):
        row = await sb.add_message("c1", "ai", "Visit evil.com")
    assert row and row["content"] == "Visit [removed]"


# ── metadata allowlist ──────────────────────────────────────────────────────

def test_allowlist_keeps_attribution_drops_the_rest():
    raw = {
        "utm_source": "google", "landing_city": "NYC", "gclid": "abc",
        "page_url": "/x", "referrer": "r",
        "client_ip": "6.6.6.6", "site": "evil", "engaged_agent_id": "a1",
        "sticky_agent_id": "a2", "summary_shown_at": "now",
    }
    out = allowed_widget_metadata(raw)
    assert out == {"utm_source": "google", "landing_city": "NYC", "gclid": "abc",
                   "page_url": "/x", "referrer": "r"}
    for forbidden in ("client_ip", "site", "engaged_agent_id", "sticky_agent_id", "summary_shown_at"):
        assert forbidden not in out


def test_allowlist_keeps_every_click_id_the_widget_sends():
    # CodeRabbit #226 follow-up: utm.ts captures these and the server reads
    # them (CRM utm block, the bing / kayak source fallback). They were dropped.
    raw = {"msclkid": "m1", "ttclid": "t1", "kayak_click_id": "k1", "kclid": "k2",
           "skyscanner_click_id": "s1", "google_analytics_client_id": "123.456", "fbclid": "f1"}
    assert allowed_widget_metadata(raw) == raw


def test_allowlist_keeps_strings_only_and_bounded():
    out = allowed_widget_metadata({
        "utm_source": {"$where": "1"}, "utm_medium": ["a"], "utm_term": 7,
        "page_url": "/" + "p" * 5000, "utm_" + "x" * 80: "long key", "referrer": "r",
    })
    assert out == {"page_url": "/" + "p" * 2047, "referrer": "r"}
    many = allowed_widget_metadata({f"utm_k{i}": "v" for i in range(80)})
    assert len(many) == 50


def test_allowlist_keeps_a_known_brand_and_the_booking_id():
    """Review fix: `site` decides the brand (prompts, phone, CRM base); the
    server derives it nowhere, so a configured brand id is kept. An unknown
    one ('evil' above) is still dropped."""
    out = allowed_widget_metadata({"site": "bct", "booking_id": " BK-123 "})
    assert out == {"site": "bct", "booking_id": "BK-123"}
    assert "site" not in allowed_widget_metadata({"site": "bbc-but-not-really"})
    assert "booking_id" not in allowed_widget_metadata({"booking_id": "x" * 65})
    assert "booking_id" not in allowed_widget_metadata({"booking_id": {"$ne": 1}})


# ── cache: templates only ────────────────────────────────────────────────

def test_cache_rejects_generated_reply():
    from app.services import response_cache

    with patch.object(response_cache, "_get_redis") as redis:
        client = MagicMock(); redis.return_value = client
        response_cache.set_cached("hi", "generated answer", "greeting", model_used="sonnet")
        client.setex.assert_not_called()
        response_cache.set_cached("hi", "template answer", "greeting", model_used="template")
        client.setex.assert_called_once()


# ── role gate: log-first ────────────────────────────────────────────────────

def test_gate_allows_management():
    assert gates.enforce_or_log(gates.MANAGEMENT, {"role": "owner"}, "x") is True


def test_gate_logs_but_allows_when_not_enforcing():
    assert gates.enforce_or_log(gates.MANAGEMENT, {"role": "sales"}, "x") is False
    assert gates.GATE_HEALTH["would_refuse"] == 1
    assert gates.GATE_HEALTH["refused"] == 0


def test_gate_raises_403_when_enforcing(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("ROLE_GATES_ENFORCE", "true")
    with pytest.raises(HTTPException) as exc:
        gates.enforce_or_log(gates.MANAGEMENT, {"role": "sales"}, "x")
    assert exc.value.status_code == 403


# ── token_version ────────────────────────────────────────────────────────

def test_token_version_matches_zero_by_default(monkeypatch):
    monkeypatch.setattr(token_version, "_read", lambda uid: 0)
    assert token_version.matches("u1", None) is True
    assert token_version.matches("u1", 0) is True


def test_token_version_unreadable_and_uncached_fails_closed(monkeypatch):
    monkeypatch.setattr(token_version, "_read", lambda uid: None)
    assert token_version.matches("u1", 0) is False
    assert token_version.TOKEN_VERSION_HEALTH["refused_unreadable"] == 1


def test_token_version_unreadable_keeps_the_version_this_process_read(monkeypatch):
    monkeypatch.setattr(token_version, "_read", lambda uid: 2)
    assert token_version.matches("u1", 2) is True
    monkeypatch.setattr(token_version.time, "time", lambda: 10**10)  # past the 60s TTL
    monkeypatch.setattr(token_version, "_read", lambda uid: None)
    assert token_version.matches("u1", 2) is True
    token_version.invalidate_cache("u1")  # what a bump does
    assert token_version.matches("u1", 2) is False


def test_a_generic_read_error_is_not_version_zero():
    real_read = token_version._read.real
    client = MagicMock()
    client.table.side_effect = RuntimeError("connection reset")
    with patch("app.db.supabase.get_client", return_value=client):
        assert real_read("u1") is None
    assert token_version.TOKEN_VERSION_HEALTH["read_failed"] == 1


def test_a_missing_column_still_reads_zero():
    real_read = token_version._read.real
    client = MagicMock()
    client.table.side_effect = RuntimeError("column users.token_version does not exist (42703)")
    with patch("app.db.supabase.get_client", return_value=client):
        assert real_read("u1") == 0


def test_token_version_mismatch_after_bump(monkeypatch):
    monkeypatch.setattr(token_version, "_read", lambda uid: 3)
    token_version.invalidate_cache("u1")
    assert token_version.matches("u1", 2) is False
    assert token_version.matches("u1", 3) is True


# ── review fixes: the output guard never logs or mails a customer contact ──

@pytest.mark.asyncio
async def test_guard_ignores_the_clients_own_contact_and_logs_no_values(monkeypatch, caplog):
    from unittest.mock import AsyncMock
    from app.ai import output_guard as og

    og._reset_for_tests()
    monkeypatch.setattr("app.db.supabase.get_conversation_simple",
                        AsyncMock(return_value={"visitor_phone": "+1 415 555 0134", "visitor_email": "ana@example.com"}))
    sent = AsyncMock()
    monkeypatch.setattr("app.services.email.send_ops_alert_email", sent)
    text = "Confirming: ana@example.com, +1 415 555 0134. Call me at +44 20 7946 0999 or x@evil.io"
    with caplog.at_level("WARNING"):
        out = await og.guard_ai_text(text, conversation_id="c1")
    assert out == text                                   # log mode: unchanged
    assert og.OUTPUT_GUARD_HEALTH["would_remove"] == 2   # only the foreign two
    logged = " ".join(r.getMessage() for r in caplog.records)
    for value in ("ana@example.com", "x@evil.io", "7946", "555"):
        assert value not in logged

    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    from app import flags
    flags._reset_for_tests()
    out = await og.guard_ai_text(text, conversation_id="c1")
    assert "ana@example.com" in out and "+1 415 555 0134" in out
    assert "x@evil.io" not in out and "7946" not in out


# ── CodeRabbit #226: the domain gate logs the domain, never the address ───

@pytest.mark.asyncio
async def test_domain_gate_warning_names_the_domain_not_the_address(caplog):
    import logging

    from app.api import auth_routes

    req = auth_routes.InviteRequest(name="Test", email="someone.private@gmail.com", role="sales")
    with patch.object(auth_routes.db, "get_user_by_email", new=AsyncMock(side_effect=RuntimeError("stop"))), \
         caplog.at_level(logging.WARNING, logger="app.api.auth_routes"):
        with pytest.raises(RuntimeError):
            await auth_routes.invite_user(req, current_user={"role": "owner", "id": "u0"})
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "domain-gate" in text and "gmail.com" in text
    assert "someone.private" not in text


# ── CodeRabbit #226: prices, decimals and dates are not contacts ──────────

@pytest.mark.parametrize("text", [
    "Fares from $1,200.50 one way",
    "Depart 2026-05-14, back 12.05.2026",
    "Dates: 2026-05-14 - 2026-05-21",
    "The flight is about 2.5 hours",
])
def test_prices_decimals_and_dates_survive_the_guard(text):
    out, removed = output_guard._scrub(text)
    assert out == text and removed == []


def test_a_phone_shaped_like_a_bad_date_is_still_removed():
    out, removed = output_guard._scrub("call 12.34.5678 now")
    assert removed == ["12.34.5678"]
