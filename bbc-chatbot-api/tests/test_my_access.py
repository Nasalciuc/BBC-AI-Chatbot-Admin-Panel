"""GET /api/auth/me — your access, as the database has it now.

Settings › Profile shows it. The session token keeps the role it was issued
with for 24 h, so the page reads the row and can say when they differ.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

@pytest.mark.asyncio
async def test_my_access_reads_the_row_not_the_token():
    from unittest.mock import AsyncMock

    from app.api import auth_routes
    from app.db import supabase as sb

    row = {"id": "u1", "role": "support", "tunnel_scope": "support", "is_active": True,
           "chat_enabled": True, "team_id": "t1", "email": "ana@bbc.com", "phone": "+37369"}
    team = {"id": "t1", "name": "Day shift", "shift_name": "Day", "shift_start": "12:00:00",
            "shift_end": "20:00:00", "supervisor_id": "s1", "pm_id": "p1"}
    sup = {"id": "s1", "name": "Victor Rusu", "email": "victor@bbc.com", "phone": "+3737"}
    users = {"u1": row, "s1": sup}
    with patch.object(sb, "get_user_by_id", new=AsyncMock(side_effect=lambda i: users.get(i))), \
         patch.object(sb, "get_team", new=AsyncMock(return_value=team)), \
         patch.object(sb, "get_teams", new=AsyncMock()) as led:
        out = await auth_routes.get_own_access({"id": "u1", "role": "sales"})

    d = out["data"]
    assert d["role"] == "support" and d["session_role"] == "sales"   # changed since sign-in
    assert d["receives_chats"] is True
    assert d["team"] == {"id": "t1", "name": "Day shift", "shift_name": "Day", "shift_start": "12:00:00",
                         "shift_end": "20:00:00", "supervisor_name": "Victor Rusu"}
    # Only the supervisor's name travels — never their e-mail or phone.
    assert "victor@bbc.com" not in str(d) and "+3737" not in str(d)
    led.assert_not_awaited()                                           # not a supervisor


@pytest.mark.asyncio
@pytest.mark.parametrize("row, receives", [
    ({"role": "sales", "is_active": True, "chat_enabled": False}, False),   # chats switched off
    ({"role": "sales", "is_active": False}, False),                        # deactivated
    ({"role": "qa"}, False),                                               # reads, never answers
    ({"role": "sales"}, True),                                             # no chat_enabled column
])
async def test_who_receives_chats(row, receives):
    from unittest.mock import AsyncMock

    from app.api import auth_routes
    from app.db import supabase as sb

    with patch.object(sb, "get_user_by_id", new=AsyncMock(return_value={"id": "u1", **row})), \
         patch.object(sb, "get_teams", new=AsyncMock(return_value=[])):
        out = await auth_routes.get_own_access({"id": "u1", "role": row["role"]})
    assert out["data"]["receives_chats"] is receives
    assert out["data"]["team"] is None


@pytest.mark.asyncio
async def test_a_supervisor_sees_the_teams_they_lead():
    from unittest.mock import AsyncMock

    from app.api import auth_routes
    from app.db import supabase as sb

    with patch.object(sb, "get_user_by_id", new=AsyncMock(return_value={"id": "s1", "role": "supervisor"})), \
         patch.object(sb, "get_teams", new=AsyncMock(return_value=[{"id": "t1", "name": "Test group"}])) as led:
        out = await auth_routes.get_own_access({"id": "s1", "role": "supervisor"})
    assert led.await_args.kwargs == {"is_active": True, "supervisor_id": "s1"}
    assert [t["name"] for t in out["data"]["leads_teams"]] == ["Test group"]


@pytest.mark.asyncio
async def test_an_unknown_caller_gets_404_not_someone_else():
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.api import auth_routes
    from app.db import supabase as sb

    with patch.object(sb, "get_user_by_id", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await auth_routes.get_own_access({"id": "ghost", "role": "sales"})
    assert exc.value.status_code == 404
