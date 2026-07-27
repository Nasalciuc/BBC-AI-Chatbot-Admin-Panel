"""Abuse blocklist — matching policy, normalization, endpoints.

The critical guarantee under test: a blocked IP NEVER refuses anyone on its own.
IPs are shared (offices, hotels, carriers), so an IP-only rule would block
innocent visitors. Only phone/email refuse; the IP row exists for audit.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import blocklist as bl
from app.security.auth import get_current_user
from config.settings import settings


# ── Fake blocklist store: emulates the DB layer's active-entry semantics ──


class FakeStore:
    """(kind, value) -> expires_at (None = permanent). Values pre-normalized."""

    def __init__(self):
        self.rows: dict[tuple[str, str], str | None] = {}

    async def has_active(self, kind, value):
        if (kind, value) not in self.rows:
            return False
        exp = self.rows[(kind, value)]
        if exp is None:
            return True
        return exp > datetime.now(timezone.utc).isoformat()

    async def upsert(self, kind, value, *, reason=None, blocked_by=None,
                     conversation_id=None, expires_at=None):
        self.rows[(kind, value)] = expires_at
        return True

    async def delete(self, kind, value):
        return self.rows.pop((kind, value), "missing") != "missing"


@pytest.fixture
def store():
    s = FakeStore()
    with (
        patch.object(bl.db, "blocklist_has_active", s.has_active),
        patch.object(bl.db, "blocklist_upsert", s.upsert),
        patch.object(bl.db, "blocklist_delete", s.delete),
    ):
        yield s


def _conv(ip="203.0.113.7", phone="+1 844-770-4910", email="Bhai@X.com"):
    return {
        "id": "conv-1",
        "visitor_phone": phone,
        "visitor_email": email,
        "metadata": {"client_ip": ip} if ip else {},
    }


# ── 1-2. Normalization ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phone_normalization_matches_any_format(store):
    store.rows[("phone", "18447704910")] = None
    assert await bl.is_blocked(phone="18447704910") is True
    assert await bl.is_blocked(phone="+1 844-770-4910") is True
    assert await bl.is_blocked(phone="+1 (844) 770 4910") is True


@pytest.mark.asyncio
async def test_email_normalization_is_case_insensitive(store):
    store.rows[("email", "bhai@x.com")] = None
    assert await bl.is_blocked(email="bhai@x.com") is True
    assert await bl.is_blocked(email="Bhai@X.com") is True
    assert await bl.is_blocked(email="  BHAI@X.COM  ") is True


# ── 3-4. Phone alone / email alone block ────────────────────────────


@pytest.mark.asyncio
async def test_phone_alone_blocks(store):
    store.rows[("phone", "18447704910")] = None
    assert await bl.is_blocked(phone="+18447704910") is True


@pytest.mark.asyncio
async def test_email_alone_blocks(store):
    store.rows[("email", "bhai@x.com")] = None
    assert await bl.is_blocked(email="bhai@x.com") is True


# ── 5-6. THE SAFETY TESTS: IP never blocks alone ────────────────────


@pytest.mark.asyncio
async def test_ip_alone_does_not_block(store):
    """Core safety guarantee: shared IP must not refuse an innocent visitor."""
    store.rows[("ip", "203.0.113.7")] = None
    assert await bl.is_blocked(ip="203.0.113.7") is False


@pytest.mark.asyncio
async def test_blocked_ip_with_clean_contact_does_not_block(store):
    """Someone else behind the abuser's office IP is never caught."""
    store.rows[("ip", "203.0.113.7")] = None
    store.rows[("phone", "18447704910")] = None
    store.rows[("email", "bhai@x.com")] = None
    assert await bl.is_blocked(
        ip="203.0.113.7",
        phone="+1 202 555 0100",
        email="innocent@example.com",
    ) is False


# ── 7. Blocked phone refuses at the chat chokepoint ─────────────────


@pytest.mark.asyncio
async def test_blocked_visitor_refused_at_chat_init(store):
    from app.api.chat import chat_init, ChatInitRequest
    from app.models.chat import VisitorInfo
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    store.rows[("phone", "18447704910")] = None

    req = MagicMock()
    req.headers = {"cf-connecting-ip": "203.0.113.7"}
    req.client = MagicMock(host="203.0.113.7")

    payload = ChatInitRequest(
        visitor=VisitorInfo(name="Bhai", phone="+1 844-770-4910", email="bhai@x.com")
    )

    create = AsyncMock()
    with patch("app.services.conversation_service.get_or_create_conversation", create):
        with pytest.raises(HTTPException) as exc:
            await chat_init(payload, req)

    assert exc.value.status_code == 403
    assert "unable to process" in exc.value.detail.lower()
    create.assert_not_called()  # no conversation created for a blocked visitor


@pytest.mark.asyncio
async def test_clean_visitor_not_refused_at_chat_init(store):
    """Regression: an unblocked visitor proceeds exactly as before."""
    from app.api.chat import chat_init, ChatInitRequest
    from app.models.chat import VisitorInfo
    from unittest.mock import MagicMock

    store.rows[("phone", "18447704910")] = None  # a DIFFERENT person is blocked

    req = MagicMock()
    req.headers = {"cf-connecting-ip": "198.51.100.9"}
    req.client = MagicMock(host="198.51.100.9")

    payload = ChatInitRequest(
        visitor=VisitorInfo(name="Clean", phone="+1 202 555 0100", email="ok@example.com")
    )

    create = AsyncMock(return_value={"id": "conv-new"})
    with patch("app.services.conversation_service.get_or_create_conversation", create):
        resp = await chat_init(payload, req)

    assert resp.conversation_id == "conv-new"
    create.assert_called_once()


