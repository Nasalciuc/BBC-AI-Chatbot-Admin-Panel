"""Claude API client — Haiku + Sonnet + Opus. No abstraction, no factory."""

import logging
import time
from typing import Optional

import anthropic

from config.settings import settings
from app.ai.prompts import CLASSIFIER_PROMPT

logger = logging.getLogger(__name__)

# ── Client singleton ──────────────────────────────────────────
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ── Cost estimation ───────────────────────────────────────────
COSTS: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {
        "input": 1.0 / 1_000_000,
        "output": 5.0 / 1_000_000,
    },
    "claude-sonnet-4-20250514": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
    },
    "claude-sonnet-4-6": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
    },
    "claude-opus-4-8": {
        "input": 5.0 / 1_000_000,
        "output": 25.0 / 1_000_000,
    },
}

# Fallback rates per model family. Model ids are env-overridable, and an id
# missing from COSTS used to record $0.00 silently — a wrong-by-a-cent
# estimate beats an invisible spend.
FAMILY_COSTS: dict[str, dict[str, float]] = {
    "haiku": COSTS["claude-haiku-4-5-20251001"],
    "sonnet": COSTS["claude-sonnet-4-6"],
    "opus": COSTS["claude-opus-4-8"],
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = COSTS.get(model)
    if rates is None:
        rates = next(
            (r for family, r in FAMILY_COSTS.items() if family in model), None
        )
        if rates is None:
            logger.warning(f"No cost rates for model={model} — recording $0")
            rates = {"input": 0.0, "output": 0.0}
        else:
            logger.warning(f"No exact cost entry for model={model} — using family rates")
    return round(
        input_tokens * rates["input"] + output_tokens * rates["output"], 6
    )


# ── Per-tier response caps ────────────────────────────────────
# Sales answers were clipping at 200 tokens; the premium tiers get room to
# finish a thought.
HAIKU_TOOL_MAX_TOKENS = 200
SONNET_MAX_TOKENS = 400
OPUS_MAX_TOKENS = 450
# Offline synthesis, not a chat reply — it returns JSON, not two sentences.
LEARNING_MAX_TOKENS = 2500


def _build_system(system_prompt):
    """Convert system prompt to cached blocks if tuple (static, dynamic)."""
    if isinstance(system_prompt, tuple):
        static, dynamic = system_prompt
        blocks = [
            {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
        ]
        if dynamic and dynamic.strip():
            blocks.append({"type": "text", "text": dynamic})
        return blocks
    return system_prompt  # string — classify, summary, legacy


# ── API calls ─────────────────────────────────────────────────

def call_haiku(system_prompt, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Haiku (cheap, fast). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_haiku_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=150,
        temperature=0.3,
    )


def call_sonnet(system_prompt, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Sonnet (sales default). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_sonnet_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=SONNET_MAX_TOKENS,
        temperature=0.4,
    )


def call_opus(system_prompt, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Opus (premium turns). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_opus_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=OPUS_MAX_TOKENS,
        temperature=0.4,
    )


def call_sonnet_learning(system_prompt, user_message: str) -> tuple[Optional[str], float]:
    """Call Sonnet for offline pattern synthesis (the daily learning loop).

    Separate from call_sonnet because synthesis needs room the chat caps deny,
    and a colder temperature: this reads conversations, it does not talk to
    clients.
    """
    return _call_model(
        model=settings.claude_sonnet_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=LEARNING_MAX_TOKENS,
        temperature=0.2,
    )


def classify_intent(message: str) -> tuple[Optional[str], float]:
    """Classify message intent using Haiku. Returns (category_string, cost)."""
    text, cost = _call_model(
        model=settings.claude_haiku_model,
        system_prompt=CLASSIFIER_PROMPT,
        user_message=message,
        max_tokens=20,
        temperature=0,
    )
    return text, cost


def _call_model(
    model: str,
    system_prompt,
    user_message: str,
    max_tokens: int,
    temperature: float,
) -> tuple[Optional[str], float]:
    """Internal: make a single Claude API call with logging and 1 retry on timeout. Returns (text, cost)."""
    start = time.time()
    logger.info(f"Claude START | model={model} max_tokens={max_tokens}")

    for attempt in range(2):
        try:
            response = _get_client().messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=_build_system(system_prompt),
                messages=[{"role": "user", "content": user_message}],
                timeout=settings.claude_timeout,
            )

            elapsed = round(time.time() - start, 3)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = _estimate_cost(model, input_tokens, output_tokens)
            text = response.content[0].text.strip()

            logger.info(
                f"Claude SUCCESS | model={model} in={input_tokens} out={output_tokens} "
                f"cost=${cost:.6f} time={elapsed}s"
            )
            return text, cost

        except anthropic.APITimeoutError:
            logger.error(f"Claude timeout ({settings.claude_timeout}s) model={model} attempt={attempt + 1}/2")
            if attempt == 0:
                continue  # retry once
            return None, 0.0
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e} | model={model} | status={getattr(e, 'status_code', 'N/A')}")
            return None, 0.0
        except Exception as e:
            logger.error(f"Claude unexpected error: {type(e).__name__}: {e} | model={model}")
            return None, 0.0

    return None, 0.0


