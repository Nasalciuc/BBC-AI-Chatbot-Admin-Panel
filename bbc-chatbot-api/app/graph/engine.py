"""Engine selection for a chat turn (PR-5)."""

from __future__ import annotations

import logging
from typing import Optional

from app.experiments import in_arm
from app.flags import flag
from app.models.chat import ChatResponse, VisitorInfo

logger = logging.getLogger(__name__)


def use_graph(conversation_id: Optional[str]) -> bool:
    """True when this turn should run on the LangGraph engine.

    PIPELINE_ENGINE=graph forces it on; =legacy forces it off. Otherwise
    PIPELINE_GRAPH_PERCENT routes a stable bucket of conversations (R11) so a
    conversation never switches engine mid-way. No conversation id yet → legacy.
    """
    engine = str(flag("PIPELINE_ENGINE", "legacy")).strip().lower()
    if engine == "graph":
        return True
    if engine not in ("legacy", ""):
        logger.warning(f"[graph] unknown PIPELINE_ENGINE={engine!r} — using legacy")
    percent = int(flag("PIPELINE_GRAPH_PERCENT", 0))
    if percent <= 0 or not conversation_id:
        return False
    return in_arm("pipeline_graph", conversation_id, percent)


async def run_turn_graph(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict] = None,
    visitor_id: Optional[str] = None,
    persist_state: Optional[dict] = None,
    skip_user_save: bool = False,
) -> ChatResponse:
    """Run one turn through TURN_GRAPH. Same contract as _pipeline."""
    from app.graph.build import TURN_GRAPH

    out = await TURN_GRAPH.ainvoke({
        "conversation_id": conversation_id,
        "message": message,
        "tunnel": tunnel,
        "visitor": visitor,
        "metadata": metadata,
        "visitor_id": visitor_id,
        "skip_user_save": skip_user_save,
        "persist_state": persist_state or {"ai_persisted": False},
        "events": [],
    })
    return out["response"]
