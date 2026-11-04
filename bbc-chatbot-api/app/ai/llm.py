"""Single async Anthropic client for the graph era (PR-6.1).

The sync functions in claude.py keep serving the legacy pipeline; this is the
one async seam the graph nodes and batch jobs use. It owns the 5.5 family
rules (text from blocks by type, effort instead of sampling, cache TTL from the
router, refusal/max_tokens handling) and records every call through PR-2's
ledger, so nothing added later can bypass the cost ledger.

Not wired into the hot generation path in PR-6 — that path stays on the
cost-recorded sync calls routed by app/ai/router. closing-5.5 and the per-node
graph calls move here as the graph rollout proceeds.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from config.settings import settings
from app.ai.ledger import record_ai_call
from app.ai.prices import cost_from_usage
from app.ai.claude import _sampling_params

logger = logging.getLogger(__name__)

_client: Optional["anthropic.AsyncAnthropic"] = None


def get_async_client() -> "anthropic.AsyncAnthropic":
    global _client
    if _client is None:
        # Our own retries live in one place; the SDK does none.
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=0)
    return _client


@dataclass
class LLMResult:
    text: str
    tool_input: Optional[dict]
    usage: Any
    cost: float
    model: str
    effort: str
    stop_reason: Optional[str]
    request_id: Optional[str]


def _is_55(model: str) -> bool:
    return "-5-5" in model


def _text_from_blocks(content) -> str:
    parts = [getattr(b, "text", "") or "" for b in (content or []) if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def _tool_from_blocks(content) -> Optional[dict]:
    for b in (content or []):
        if getattr(b, "type", None) == "tool_use":
            raw = getattr(b, "input", None)
            return dict(raw) if isinstance(raw, dict) else None
    return None


def _build_params(model: str, system, messages, tools, effort: str, max_tokens: int, cache_ttl: str) -> dict:
    # cache_control with the router's TTL on the static system block.
    sys_param = system
    if isinstance(system, str) and system:
        sys_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": cache_ttl}}]
    params: dict = {"model": model, "system": sys_param, "messages": messages}
    if _is_55(model):
        params["max_tokens"] = max(max_tokens, 512)
        params["output_config"] = {"effort": effort}
    else:
        params["max_tokens"] = max_tokens
        # Same denylist as the sync path: claude-opus-4-8 answers 400 to a
        # non-default temperature, so the parameter is left out there.
        params.update(_sampling_params(model, temperature=0.6))
    if tools:
        params["tools"] = tools
    return params


async def call(
    task: str,
    *,
    model: str,
    system,
    messages: list,
    tools: Optional[list] = None,
    effort: str = "low",
    max_tokens: int = 1024,
    cache_ttl: str = "5m",
    on_chunk=None,
    conversation_id: Optional[str] = None,
) -> LLMResult:
    """One async call. Streams when on_chunk is given. Records cost always."""
    client = get_async_client()
    params = _build_params(model, system, messages, tools, effort, max_tokens, cache_ttl)
    start = time.time()
    try:
        if on_chunk is not None:
            text = ""
            async with client.messages.stream(**params) as stream:
                async for delta in stream.text_stream:
                    text += delta
                    if delta:
                        on_chunk(delta)
                final = await stream.get_final_message()
            usage, content = final.usage, final.content
            stop_reason = getattr(final, "stop_reason", None)
            request_id = getattr(final, "id", None)
            if not text:
                text = _text_from_blocks(content)
        else:
            resp = await client.messages.create(**params)
            usage, content = resp.usage, resp.content
            stop_reason = getattr(resp, "stop_reason", None)
            request_id = getattr(resp, "id", None)
            text = _text_from_blocks(content)
    except anthropic.APITimeoutError:
        record_ai_call(task=task, model=model, cost=0.0, status="timeout", conversation_id=conversation_id)
        raise
    except Exception:
        record_ai_call(task=task, model=model, cost=0.0, status="error", conversation_id=conversation_id)
        raise

    status = "refusal" if stop_reason == "refusal" else "ok"
    cost = record_ai_call(
        task=task, model=model, usage=usage, status=status,
        latency_ms=int((time.time() - start) * 1000),
        request_id=request_id, conversation_id=conversation_id,
    )
    return LLMResult(
        text="" if status == "refusal" else text,
        tool_input=_tool_from_blocks(content),
        usage=usage, cost=cost, model=model, effort=effort,
        stop_reason=stop_reason, request_id=request_id,
    )