# ── Travel extraction tool ────────────────────────────────────
TRAVEL_TOOL = {
    "name": "save_travel_details",
    "description": (
        "IMPORTANT: Always write a helpful response to the customer AND save their travel details. Never call this tool without also responding. "
        "Save travel details from the customer's message. "
        "Call when customer mentions route, destination, dates, or trip type. Do NOT call on greetings, confirmations, or questions without travel details.\n"
        "RULES:\n"
        "1. CITIES: Convert to 3-letter IATA code. Handle typos: londn=LHR, dubei=DXB, millan=MXP, pariz=CDG. "
        "Handle slang: nyc=JFK, la=LAX, lon=LHR, chi=ORD, sf=SFO, bos=BOS, vegas=LAS.\n"
        "2. ORIGIN/DEST: First city mentioned=origin, second=destination. "
        "'jfk lhr' means origin=JFK dest=LHR. 'I am from London, fly to Dubai' means origin=LHR dest=DXB.\n"
        "3. CHANGED MIND: Use LAST mentioned value. 'from JFK no wait LAX' means origin=LAX. "
        "Signals: actually, no wait, I mean, sorry, not X but Y.\n"
        "4. IGNORE IRRELEVANT: 'friend works at JFK' — JFK is NOT origin. "
        "'last time I flew to London' — ignore past trips, extract only CURRENT request.\n"
        "5. TRIP TYPE: oneway/ow/single=one_way. roundtrip/rt/return=round_trip. "
        "If return date exists, use round_trip even if they said one way.\n"
        "6. DATES: Convert to YYYY-MM-DD. 'june 15'=2026-06-15. If too vague like 'next month', omit. "
        "Dates must be today or in the future — never return a past date; resolve a past-looking date "
        "to its next future occurrence. For numeric dates like 09/05 assume MM/DD unless the first "
        "number is > 12 (then DD/MM)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {
                "type": "string",
                "description": (
                    "3-letter IATA airport code for departure city (e.g. JFK, LHR, CDG, DXB). "
                    "Use the MAIN international airport."
                ),
            },
            "destination": {
                "type": "string",
                "description": (
                    "3-letter IATA airport code for arrival city (e.g. MXP for Milan, "
                    "FLR for Florence, NRT for Tokyo)."
                ),
            },
            "departure_date": {
                "type": "string",
                "description": "Departure date in YYYY-MM-DD format. Must be today or later — never a past date.",
            },
            "return_date": {
                "type": "string",
                "description": "Return date in YYYY-MM-DD format. Must be today or later. Omit if one-way.",
            },
            "trip_type": {
                "type": "string",
                "enum": ["one_way", "round_trip"],
                "description": "one_way or round_trip based on customer message.",
            },
        },
    },
}


def call_haiku_with_tools(
    system_prompt, user_message: str
) -> tuple[Optional[str], float, dict]:
    """Call Haiku with travel extraction tool. Returns (text, cost, entities)."""
    return _call_model_with_tools(
        settings.claude_haiku_model, system_prompt, user_message,
        max_tokens=HAIKU_TOOL_MAX_TOKENS, temperature=0.3,
    )


def call_sonnet_with_tools(
    system_prompt, user_message: str
) -> tuple[Optional[str], float, dict]:
    """Call Sonnet with travel extraction tool. Returns (text, cost, entities)."""
    return _call_model_with_tools(
        settings.claude_sonnet_model, system_prompt, user_message,
        max_tokens=SONNET_MAX_TOKENS, temperature=0.4,
    )


def call_opus_with_tools(
    system_prompt, user_message: str
) -> tuple[Optional[str], float, dict]:
    """Call Opus with travel extraction tool. Returns (text, cost, entities)."""
    return _call_model_with_tools(
        settings.claude_opus_model, system_prompt, user_message,
        max_tokens=OPUS_MAX_TOKENS, temperature=0.4,
    )


