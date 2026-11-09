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
def _no_contact_in_crm_by_default():
    """The automatic CRM pushes ask "is this human already in the CRM?"
    before pushing. Tests have no database: the default answer is "nobody"
    ([]); a test that needs another answer patches get_crm_contact_rows
    itself (inner patches win)."""
    with patch(
        "app.db.supabase.get_crm_contact_rows",
        new=AsyncMock(return_value=[]),
    ):
        yield
