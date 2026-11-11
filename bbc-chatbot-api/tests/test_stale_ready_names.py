"""The auto-reset job says who by id, never by name.

11 Nov 2026, production logs: "[cron] Auto-reset is_ready: ['<first> <last>']",
five agents in one evening, and the same names again in the line the scheduler
writes with the job's result. It was the one place in the backend that logged
a person's name at INFO (rule 7: no e-mail, phone or name at INFO or above).
"""

import logging
import os
import re
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron  # noqa: E402
from app.services import scheduler as sched  # noqa: E402

_U1 = "aaaaaaaa-0000-0000-0000-000000000001"
_U2 = "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_auto_reset_says_who_by_id_not_by_name(caplog):
    stale = [{"id": _U1, "name": "Rosalind Example"}, {"id": _U2, "name": None}]
    leave = AsyncMock(return_value=0)
    with patch.object(cron.db, "reset_stale_ready_users", AsyncMock(return_value=stale)), \
            patch("app.services.tasks_service.leave_shift", leave), \
            caplog.at_level(logging.DEBUG):
        out = await cron.run_cleanup_stale_ready()

    assert out == {"reset": 2, "ids": [_U1, _U2]}
    # Their open tasks still move, one call per person, as before.
    assert [c.args[0] for c in leave.await_args_list] == [_U1, _U2]
    lines = [r.getMessage() for r in caplog.records]
    assert any("Auto-reset is_ready" in line and _U1 in line and _U2 in line for line in lines)
    for line in lines:
        assert "Rosalind" not in line and "Example" not in line, line
    # The scheduler writes this result at INFO when someone was reset.
    shown = str(sched._loggable("stale_ready", out))
    assert "Rosalind" not in shown and _U1 in shown


@pytest.mark.asyncio
async def test_nobody_to_reset_writes_no_line(caplog):
    leave = AsyncMock()
    with patch.object(cron.db, "reset_stale_ready_users", AsyncMock(return_value=[])), \
            patch("app.services.tasks_service.leave_shift", leave), \
            caplog.at_level(logging.DEBUG):
        out = await cron.run_cleanup_stale_ready()

    assert out == {"reset": 0, "ids": []}
    leave.assert_not_awaited()
    assert not [r for r in caplog.records if "Auto-reset" in r.getMessage()]
    # A run that reset nobody stays quiet in the scheduler's log.
    assert sched._QUIET_WHEN["stale_ready"](out) is True


def test_no_log_line_at_info_or_above_prints_a_persons_name():
    """One line, one logger call: a name put into an INFO/WARNING/ERROR message."""
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    named = re.compile(
        r"""logger\.(?:info|warning|error|critical|exception)\(.*"""
        r"""(?:visitor_name|agent_name|full_name|first_name|last_name|user_name|\.get\(["']name["']\)|\{names?\})"""
    )
    found = []
    for folder, _dirs, files in os.walk(app_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if named.search(line):
                        found.append(f"{os.path.relpath(path, app_dir)}:{n}: {line.strip()[:90]}")
    assert found == [], found