def _call_model_with_tools(
    model: str,
    system_prompt,
    user_message: str,
    max_tokens: int,
    temperature: float,
) -> tuple[Optional[str], float, dict]:
    """Internal: one generation call carrying the travel extraction tool.

    entities contains keys Claude extracted (origin, destination, etc.).
    Empty dict if Claude did not call the tool or on failure.
    """
    start = time.time()
    logger.info(f"Claude+Tool START | model={model}")

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=_build_system(system_prompt),
            messages=[{"role": "user", "content": user_message}],
            tools=[TRAVEL_TOOL],
            timeout=settings.claude_timeout,
        )

        elapsed = round(time.time() - start, 3)
        cost = _estimate_cost(
            model, response.usage.input_tokens, response.usage.output_tokens
        )

        text = ""
        tool_entities: dict = {}

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text = (getattr(block, "text", None) or "").strip()
            elif btype == "tool_use" and getattr(block, "name", None) == "save_travel_details":
                raw_in = getattr(block, "input", None) or {}
                tool_entities = dict(raw_in) if isinstance(raw_in, dict) else {}

        logger.info(
            f"Claude+Tool SUCCESS | text={len(text)}ch entities={list(tool_entities.keys())} "
            f"cost=${cost:.6f} time={elapsed}s"
        )
        return text, cost, tool_entities

    except anthropic.APITimeoutError:
        logger.warning(f"Claude+Tool timeout ({settings.claude_timeout}s) — retrying once")
        try:
            response = _get_client().messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=_build_system(system_prompt),
                messages=[{"role": "user", "content": user_message}],
                tools=[TRAVEL_TOOL],
                timeout=settings.claude_timeout,
            )
            elapsed = round(time.time() - start, 3)
            cost = _estimate_cost(
                model, response.usage.input_tokens, response.usage.output_tokens
            )
            text = ""
            tool_entities: dict = {}
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text = (getattr(block, "text", None) or "").strip()
                elif btype == "tool_use" and getattr(block, "name", None) == "save_travel_details":
                    raw_in = getattr(block, "input", None) or {}
                    tool_entities = dict(raw_in) if isinstance(raw_in, dict) else {}
            logger.info(f"Claude+Tool RETRY SUCCESS | time={elapsed}s")
            return text, cost, tool_entities
        except Exception as retry_err:
            logger.error(f"Claude+Tool retry also failed: {retry_err}")
            return None, 0.0, {}
    except anthropic.APIError as e:
        logger.error(
            f"Claude+Tool API error: {e} | status={getattr(e, 'status_code', 'N/A')}"
        )
        return None, 0.0, {}
    except Exception as e:
        logger.error(f"Claude+Tool error: {type(e).__name__}: {e}")
        return None, 0.0, {}


def stream_haiku_with_tools(
    system_prompt,
    user_message: str,
    on_chunk=None,
) -> tuple[Optional[str], float, dict]:
    """Stream Haiku with tool support. Calls on_chunk(text) per delta."""
    return _stream_model_with_tools(
        settings.claude_haiku_model, system_prompt, user_message,
        max_tokens=HAIKU_TOOL_MAX_TOKENS, temperature=0.3, on_chunk=on_chunk,
    )


def stream_sonnet_with_tools(
    system_prompt,
    user_message: str,
    on_chunk=None,
) -> tuple[Optional[str], float, dict]:
    """Stream Sonnet with tool support. Calls on_chunk(text) per delta."""
    return _stream_model_with_tools(
        settings.claude_sonnet_model, system_prompt, user_message,
        max_tokens=SONNET_MAX_TOKENS, temperature=0.4, on_chunk=on_chunk,
    )


def stream_opus_with_tools(
    system_prompt,
    user_message: str,
    on_chunk=None,
) -> tuple[Optional[str], float, dict]:
    """Stream Opus with tool support. Calls on_chunk(text) per delta."""
    return _stream_model_with_tools(
        settings.claude_opus_model, system_prompt, user_message,
        max_tokens=OPUS_MAX_TOKENS, temperature=0.4, on_chunk=on_chunk,
    )