# ── 8-9. Expiry semantics ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_entry_is_ignored(store):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store.rows[("phone", "18447704910")] = past
    assert await bl.is_blocked(phone="+18447704910") is False


@pytest.mark.asyncio
async def test_permanent_entry_still_matches(store):
    store.rows[("email", "bhai@x.com")] = None
    assert await bl.is_blocked(email="bhai@x.com") is True


@pytest.mark.asyncio
async def test_future_expiry_still_matches(store):
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    store.rows[("phone", "18447704910")] = future
    assert await bl.is_blocked(phone="+18447704910") is True


# ── 10. block_from_conversation records all three kinds ─────────────


@pytest.mark.asyncio
async def test_block_from_conversation_records_all_three(store):
    kinds = await bl.block_from_conversation(_conv(), blocked_by_id="admin-1", reason="scammer")
    assert set(kinds) == {"ip", "phone", "email"}

    # normalized values stored
    assert ("phone", "18447704910") in store.rows
    assert ("email", "bhai@x.com") in store.rows
    assert ("ip", "203.0.113.7") in store.rows

    # phone/email permanent; IP expires
    assert store.rows[("phone", "18447704910")] is None
    assert store.rows[("email", "bhai@x.com")] is None
    assert store.rows[("ip", "203.0.113.7")] is not None

    # ...and the recorded IP still does not block on its own
    assert await bl.is_blocked(ip="203.0.113.7") is False
    # ...while the phone does
    assert await bl.is_blocked(phone="+1 844-770-4910") is True


@pytest.mark.asyncio
async def test_block_from_conversation_partial_data(store):
    kinds = await bl.block_from_conversation(
        _conv(ip=None, phone=None, email="only@mail.com")
    )
    assert kinds == ["email"]


# ── 12. Unblock removes entries ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unblock_removes_entries(store):
    conv = _conv()
    await bl.block_from_conversation(conv, blocked_by_id="admin-1")
    assert await bl.is_blocked(phone="+1 844-770-4910") is True

    removed = await bl.unblock_from_conversation(conv)
    assert set(removed) == {"ip", "phone", "email"}
    assert await bl.is_blocked(phone="+1 844-770-4910") is False
    assert await bl.is_blocked(email="bhai@x.com") is False


# ── 13. Clean visitor unaffected ────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_visitor_never_blocked(store):
    store.rows[("phone", "18447704910")] = None
    store.rows[("email", "bhai@x.com")] = None
    store.rows[("ip", "203.0.113.7")] = None
    assert await bl.is_blocked(
        ip="198.51.100.9", phone="+1 202 555 0100", email="clean@example.com"
    ) is False


@pytest.mark.asyncio
async def test_no_identifiers_never_blocks(store):
    store.rows[("ip", "203.0.113.7")] = None
    assert await bl.is_blocked() is False
    assert await bl.is_blocked(ip="203.0.113.7", phone="", email="") is False


# ── 11. Role gate on the endpoints ──────────────────────────────────

PRIVILEGED = ["owner", "admin", "dev", "supervisor", "project_manager"]
NOT_PRIVILEGED = ["sales", "support", "qa"]


def _client(role: str):
    """Mounts the conversations router under /api, exactly as main.py does."""
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u-1", "email": f"{role}@bbc.com", "role": role,
        "tunnel_scope": "all",
    }
    return TestClient(app)


@pytest.mark.parametrize("role", PRIVILEGED)
def test_privileged_roles_can_block(role, store):
    with (
        patch.object(bl.db, "blocklist_has_active", store.has_active),
        patch.object(bl.db, "blocklist_upsert", store.upsert),
        patch("app.api.conversations.db.get_conversation_simple",
              AsyncMock(return_value=_conv())),
    ):
        resp = _client(role).post("/api/conversations/conv-1/block", json={})
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["blocked"]) == {"ip", "phone", "email"}


@pytest.mark.parametrize("role", NOT_PRIVILEGED)
def test_non_privileged_roles_cannot_block(role):
    resp = _client(role).post("/api/conversations/conv-1/block", json={})
    assert resp.status_code == 403


@pytest.mark.parametrize("role", NOT_PRIVILEGED)
def test_non_privileged_roles_cannot_unblock(role):
    resp = _client(role).post("/api/conversations/conv-1/unblock")
    assert resp.status_code == 403


def test_unblock_endpoint_removes(store):
    conv = _conv()
    with (
        patch.object(bl.db, "blocklist_upsert", store.upsert),
        patch.object(bl.db, "blocklist_delete", store.delete),
        patch.object(bl.db, "blocklist_has_active", store.has_active),
        patch("app.api.conversations.db.get_conversation_simple",
              AsyncMock(return_value=conv)),
    ):
        c = _client("admin")
        c.post("/api/conversations/conv-1/block", json={})
        resp = c.post("/api/conversations/conv-1/unblock")
    assert resp.status_code == 200
    assert set(resp.json()["unblocked"]) == {"ip", "phone", "email"}


def test_block_unknown_conversation_404():
    with patch("app.api.conversations.db.get_conversation_simple",
               AsyncMock(return_value=None)):
        resp = _client("admin").post("/api/conversations/nope/block", json={})
    assert resp.status_code == 404


def test_block_nothing_to_block_400(store):
    empty = {"id": "c", "visitor_phone": None, "visitor_email": None, "metadata": {}}
    with (
        patch.object(bl.db, "blocklist_upsert", store.upsert),
        patch("app.api.conversations.db.get_conversation_simple",
              AsyncMock(return_value=empty)),
    ):
        resp = _client("admin").post("/api/conversations/c/block", json={})
    assert resp.status_code == 400


def test_ip_ttl_setting_default():
    assert settings.blocklist_ip_ttl_days == 30
