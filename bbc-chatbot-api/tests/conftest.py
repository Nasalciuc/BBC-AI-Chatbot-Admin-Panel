pytest_plugins = ["tests.plugins.timetravel"]

import pytest
from unittest.mock import AsyncMock, patch


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