def _stream_model_with_tools(
    model: str,
    system_prompt,
    user_message: str,
    max_tokens: int,
    temperature: float,
    on_chunk=None,
) -> tuple[Optional[str], float, dict]:
    """Internal: streamed generation carrying the travel extraction tool.

    Returns the same (text, cost, tool_entities) as _call_model_with_tools.
    """
    start = time.time()
    logger.info(f"Claude+Tool STREAM START | model={model}")

    try:
        with _get_client().messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=_build_system(system_prompt),
            messages=[{"role": "user", "content": user_message}],
            tools=[TRAVEL_TOOL],
            timeout=settings.claude_timeout,
        ) as stream:
            full_text = ""
            for text_chunk in stream.text_stream:
                full_text += text_chunk
                if on_chunk and text_chunk:
                    on_chunk(text_chunk)

            final = stream.get_final_message()

        # Log prompt cache stats
        try:
            _usage = getattr(final, 'usage', None)
            if _usage:
                _cc = getattr(_usage, 'cache_creation_input_tokens', 0) or 0
                _cr = getattr(_usage, 'cache_read_input_tokens', 0) or 0
                if _cc or _cr:
                    logger.info(
                        f"[CACHE] create={_cc} read={_cr} "
                        f"input={getattr(_usage, 'input_tokens', 0)}"
                    )
        except Exception:
            pass  # Non-critical logging

        elapsed = round(time.time() - start, 3)
        cost = _estimate_cost(
            model, final.usage.input_tokens, final.usage.output_tokens
        )

        tool_entities: dict = {}
        for block in final.content:
            btype = getattr(block, "type", None)
            if btype == "tool_use" and getattr(block, "name", None) == "save_travel_details":
                raw_in = getattr(block, "input", None) or {}
                tool_entities = dict(raw_in) if isinstance(raw_in, dict) else {}

        if not full_text.strip():
            for block in final.content:
                if getattr(block, "type", None) == "text":
                    full_text = (getattr(block, "text", None) or "").strip()

        logger.info(
            f"Claude+Tool STREAM SUCCESS | text={len(full_text)}ch entities={list(tool_entities.keys())} "
            f"cost=${cost:.6f} time={elapsed}s"
        )
        return full_text, cost, tool_entities

    except anthropic.APITimeoutError:
        logger.warning(f"Claude+Tool STREAM timeout after {time.time() - start:.1f}s")
        return None, 0.0, {}
    except anthropic.APIError as e:
        logger.error(f"Claude+Tool STREAM API error: {e}")
        return None, 0.0, {}
    except Exception as e:
        logger.error(f"Claude+Tool STREAM unexpected error: {e}")
        return None, 0.0, {}


def stream_sonnet(
    system_prompt,
    user_message: str,
    on_chunk=None,
) -> tuple[Optional[str], float]:
    """Stream Sonnet response. Returns (text, cost)."""
    return _stream_model(
        settings.claude_sonnet_model, system_prompt, user_message,
        max_tokens=SONNET_MAX_TOKENS, temperature=0.4, on_chunk=on_chunk,
    )


def stream_opus(
    system_prompt,
    user_message: str,
    on_chunk=None,
) -> tuple[Optional[str], float]:
    """Stream Opus response. Returns (text, cost)."""
    return _stream_model(
        settings.claude_opus_model, system_prompt, user_message,
        max_tokens=OPUS_MAX_TOKENS, temperature=0.4, on_chunk=on_chunk,
    )


def _stream_model(
    model: str,
    system_prompt,
    user_message: str,
    max_tokens: int,
    temperature: float,
    on_chunk=None,
) -> tuple[Optional[str], float]:
    """Internal: streamed generation without tools. Returns (text, cost).

    Mirrors _call_model: one retry on APITimeoutError, then the same
    APIError / unexpected-error logging and (None, 0.0) contract.
    """
    start = time.time()
    logger.info(f"Claude STREAM START | model={model}")

    for attempt in range(2):
        try:
            with _get_client().messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=_build_system(system_prompt),
                messages=[{"role": "user", "content": user_message}],
                timeout=settings.claude_timeout,
            ) as stream:
                full_text = ""
                for text_chunk in stream.text_stream:
                    full_text += text_chunk
                    if on_chunk and text_chunk:
                        on_chunk(text_chunk)

                final = stream.get_final_message()

            # Log prompt cache stats
            try:
                _usage = getattr(final, 'usage', None)
                if _usage:
                    _cc = getattr(_usage, 'cache_creation_input_tokens', 0) or 0
                    _cr = getattr(_usage, 'cache_read_input_tokens', 0) or 0
                    if _cc or _cr:
                        logger.info(
                            f"[CACHE] create={_cc} read={_cr} "
                            f"input={getattr(_usage, 'input_tokens', 0)}"
                        )
            except Exception:
                pass  # Non-critical logging

            cost = _estimate_cost(model, final.usage.input_tokens, final.usage.output_tokens)
            logger.info(
                f"Claude STREAM SUCCESS | model={model} text={len(full_text)}ch cost=${cost:.6f} "
                f"time={round(time.time() - start, 3)}s"
            )
            return full_text, cost

        except anthropic.APITimeoutError:
            logger.error(
                f"Claude STREAM timeout ({settings.claude_timeout}s) model={model} "
                f"attempt={attempt + 1}/2"
            )
            if attempt == 0:
                continue  # retry once
            return None, 0.0
        except anthropic.APIError as e:
            logger.error(
                f"Claude STREAM API error: {e} | model={model} "
                f"| status={getattr(e, 'status_code', 'N/A')}"
            )
            return None, 0.0
        except Exception as e:
            logger.error(
                f"Claude STREAM unexpected error: {type(e).__name__}: {e} | model={model}"
            )
            return None, 0.0

    return None, 0.0
