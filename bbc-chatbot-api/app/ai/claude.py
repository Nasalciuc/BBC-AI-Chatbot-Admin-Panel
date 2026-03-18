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
    "claude-3-5-haiku-20241022": {
        "input": 0.25 / 1_000_000,
        "output": 1.25 / 1_000_000,
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
        max_tokens=300,
        temperature=0.3,
    )


def call_sonnet(system_prompt: str, user_message: str) -> tuple[Optional[str], float]:
    """Call Claude Sonnet (expensive, smarter). Returns (text, cost) — (None, 0) on failure."""
    return _call_model(
        model=settings.claude_sonnet_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=500,
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
