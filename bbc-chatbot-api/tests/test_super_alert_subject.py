"""Super-alert emails: unique per-chat subject + chat number identity.

Gmail threads by subject — the old static subject collapsed alerts from
DIFFERENT chats into one ever-growing thread (9+ alerts, one thread,
new chats drowning under old ones). A unique subject per chat makes that
impossible by construction; the number leads the body so collapsed rows
are distinguishable in the Gmail list preview. The per-conversation
claim/cooldown dedup (claim_super_alert) was always fine and is untouched.
"""

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services.email import send_super_alert_email

_CONV_A = "abcd1234-1111-2222-3333-444444444444"
_CONV_B = "efgh5678-5555-6666-7777-888888888888"

_VISITOR_FIXTURE = {
    "visitor_name": "Sheniquia Testperson",
    "visitor_phone": "+15559876543",
    "visitor_email": "sheniquia@example.com",
}


async def _send_and_capture(**kwargs):
    """Run send_super_alert_email with Postmark mocked; return the payload."""
    post = AsyncMock(return_value=MagicMock(status_code=200, text=""))
    client = MagicMock()
    client.post = post
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    defaults = {
        "conversation_id": _CONV_A,
        "tunnel": "sales",
        "last_message": "discount business ticket to sydney",
        **_VISITOR_FIXTURE,
    }
    defaults.update(kwargs)

    with patch("app.services.email.settings") as s, \
         patch("app.services.email.httpx.AsyncClient", return_value=cm):
        s.postmark_token = "test-token"
        s.email_from = "alerts@example.com"
        s.super_alert_email = "super@example.com"
        s.admin_panel_url = "https://panel.example.com"
        ok = await send_super_alert_email(**defaults)

    assert ok is True
    return post.call_args.kwargs["json"]


class TestSubjectIdentity:
    @pytest.mark.asyncio
    async def test_subject_carries_chat_number_and_tunnel(self):
        payload = await _send_and_capture(chat_number=1042)
        assert payload["Subject"] == "Chat #1042 waiting — no agents online (sales)"

    @pytest.mark.asyncio
    async def test_fallback_short_conv_id_when_number_missing(self):
        payload = await _send_and_capture(chat_number=None)
        assert payload["Subject"] == "Chat (abcd1234) waiting — no agents online (sales)"

    @pytest.mark.asyncio
    async def test_two_chats_never_share_a_subject(self):
        a = await _send_and_capture(conversation_id=_CONV_A, chat_number=1042)
        b = await _send_and_capture(conversation_id=_CONV_B, chat_number=1043)
        assert a["Subject"] != b["Subject"]
        # Even without numbers, the short-id fallback keeps subjects unique.
        a2 = await _send_and_capture(conversation_id=_CONV_A, chat_number=None)
        b2 = await _send_and_capture(conversation_id=_CONV_B, chat_number=None)
        assert a2["Subject"] != b2["Subject"]


class TestBodyIdentity:
    @pytest.mark.asyncio
    async def test_number_is_first_detail_line_in_both_variants(self):
        payload = await _send_and_capture(chat_number=1042)
        # Text: the number is the FIRST line — it lands in the Gmail preview.
        assert payload["TextBody"].splitlines()[0] == "Chat: #1042"
        # Html: the number detail precedes the tunnel detail.
        html = payload["HtmlBody"]
        assert "<strong>Chat:</strong> #1042" in html
        assert html.index("Chat:") < html.index("Tunnel:")

    @pytest.mark.asyncio
    async def test_no_pii_regression(self):
        payload = await _send_and_capture(chat_number=1042)
        rendered = payload["Subject"] + payload["HtmlBody"] + payload["TextBody"]
        for value in _VISITOR_FIXTURE.values():
            assert value not in rendered


class TestCallSite:
    def test_orchestrator_passes_chat_number_from_conversation_row(self):
        # The alert closure lives inline in the pipeline; the contract is
        # that it forwards the conversation row's chat_number.
        from app.pipeline.orchestrator import _pipeline

        src = inspect.getsource(_pipeline)
        assert 'chat_number=(conv or {}).get("chat_number")' in src

    @pytest.mark.asyncio
    async def test_signature_is_backward_safe(self):
        # Existing callers that don't pass chat_number (first-message alert in
        # chat.py) must keep working — the fallback subject stays unique.
        payload = await _send_and_capture()  # no chat_number kwarg at all
        assert payload["Subject"].startswith("Chat (abcd1234) waiting")
