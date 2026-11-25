import pytest
from unittest.mock import AsyncMock, patch

pytest_plugins = ["tests.plugins.timetravel"]


@pytest.fixture(autouse=True)
def _token_version_reads_zero(monkeypatch):
    """There is no database in the unit suite. A token_version read that fails
    now fails closed (401), so every test that is not about token_version gets
    the stored version a fresh user has: 0. Tests of the read itself patch
    `_read` again and win."""
    from app.security import token_version

    real = token_version._read

    def _zero(user_id):
        return 0

    _zero.real = real  # the unpatched read, for the tests of the read itself
    monkeypatch.setattr(token_version, "_read", _zero)
    yield


@pytest.fixture(autouse=True)
def _no_shared_redis_or_presence_between_tests():
    """The shared Redis client and the heartbeat's last-write memory live for the
    process; a test starts with neither (no Redis, every beat writes)."""
    from app.infra import redis_client
    from app.services import agent_presence

    redis_client.set_client_for_tests(None)
    agent_presence._reset_for_tests()
    yield
    redis_client.set_client_for_tests(None)
    agent_presence._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_contact_in_crm_by_default():
    """The automatic CRM pushes ask "is this human already in the CRM?"
    before pushing. Tests have no database: the default answer is "nobody"
    ([]); a test that needs another answer patches get_crm_contact_rows
    itself (inner patches win)."""
    with patch(
        "app.db.supabase.get_crm_contact_rows",
        new=AsyncMock(return_value=[]),
    ), patch(
        # The same question for the agent's button receipts, and the one asked
        # again right before an automatic submit: "nothing sent" by default.
        "app.db.supabase.get_button_lead_contact_rows",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.db.supabase.get_crm_receipts",
        new=AsyncMock(return_value={"crm_lead": None, "created_in_crm": False, "crm_lead_id": None}),
    ):
        yield


async def _plain_write(conversation_id, payload, receipt_keys=()):
    from app.db import supabase as sb

    return await sb.update_conversation(conversation_id, payload)


async def _plain_close(conversation_id, payload):
    from app.db import supabase as sb

    await sb.update_conversation(conversation_id, payload)
    return "closed", ""


@pytest.fixture(autouse=True)
def _handoff_writes_without_a_database():
    """Without a database the receipt guard of a new cycle's write has nothing to guard: the write is the plain
    update_conversation, which tests patch and read as before (inner patches win). The abandoned sweep's
    conditional close is the plain update too. The deadline sweep finds no Take to restore a lost clock from.
    Tests of the real functions capture them before this runs."""
    with patch("app.db.supabase.update_conversation_keeping_receipts", new=_plain_write), \
            patch("app.db.supabase.close_conversation_if_unowned", new=_plain_close), \
            patch("app.db.supabase.get_last_take", new=AsyncMock(return_value=("none", None))):
        yield
