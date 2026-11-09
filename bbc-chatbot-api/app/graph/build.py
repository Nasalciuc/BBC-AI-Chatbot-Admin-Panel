"""The compiled turn graph (PR-5).

Flow control for a chat turn now lives in a LangGraph StateGraph, compiled once
at import as TURN_GRAPH. PR-5 is a strangler step with ZERO behaviour change:
the single `run_turn` node calls today's `_pipeline` unchanged, so the graph
and the legacy path produce identical results (proven by tests/test_graph_parity).

The node-by-node split (load → guard → classify/extract/kb → decide → slots →
route → generate → post → persist, III.1) lands in later PRs, once the owner
has run the parity eval on production data — the plan sequences the real
reorder into PR-7 and PR-9, each with its own tests.
"""

from __future__ import annotations

import logging

from langgraph.graph import StateGraph, START, END

from app.graph.state import TurnState

logger = logging.getLogger(__name__)


async def _run_turn_node(state: TurnState) -> dict:
    # Imported here, not at module load, to avoid an import cycle with the
    # orchestrator (which imports the engine).
    from app.pipeline.orchestrator import _pipeline

    response = await _pipeline(
        state.get("conversation_id"),
        state["message"],
        state["tunnel"],
        state["visitor"],
        state.get("metadata"),
        visitor_id=state.get("visitor_id"),
        _persist_state=state.get("persist_state") or {"ai_persisted": False},
        skip_user_save=bool(state.get("skip_user_save")),
    )
    return {"response": response, "events": ["run_turn"]}


def _build():
    g = StateGraph(TurnState)
    g.add_node("run_turn", _run_turn_node)
    g.add_edge(START, "run_turn")
    g.add_edge("run_turn", END)
    return g.compile()


# Compiled exactly once.
TURN_GRAPH = _build()
