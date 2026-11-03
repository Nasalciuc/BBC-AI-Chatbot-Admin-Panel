"""mask_pii — scrub a conversation transcript before it leaves the process.

The eval and replay scripts write masked transcripts to out/ (gitignored); the
learning loop (PR-8) reuses this. The 3 Nov reading found the old masker left
first names, partial phone numbers and PNRs in place — this hardens exactly
those cases. Masking is best-effort redaction, never a security boundary.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

_EMAIL = re.compile(r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b", re.I)
# A run of >=4 digits next to +, (, - or spaces — a phone, whole or partial.
_PHONE = re.compile(r"(?:\+?\d[\s().\-]?){6,}\d")
_PHONE_PARTIAL = re.compile(r"[+(\-]\s*\d{4,}")
# A 6-char uppercase alphanumeric block with at least one letter and one digit:
# an airline PNR / booking reference.
_PNR = re.compile(r"\b(?=[A-Z0-9]{6}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{6}\b")
# Exact dates become [date] (a precise travel date is identifying in context).
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Capitalised word following a greeting in a bot line → a first name.
_GREETING_NAME = re.compile(r"\b(Welcome|Thank you|Thanks|Hi|Hello|Dear)([,!]?\s+)([A-Z][a-z]+)")


def mask_pii(text: Optional[str], names: Iterable[Optional[str]] = ()) -> str:
    """Redact emails, phones (incl. partial), PNRs, exact dates and names."""
    if not text:
        return text or ""
    out = text
    # Caller-supplied names (visitor_name, operator names) first, whole words.
    for name in names:
        n = (name or "").strip()
        if len(n) >= 2:
            for part in re.split(r"\s+", n):
                if len(part) >= 2:
                    out = re.sub(rf"\b{re.escape(part)}\b", "[name]", out, flags=re.I)
    out = _GREETING_NAME.sub(lambda m: f"{m.group(1)}{m.group(2)}[name]", out)
    out = _EMAIL.sub("[email]", out)
    out = _PNR.sub("[ref]", out)
    out = _PHONE.sub("[phone]", out)
    out = _PHONE_PARTIAL.sub("[phone]", out)
    out = _DATE.sub("[date]", out)
    return out
