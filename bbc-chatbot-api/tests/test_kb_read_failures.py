"""Knowledge base reads: a failed read is reported, never an empty list.

The agents' Knowledge page shows "No answers here yet" for an empty list and
"Couldn't load the answers · Try again" for an error. When the database read
failed, the old code returned [] with success: true, so an outage looked like
an empty knowledge base and the page offered no retry.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import kb  # noqa: E402
from app.db import supabase as db  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(kb.router, prefix="/api")
    return TestClient(app)


def _broken_client():
    client = MagicMock()
    client.table.side_effect = RuntimeError("connection reset")
    return client


@pytest.mark.parametrize("fn", ["get_kb_entries", "get_kb_categories"])
def test_a_failed_read_raises_instead_of_returning_an_empty_list(fn):
    import asyncio

    with patch.object(db, "get_client", return_value=_broken_client()):
        with pytest.raises(RuntimeError):
            asyncio.run(getattr(db, fn)())


@pytest.mark.parametrize("path,fn", [("/api/kb/entries", "get_kb_entries"), ("/api/kb/categories", "get_kb_categories")])
def test_the_route_reports_the_failure(path, fn):
    with patch.object(db, fn, AsyncMock(side_effect=RuntimeError("connection reset"))):
        body = _client().get(path).json()
    assert body["success"] is False
    assert body["data"] == [] and body["count"] == 0
    # The client gets a plain sentence, not the database's internals.
    assert body["error"] == "Couldn't read the knowledge base"


@pytest.mark.parametrize("path,fn", [("/api/kb/entries", "get_kb_entries"), ("/api/kb/categories", "get_kb_categories")])
def test_an_empty_knowledge_base_is_still_a_success(path, fn):
    with patch.object(db, fn, AsyncMock(return_value=[])):
        body = _client().get(path).json()
    assert body == {"success": True, "data": [], "count": 0}


def test_sync_reports_a_failed_read_instead_of_syncing_nothing():
    with patch.object(db, "get_kb_entries", AsyncMock(side_effect=RuntimeError("connection reset"))):
        body = _client().post("/api/kb/sync").json()
    assert body["success"] is False
