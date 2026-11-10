"""No e-mail address in a log line at INFO or above.

Read in production on 9-10 Nov 2026: `[agent-status] <address> -> ready` for
every Ready switch, `Sent ops alert email (...) → <address>` for every alert,
and the invite and attention mails logged their recipient too. A person is
named by id; a recipient is written masked; what the mail provider answers —
it names the address it refused — is written without the addresses.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")

from app.api import agent, lessons  # noqa: E402
from app.services import email as mail  # noqa: E402
from config.settings import settings  # noqa: E402

_ADDR = "dana.popescu@bbc.test"
_OPS = "ops-inbox@bbc.test"


def _lines(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


def _postmark(status, body):
    """httpx.AsyncClient, answering every post with one Postmark response."""
    res = httpx.Response(status, text=body, request=httpx.Request("POST", "https://api.postmarkapp.com/email"))
    client = MagicMock()
    client.post = AsyncMock(return_value=res)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(mail.httpx, "AsyncClient", return_value=ctx), client


# ── a person is named by id ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_ready_switch_names_the_agent_by_id(caplog):
    user = {"id": "u-17", "email": _ADDR, "role": "sales"}
    with patch.object(agent.db, "update_user", new=AsyncMock(return_value={"id": "u-17"})), \
         patch.object(agent, "_fire_and_forget", new=lambda coro: coro.close()), \
         caplog.at_level("INFO"):
        out = await agent.set_ready_status(agent.ReadyStatusRequest(is_ready=True), user)
    assert out == {"success": True, "is_ready": True}
    assert "[agent-status] user=u-17 -> ready" in _lines(caplog)
    assert _ADDR not in _lines(caplog) and "@" not in _lines(caplog)


@pytest.mark.asyncio
async def test_a_reviewed_lesson_names_the_reviewer_by_id(caplog):
    user = {"id": "u-1", "email": _ADDR, "role": "owner"}
    body = lessons.LessonStatusUpdate(status="retired")
    with patch.object(lessons, "set_lesson_status", new=AsyncMock(return_value={"id": "L1", "status": "retired"})), \
         patch.object(lessons, "invalidate_lesson_cache", new=MagicMock()), \
         caplog.at_level("INFO"):
        await lessons.update_lesson_status("L1", body, user)
    assert "by user=u-1" in _lines(caplog) and _ADDR not in _lines(caplog)


# ── a recipient is written masked ───────────────────────────────────────

@pytest.mark.asyncio
async def test_an_invite_that_was_sent_logs_the_recipient_masked(caplog):
    p, client = _postmark(200, '{"ErrorCode":0,"Message":"OK"}')
    with patch.object(settings, "postmark_token", "pm-test"), p, caplog.at_level("INFO"):
        assert await mail.send_invite_email(_ADDR, "Dana", "https://panel.test/set-password?token=x", 60) is True
    assert client.post.await_args.kwargs["json"]["To"] == _ADDR          # the mail still goes to the real address
    assert "@bbc.test" in _lines(caplog) and _ADDR not in _lines(caplog) and "popescu" not in _lines(caplog)


@pytest.mark.asyncio
async def test_an_ops_alert_logs_where_it_really_went_masked(caplog):
    p, client = _postmark(200, '{"ErrorCode":0,"Message":"OK"}')
    with patch.object(settings, "postmark_token", "pm-test"), \
         patch.object(settings, "ops_alert_email", _OPS), \
         patch.object(settings, "super_alert_email", "super-inbox@bbc.test"), \
         p, caplog.at_level("INFO"):
        assert await mail.send_ops_alert_email("DB executor saturated", "18/20") is True
    assert client.post.await_args.kwargs["json"]["To"] == _OPS
    said = _lines(caplog)
    assert "Sent ops alert email (DB executor saturated)" in said
    assert _OPS not in said and "super-inbox" not in said and "op" in said and "@bbc.test" in said


@pytest.mark.asyncio
async def test_an_attention_mail_logs_its_recipient_masked(caplog):
    p, _ = _postmark(200, '{"ErrorCode":0,"Message":"OK"}')
    with patch.object(settings, "postmark_token", "pm-test"), \
         patch.object(settings, "attention_email_to", _ADDR), \
         p, caplog.at_level("INFO"):
        assert await mail.send_attention_email(
            tag="returning_customer", chat_number=3301, created_at=None,
            customer_name="A Client", conversation_id="c-1",
        ) is True
    assert "Attention email sent for conv c-1" in _lines(caplog) and _ADDR not in _lines(caplog)


# ── what the provider answers is written without the addresses ──────────

@pytest.mark.asyncio
async def test_a_refused_invite_logs_the_code_not_the_address(caplog):
    refused = '{"ErrorCode":406,"Message":"You tried to send to recipient(s) that have been marked as inactive. Found inactive addresses: %s."}' % _ADDR
    p, _ = _postmark(422, refused)
    with patch.object(settings, "postmark_token", "pm-test"), p, caplog.at_level("INFO"):
        assert await mail.send_invite_email(_ADDR, "Dana", "https://panel.test/x", 60) is False
    said = _lines(caplog)
    assert "Postmark 422: code=406" in said and "inactive" in said
    assert _ADDR not in said and "<address>" in said


@pytest.mark.asyncio
async def test_an_answer_that_is_not_json_logs_the_status_alone(caplog):
    p, _ = _postmark(502, "<html>Bad gateway for %s</html>" % _ADDR)
    with patch.object(settings, "postmark_token", "pm-test"), \
         patch.object(settings, "super_alert_email", _OPS), p, caplog.at_level("INFO"):
        assert await mail.send_ops_alert_email("x", "y") is False
    said = _lines(caplog)
    assert "502 (no JSON body)" in said and _ADDR not in said and _OPS not in said


# ── an error while sending is written by its kind, not its text ─────────

def _postmark_raises(exc):
    """httpx.AsyncClient whose post raises: the mail never left."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=exc)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(mail.httpx, "AsyncClient", return_value=ctx)


async def _send_invite():
    return await mail.send_invite_email(_ADDR, "Dana", "https://panel.test/x", 60)


async def _send_attention():
    return await mail.send_attention_email(
        tag="returning_customer", chat_number=3301, created_at=None,
        customer_name="A Client", conversation_id="c-1",
    )


async def _send_ops_alert():
    return await mail.send_ops_alert_email("DB executor saturated", "18/20")


@pytest.mark.asyncio
@pytest.mark.parametrize("send, line", [
    (_send_invite, "send_invite_email error: LocalProtocolError"),
    (_send_attention, "Attention email exception: LocalProtocolError"),
    (_send_ops_alert, "Exception sending ops alert email (DB executor saturated): LocalProtocolError"),
])
async def test_an_error_while_sending_is_logged_by_its_kind(send, line, caplog):
    """An HTTP client's error can quote what it was sending: here the token
    it refused as a header, and an address. Neither reaches the log."""
    boom = httpx.LocalProtocolError("Illegal header value b'pm-secret-token\\n' while sending to %s" % _ADDR)
    with patch.object(settings, "postmark_token", "pm-secret-token\n"), \
         patch.object(settings, "attention_email_to", _ADDR), \
         patch.object(settings, "ops_alert_email", _OPS), \
         patch.object(settings, "super_alert_email", _OPS), \
         _postmark_raises(boom), caplog.at_level("INFO"):
        assert await send() is False
    said = _lines(caplog)
    assert line in said
    assert "pm-secret-token" not in said and _ADDR not in said and _OPS not in said and "Illegal header" not in said
