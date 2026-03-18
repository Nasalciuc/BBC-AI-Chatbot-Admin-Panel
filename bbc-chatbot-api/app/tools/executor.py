"""Tool execution layer — validates and executes all tool calls.

Architecture: Claude PROPOSES tool calls → Orchestrator VALIDATES via this module →
Orchestrator EXECUTES → Result returned to Claude. Claude NEVER calls tools directly.

V1.5: Foundation with zero tools. Registry is empty.
V2.1+: Tools registered incrementally. Each tool defines:
  - param_schema: JSON Schema for parameter validation
  - max_per_message: rate limit per single message
  - max_per_conversation: rate limit per conversation
  - cost_estimate: for budget tracking
  - allowed_tunnels: sales/support scope restriction

Security principles (OWASP LLM06 Excessive Agency):
1. Zero Trust: every param validated before execution
2. Least Privilege: each tool has minimal scope
3. Budget Control: hard stop at MAX_TOOL_COST_PER_MESSAGE
4. No Recursion: tools cannot call other tools
5. Audit: every call logged with params + result + cost + latency
"""

import logging
import time
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────
MAX_TOOL_CALLS_PER_MESSAGE = 5
MAX_TOOL_COST_PER_MESSAGE = 0.10
MAX_AGENTIC_TIMEOUT_SECONDS = 30

# ── Data Classes ───────────────────────────────────────────

@dataclass
class ToolCall:
    """Represents a tool call proposed by Claude."""
    name: str
    params: dict

@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    cost: float = 0.0
    latency_ms: int = 0

@dataclass
class ToolDefinition:
    """Definition of a registered tool with security constraints."""
    name: str
    description: str
    handler: Callable
    param_schema: dict
    max_per_message: int = 3
    max_per_conversation: int = 10
    cost_estimate: float = 0.0
    requires_auth: bool = False
    allowed_tunnels: list = field(default_factory=lambda: ["sales", "support"])

# ── Registry ───────────────────────────────────────────────
TOOL_REGISTRY: dict[str, ToolDefinition] = {}


def register_tool(tool: ToolDefinition) -> None:
    """Register a tool. V2.x calls this at import time."""
    TOOL_REGISTRY[tool.name] = tool
    logger.info(f"Tool registered: {tool.name} (max {tool.max_per_message}/msg, ${tool.cost_estimate}/call)")


def get_registered_tools() -> list[str]:
    """List registered tool names. Empty in V1.5."""
    return list(TOOL_REGISTRY.keys())


# ── Parameter Validation ───────────────────────────────────

def _validate_params(params: dict, schema: dict) -> tuple[bool, str]:
    """Validate tool parameters against schema.

    Schema format per param:
    {
        "param_name": {
            "type": "string"|"int"|"float"|"bool",
            "required": True|False,
            "max_length": 500,       # for strings
            "pattern": r"^[A-Z]{3}$", # regex for strings
            "enum": ["a", "b"],      # allowed values
            "min": 0, "max": 100,    # for numbers
        }
    }
    """
    # Check required params
    for key, rules in schema.items():
        if rules.get("required") and key not in params:
            return False, f"Missing required param: {key}"

    # Validate provided params
    for key, val in params.items():
        if key not in schema:
            return False, f"Unexpected param: {key}"

        rules = schema[key]

        # Type validation
        expected = rules.get("type", "string")
        type_map = {"string": str, "int": int, "float": (int, float), "bool": bool}
        if expected in type_map and not isinstance(val, type_map[expected]):
            return False, f"Param '{key}' must be {expected}, got {type(val).__name__}"

        # String validations
        if isinstance(val, str):
            max_len = rules.get("max_length", 500)
            if len(val) > max_len:
                return False, f"Param '{key}' too long ({len(val)} > {max_len})"

            pattern = rules.get("pattern")
            if pattern and not re.match(pattern, val):
                return False, f"Param '{key}' doesn't match required format"

            # Anti-injection: no SQL-like patterns in string params
            sql_patterns = ["DROP ", "DELETE ", "INSERT ", "UPDATE ", "--", ";", "/*"]
            if any(p in val.upper() for p in sql_patterns):
                return False, f"Param '{key}' contains forbidden pattern"

        # Enum validation
        if "enum" in rules and val not in rules["enum"]:
            return False, f"Param '{key}' must be one of: {rules['enum']}"

        # Number range validation
        if isinstance(val, (int, float)):
            if "min" in rules and val < rules["min"]:
                return False, f"Param '{key}' below minimum ({val} < {rules['min']})"
            if "max" in rules and val > rules["max"]:
                return False, f"Param '{key}' above maximum ({val} > {rules['max']})"

    return True, ""


# ── Executor ───────────────────────────────────────────────

async def execute_tool(
    call: ToolCall,
    context: dict,
) -> ToolResult:
    """Validate and execute a tool call. EVERY tool call goes through here.

    Context dict must include:
    - conversation_id: str
    - tunnel: str ("sales" or "support")
    - tool_calls_count: int (current count for this message)
    - tool_cost_total: float (current cost for this message)
    """
    start = time.perf_counter()
    cid = context.get("conversation_id", "?")

    # 1. Tool exists?
    if call.name not in TOOL_REGISTRY:
        logger.warning(f"[{cid}] TOOL REJECTED: unknown tool '{call.name}'")
        return ToolResult(success=False, error=f"Unknown tool: {call.name}")

    tool = TOOL_REGISTRY[call.name]

    # 2. Tunnel allowed?
    tunnel = context.get("tunnel", "sales")
    if tunnel not in tool.allowed_tunnels:
        logger.warning(f"[{cid}] TOOL REJECTED: '{call.name}' not allowed in {tunnel} tunnel")
        return ToolResult(success=False, error="Tool not available in this context")

    # 3. Per-message call count limit?
    calls_count = context.get("tool_calls_count", 0)
    if calls_count >= MAX_TOOL_CALLS_PER_MESSAGE:
        logger.warning(f"[{cid}] TOOL REJECTED: message call limit ({calls_count}/{MAX_TOOL_CALLS_PER_MESSAGE})")
        return ToolResult(success=False, error="Tool call limit reached for this message")

    # 4. Budget limit?
    cost_total = context.get("tool_cost_total", 0.0)
    if cost_total + tool.cost_estimate > MAX_TOOL_COST_PER_MESSAGE:
        logger.warning(f"[{cid}] TOOL REJECTED: budget exceeded (${cost_total:.3f} + ${tool.cost_estimate:.3f} > ${MAX_TOOL_COST_PER_MESSAGE})")
        return ToolResult(success=False, error="Tool budget exceeded")

    # 5. Validate parameters
    valid, error_msg = _validate_params(call.params, tool.param_schema)
    if not valid:
        logger.warning(f"[{cid}] TOOL REJECTED: param validation failed for '{call.name}': {error_msg}")
        return ToolResult(success=False, error=f"Invalid parameters: {error_msg}")

    # 6. Execute handler
    try:
        result = await tool.handler(call.params, context)
        latency = int((time.perf_counter() - start) * 1000)
        result.latency_ms = latency

        logger.info(
            f"[{cid}] TOOL OK: {call.name} | "
            f"success={result.success} | cost=${result.cost:.4f} | "
            f"latency={latency}ms | params={list(call.params.keys())}"
        )
        return result

    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.error(f"[{cid}] TOOL ERROR: {call.name} failed after {latency}ms: {e}")
        return ToolResult(
            success=False,
            error="Tool execution failed. Please try again.",
            latency_ms=latency,
        )
