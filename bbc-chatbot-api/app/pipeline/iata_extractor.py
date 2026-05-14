"""Claude IATA fallback — extract airport codes when regex misses."""

import asyncio
import json
import logging
import re

from app.ai.claude import call_haiku

logger = logging.getLogger(__name__)

IATA_SYSTEM_PROMPT = """Extract travel details from this customer message.
Return ONLY a JSON object, nothing else:
{"origin": "XXX", "destination": "YYY", "passengers": N}

Rules:
- Use 3-letter IATA airport codes for the MAIN international airport
- If not mentioned, set to null
- passengers is an integer (null if not mentioned)
- Examples: Milan=MXP, Chisinau=KIV, Istanbul=IST, Tashkent=TAS,
  London=LHR, New York=JFK, Paris=CDG, Tokyo=NRT, Dubai=DXB"""


async def extract_iata_via_claude(message: str) -> dict:
    """Ask Claude Haiku to extract IATA codes from a message.

    Returns dict with optional keys: origin, destination, passengers.
    Called ONLY when regex entity extraction misses origin or destination.
    Cost: ~$0.00005 per call, latency: ~0.5s.
    """
    try:
        text, cost = await asyncio.to_thread(
            call_haiku, IATA_SYSTEM_PROMPT, message
        )
        if not text:
            return {}

        # Clean response — strip markdown fences if present
        clean = text.strip()
        clean = re.sub(r'^```json\s*', '', clean)
        clean = re.sub(r'\s*```$', '', clean)

        data = json.loads(clean)
        result = {}

        # Validate IATA codes: exactly 3 uppercase letters
        origin = data.get("origin")
        if origin and isinstance(origin, str):
            origin = origin.strip().upper()
            if re.match(r'^[A-Z]{3}$', origin):
                result["origin"] = origin

        destination = data.get("destination")
        if destination and isinstance(destination, str):
            destination = destination.strip().upper()
            if re.match(r'^[A-Z]{3}$', destination):
                result["destination"] = destination

        passengers = data.get("passengers")
        if passengers is not None:
            try:
                n = int(passengers)
                if 1 <= n <= 9:
                    result["passengers"] = n
            except (TypeError, ValueError):
                pass

        logger.info(f"IATA fallback: {result} (cost=${cost:.5f})")
        return result

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"IATA fallback parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"IATA fallback error: {e}")
        return {}
