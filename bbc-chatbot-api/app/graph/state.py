"""TurnState — what flows through the turn graph.

PR-5 keeps the pipeline intact and runs it inside one node, so the state only
carries the call's inputs and the response. The per-step fields (intent, slots,
route, …) arrive when the nodes are split out, after the owner's parity eval.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from app.models.chat import ChatResponse, VisitorInfo


class TurnState(TypedDict, total=False):
    conversation_id: Optional[str]
    message: str
    tunnel: str
    visitor: VisitorInfo
    metadata: Optional[dict]
    visitor_id: Optional[str]
    skip_user_save: bool
    persist_state: dict
    response: Optional[ChatResponse]
    events: list[Any]
