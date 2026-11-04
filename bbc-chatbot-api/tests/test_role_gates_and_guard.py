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


def test_stream_buffer_holds_a_suspicious_tail():
    # A trailing digit run is held back so a number is never scrubbed split
    # across two chunks; the final add_message scrub sees the whole reply.
    safe, tail = output_guard.scrub_stream_buffer("Call us at +1 (888) 322")
    assert tail == "322" and safe == "Call us at +1 (888) "
    safe2, tail2 = output_guard.scrub_stream_buffer("All set, enjoy your trip.")
    assert tail2 == ""


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
