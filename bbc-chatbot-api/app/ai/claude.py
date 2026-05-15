"""Claude API client — Haiku + Sonnet. No abstraction, no factory."""

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
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = COSTS.get(model, {"input": 0, "output": 0})
    return round(
        input_tokens * rates["input"] + output_tokens * rates["output"], 6
    )


# ── API calls ─────────────────────────────────────────────────

def call_haiku(system_prompt: str, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Haiku (cheap, fast). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_haiku_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=150,
        temperature=0.3,
    )


def call_sonnet(system_prompt: str, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Sonnet (expensive, smarter). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_sonnet_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=200,
        temperature=0.4,
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
    system_prompt: str,
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
                system=system_prompt,
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
        "Save travel details from the customer's message. "
        "ALWAYS call when customer mentions ANY travel info, even partial or misspelled.\n"
        "RULES:\n"
        "1. CITIES: Convert to 3-letter IATA code. Handle typos: londn=LHR, dubei=DXB, millan=MXP, pariz=CDG. "
        "Handle slang: nyc=JFK, la=LAX, lon=LHR, chi=ORD, sf=SFO, bos=BOS, vegas=LAS.\n"
        "2. ORIGIN/DEST: First city mentioned=origin, second=destination. "
        "'jfk lhr' means origin=JFK dest=LHR. 'I am from London, fly to Dubai' means origin=LHR dest=DXB.\n"
        "3. CHANGED MIND: Use LAST mentioned value. 'from JFK no wait LAX' means origin=LAX. "
        "Signals: actually, no wait, I mean, sorry, not X but Y.\n"
        "4. IGNORE IRRELEVANT: 'friend works at JFK' — JFK is NOT origin. "
        "'last time I flew to London' — ignore past trips, extract only CURRENT request.\n"
        "5. PASSENGERS: '2 ppl'=2, 'couple'=2, 'solo'=1, 'just me'=1, "
        "'me and wife'=2, 'family of 4'=4, 'three of us'=3, '2 adults'=2.\n"
        "6. TRIP TYPE: oneway/ow/single=one_way. roundtrip/rt/return=round_trip. "
        "If return date exists, use round_trip even if they said one way.\n"
        "7. CABIN: business/biz/j class=business. first/f class=first. premium economy=premium_economy.\n"
        "8. DATES: Convert to YYYY-MM-DD. 'june 15'=2026-06-15. If too vague like 'next month', omit."
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
                "description": "Departure date in YYYY-MM-DD format.",
            },
            "return_date": {
                "type": "string",
                "description": "Return date in YYYY-MM-DD format. Omit if one-way.",
            },
            "trip_type": {
                "type": "string",
                "enum": ["one_way", "round_trip"],
                "description": "one_way or round_trip based on customer message.",
            },
            "passengers": {
                "type": "integer",
                "description": "Number of passengers (1-9).",
            },
            "cabin_class": {
                "type": "string",
                "enum": ["business", "first", "premium_economy"],
                "description": "Cabin class preference.",
            },
        },
    },
}


def call_haiku_with_tools(
    system_prompt: str, user_message: str
) -> tuple[Optional[str], float, dict]:
    """Call Haiku with travel extraction tool. Returns (text, cost, entities).

    entities contains keys Claude extracted (origin, destination, etc.).
    Empty dict if Claude did not call the tool or on failure.
    """
    start = time.time()
    model = settings.claude_haiku_model
    logger.info(f"Claude+Tool START | model={model}")

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=200,
            temperature=0,
            system=system_prompt,
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
        logger.error(f"Claude+Tool timeout ({settings.claude_timeout}s)")
        return None, 0.0, {}
    except anthropic.APIError as e:
        logger.error(
            f"Claude+Tool API error: {e} | status={getattr(e, 'status_code', 'N/A')}"
        )
        return None, 0.0, {}
    except Exception as e:
        logger.error(f"Claude+Tool error: {type(e).__name__}: {e}")
        return None, 0.0, {}
