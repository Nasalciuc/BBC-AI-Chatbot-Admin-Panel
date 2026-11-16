"""The keyword index on `tunnel` that every KB search needs.

Every search filters on `tunnel`, and Qdrant Cloud refuses a filter on a field
without an index: "Index required but not found for "tunnel" of one of the
following types: [keyword]" (HTTP 400, QDRANT_HEALTH.refused_because, 16 Nov
2026). No turn has had a KB entry since 29 May 2026. Every start now checks
that the index is there and creates it when it is not, in the background.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import qdrant  # noqa: E402

BASE = "https://q.invalid"


def _answer(method, path, status=200, body=None):
    return httpx.Response(status, json=body if body is not None else {"status": "ok"},
                          request=httpx.Request(method, BASE + path))


def _info(payload_schema):
    return _answer("GET", "/collections/kb_entries",
                   body={"result": {"status": "green", "points_count": 42, "payload_schema": payload_schema}})


@pytest.fixture(autouse=True)
def _health():
    before = dict(qdrant.QDRANT_HEALTH)
    qdrant.QDRANT_HEALTH["tunnel_index"] = None
    yield qdrant.QDRANT_HEALTH
    qdrant.QDRANT_HEALTH.clear()
    qdrant.QDRANT_HEALTH.update(before)


def _run(client):
    with patch.object(qdrant, "_get_client", return_value=client), \
         patch.object(qdrant.settings, "qdrant_collection", "kb_entries"):
        return qdrant._ensure_tunnel_index_sync()


class TestEnsureTunnelIndex:
    def test_already_there_nothing_is_created(self, _health):
        client = MagicMock()
        client.get.return_value = _info({"tunnel": {"data_type": "keyword", "points": 42}})
        assert _run(client) == "present"
        client.put.assert_not_called()
        assert _health["tunnel_index"] == "present"

    def test_missing_it_is_created_as_a_keyword_index(self, _health):
        client = MagicMock()
        client.get.return_value = _info({"category_id": {"data_type": "keyword", "points": 3}})
        client.put.return_value = _answer("PUT", "/collections/kb_entries/index",
                                          body={"result": {"operation_id": 7, "status": "completed"}, "status": "ok"})
        assert _run(client) == "created"
        client.get.assert_called_once_with("/collections/kb_entries")
        client.put.assert_called_once_with(
            "/collections/kb_entries/index", params={"wait": "true"},
            json={"field_name": "tunnel", "field_schema": "keyword"}, timeout=qdrant._INDEX_TIMEOUT,
        )
        assert _health["tunnel_index"] == "created"

    def test_a_collection_with_no_schema_at_all_gets_it_too(self):
        client = MagicMock()
        client.get.return_value = _answer("GET", "/collections/kb_entries", body={"result": {"status": "green"}})
        client.put.return_value = _answer("PUT", "/collections/kb_entries/index")
        assert _run(client) == "created"

    def test_an_index_of_another_kind_is_not_the_one_the_filter_needs(self):
        client = MagicMock()
        client.get.return_value = _info({"tunnel": {"data_type": "text", "points": 42}})
        client.put.return_value = _answer("PUT", "/collections/kb_entries/index")
        assert _run(client) == "created"
        assert client.put.call_args.kwargs["json"] == {"field_name": "tunnel", "field_schema": "keyword"}

    def test_a_refused_creation_is_said_by_its_status_and_never_raises(self, _health, caplog):
        client = MagicMock()
        client.get.return_value = _info({})
        client.put.return_value = _answer("PUT", "/collections/kb_entries/index", status=403,
                                          body={"status": {"error": "read-only api key kb_entries"}})
        with caplog.at_level("ERROR"):
            assert _run(client) == "failed (HTTPStatusError, HTTP 403)"
        assert _health["tunnel_index"] == "failed (HTTPStatusError, HTTP 403)"
        said = "\n".join(r.getMessage() for r in caplog.records)
        assert "keyword index on 'tunnel' not ensured (HTTPStatusError, status 403): KB searches stay refused" in said
        assert "read-only" not in said  # the kind and the status, not the answer's text

    def test_a_missing_collection_is_said_too(self):
        client = MagicMock()
        client.get.return_value = _answer("GET", "/collections/kb_entries", status=404,
                                          body={"status": {"error": "Not found: Collection `kb_entries` doesn't exist!"}})
        assert _run(client) == "failed (HTTPStatusError, HTTP 404)"
        client.put.assert_not_called()

    def test_qdrant_down_is_said_and_never_raises(self):
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("down")
        assert _run(client) == "failed (ConnectError)"

    def test_not_configured_asks_nothing(self, _health):
        with patch.object(qdrant, "_get_client", return_value=None):
            assert qdrant._ensure_tunnel_index_sync() == "not configured"
        assert _health["tunnel_index"] == "not configured"

    @pytest.mark.asyncio
    async def test_the_async_door_runs_it_off_the_loop(self):
        with patch.object(qdrant, "_ensure_tunnel_index_sync", return_value="present") as sync:
            assert await qdrant.ensure_tunnel_index() == "present"
        sync.assert_called_once_with()


class TestANewCollectionGetsTheIndex:
    def test_recreating_the_collection_creates_the_index_after_it(self):
        """scripts/embed_kb.py deletes and recreates the collection: the new one
        would have no index, and every search would be refused again."""
        client = MagicMock()
        order: list[str] = []
        client.delete.side_effect = lambda *a, **k: order.append("delete") or _answer("DELETE", "/collections/kb_entries")

        def put(path, **kwargs):
            order.append(f"put {path}")
            return _answer("PUT", path)

        client.put.side_effect = put
        client.get.side_effect = lambda *a, **k: order.append("get") or _info({})
        with patch.object(qdrant, "_get_client", return_value=client), \
             patch.object(qdrant.settings, "qdrant_collection", "kb_entries"):
            assert qdrant._ensure_collection_sync() is True
        assert order == ["delete", "put /collections/kb_entries", "get", "put /collections/kb_entries/index"]


class TestStartup:
    @pytest.mark.asyncio
    async def test_the_start_asks_for_the_index_and_does_not_wait_for_it(self):
        from app import flags as runtime_flags
        from app import main
        from app.ai import ledger

        gate = asyncio.Event()

        async def slow_qdrant():
            await gate.wait()
            return "present"

        saved = dict(main.app.state._state)
        try:
            with patch.object(runtime_flags, "refresh", AsyncMock()), \
                 patch.object(runtime_flags, "start_refresher", MagicMock()), \
                 patch.object(ledger, "start_flusher", MagicMock()), \
                 patch.object(ledger, "seed_budget_bounded", AsyncMock()), \
                 patch.object(main.settings, "internal_scheduler_enabled", False), \
                 patch("app.log_setup.run_flush_forever", AsyncMock()), \
                 patch.object(qdrant, "ensure_tunnel_index", side_effect=slow_qdrant):
                # Qdrant has not answered, and the start is over all the same.
                await asyncio.wait_for(main.startup(), timeout=5)
                task = main.app.state.qdrant_index_task
                assert not task.done()
                gate.set()
                assert await asyncio.wait_for(task, timeout=5) == "present"
        finally:
            main.app.state._state.clear()
            main.app.state._state.update(saved)

    def test_health_shows_what_the_start_found(self):
        assert "tunnel_index" in qdrant.QDRANT_HEALTH
