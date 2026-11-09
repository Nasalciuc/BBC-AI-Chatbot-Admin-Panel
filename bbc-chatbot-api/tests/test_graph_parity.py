"""PR-5 — the LangGraph engine produces exactly what the legacy path does.

The graph's single node calls the same _pipeline, so parity is structural.
These tests prove the wiring: engine selection is stable per conversation, the
graph returns the identical response object, and langgraph telemetry is off.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.graph.engine import use_graph
from app.models.chat import ChatResponse, VisitorInfo


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


def test_langsmith_tracing_is_forced_off():
    import app.graph  # noqa: F401  (import runs the kill-switch)

    assert os.environ.get("LANGSMITH_TRACING") == "false"
    assert os.environ.get("LANGCHAIN_TRACING_V2") == "false"


def test_graph_compiles_once():
    from app.graph.build import TURN_GRAPH

    assert TURN_GRAPH is not None
    from app.graph import build

    assert build.TURN_GRAPH is TURN_GRAPH


def test_engine_selection():
    # default: legacy
    assert use_graph("conv-1") is False
    with patch.dict(os.environ, {"PIPELINE_ENGINE": "graph"}):
        assert use_graph("conv-1") is True
    with patch.dict(os.environ, {"PIPELINE_GRAPH_PERCENT": "100"}):
        assert use_graph("conv-1") is True
        assert use_graph(None) is False  # no id → legacy


def test_percent_is_stable_per_conversation():
    with patch.dict(os.environ, {"PIPELINE_GRAPH_PERCENT": "50"}):
        a = use_graph("conv-stable")
        b = use_graph("conv-stable")
        assert a == b


@pytest.mark.asyncio
async def test_graph_node_calls_pipeline_and_returns_its_response():
    sentinel = ChatResponse(conversation_id="c1", message="hi", type="ai", model_used="sonnet")

    async def _fake_pipeline(cid, message, tunnel, visitor, metadata, **kw):
        assert kw.get("_persist_state") is not None  # same contract
        return sentinel

    with patch("app.pipeline.orchestrator._pipeline", new=_fake_pipeline):
        from app.graph.engine import run_turn_graph

        out = await run_turn_graph("c1", "hello", "sales", VisitorInfo(name="A"))
    assert out is sentinel


@pytest.mark.asyncio
async def test_process_message_graph_and_legacy_agree():
    sentinel = ChatResponse(conversation_id="c1", message="same", type="ai", model_used="sonnet")

    async def _fake_pipeline(cid, message, tunnel, visitor, metadata, **kw):
        return sentinel

    from app.pipeline import orchestrator

    with patch.object(orchestrator, "_pipeline", new=_fake_pipeline):
        legacy = await orchestrator.process_message("c1", "hello", "sales", VisitorInfo(name="A"))
        with patch.dict(os.environ, {"PIPELINE_ENGINE": "graph"}):
            graph = await orchestrator.process_message("c1", "hello", "sales", VisitorInfo(name="A"))
    assert legacy.message == graph.message == "same"
    assert legacy.model_used == graph.model_used
