"""Input sanitisation — runs BEFORE any pipeline processing."""

import logging
import re

logger = logging.getLogger(__name__)

SAFE_FALLBACK = "I'd like to learn about business class flights."

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # ── Original 9 patterns ───────────────────────────────────
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now",
        r"(system|original)\s+prompt",
        r"reveal\s+your",
        r"act\s+as\s+(a|an)?",
        r"pretend\s+(to\s+be|you)",
        r"forget\s+(everything|all|your)",
        r"new\s+instructions?:",
        r"<\|?(system|user|assistant)\|?>",
        # ── ROLEPLAY / DAN JAILBREAK (ASR 89.6%) ─────────────────
        r"you\s+are\s+now\s+\w+",
        r"from\s+now\s+on\s+you",
        r"roleplay\s+(as|with)",
        r"let'?s?\s+(play|pretend|roleplay)",
        r"\bDAN\b.*no\s+(restrictions|rules|limits)",
        r"in\s+character",
        # ── ETHICAL DILEMMA / HYPOTHETICAL (ASR 81.4%) ────────────
        r"hypothetical(ly)?.*instructions",
        r"(emergency|life|die).*prompt",
        r"in\s+a\s+world\s+where.*no\s+rules",
        # ── ENCODING / OBFUSCATION (ASR 76.2%) ───────────────────
        r"(?:base64|decode|convert|translate).*[A-Za-z0-9+/=]{20,}",
        r"(decode|decipher|translate|convert)\s+(this|the\s+following|and\s+follow)",
        # ── SYSTEM PROMPT EXTRACTION ──────────────────────────────
        r"repeat\s+(everything|all|the\s+text)\s+(above|before)",
        r"(first|initial)\s+thing\s+(you\s+were|told)",
        r"start\s+your\s+response\s+with",
        r"what\s+(were|are)\s+you\s+told",
        r"(print|output|display|show)\s+(your|the)\s+(prompt|instructions|rules)",
        # ── PRIVILEGE ESCALATION ──────────────────────────────────
        r"(developer|admin|debug|maintenance)\s+mode",
        r"I\s+(am|work)\s+(from|at|for)\s+(anthropic|openai|the\s+company)",
        r"(enable|activate|enter)\s+.*(unrestricted|unlimited|debug)",
        # ── MULTI-LANGUAGE (top 3: FR, ES, RU) ───────────────────
        r"ignor(ez?|a|ar)\s+(les?|las?|все)?\s*(instruc|règles|reglas)",
        r"(montrez?|muestra|покажи)\s+.*(prompt|system|instruc)",
        r"(oublie|olvida|забудь)\s+.*(instruc|règles|reglas)",
        # ── TOKEN/REWARD MANIPULATION ─────────────────────────────
        r"(token|reward|points?)\s+(for|if)\s+.*(system|prompt|instructions)",
    ]
]

MAX_LENGTH = 2000
HTML_TAG_RE = re.compile(r"<[^>]+>")
EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


def count_injection_hits(message: str) -> int:
    """Count how many injection patterns match. Multiple hits = high confidence attack."""
    return sum(1 for p in INJECTION_PATTERNS if p.search(message))


def is_suspicious(message: str) -> bool:
    """Return True if message contains prompt-injection patterns."""
    hits = count_injection_hits(message)
    if hits >= 3:
        logger.critical(f"Coordinated injection attempt ({hits} patterns matched)")
    elif hits >= 1:
        logger.warning(f"Possible injection attempt ({hits} pattern(s) matched)")
    return hits > 0


def sanitize_message(message: str) -> str:
    """Clean and validate visitor message.

    1. Strip whitespace
    2. Enforce max length (truncate)
    3. Remove HTML tags
    4. Block prompt injection → return safe fallback
    5. Collapse excessive newlines
    """
    # 1. Strip
    text = message.strip()

    # 2. Max length
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH]

    # 3. Remove HTML tags
    text = HTML_TAG_RE.sub("", text)

    # 4. Injection check
    if is_suspicious(text):
        return SAFE_FALLBACK

    # 5. Collapse newlines (3+ → 2)
    text = EXCESS_NEWLINES_RE.sub("\n\n", text)

    return text if text else SAFE_FALLBACK
