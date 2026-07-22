"""PII masking helpers — applied at the role-aware API layer for supervisors.

Supervisors do QA on operators and must not see full customer PII. These
helpers produce partial masks (enough to cross-reference in the CRM, not the
raw value) and strip marketing/acquisition metadata that has no QA value.

Masking is applied per-response in the API layer (conversations/leads), never
inside the shared db functions — so owner/admin/qa keep full data.
"""

from typing import Optional

_DOT = "\u2022"  # • bullet used for masking


def mask_name(name: Optional[str]) -> Optional[str]:
    """Hide everything but the last 2 characters. 'Emily' -> '•••ly'."""
    if not name:
        return name
    if len(name) <= 2:
        return _DOT * len(name)
    return _DOT * 3 + name[-2:]


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """Hide everything but the last 4 digits. '+14157179051' -> '•••9051'."""
    if not phone:
        return phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return _DOT * 3
    return _DOT * 3 + digits[-4:]


def mask_email(email: Optional[str]) -> Optional[str]:
    """Reveal a couple leading chars + domain.
    'tristud2002@yahoo.com' -> 'tr•••@yahoo.com'."""
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{_DOT * 3}@{domain}"


# Acquisition-tracking keys with zero QA value. NOTE: 'site' is intentionally
# NOT included — it identifies the brand, not the marketing source.
_MARKETING_KEYS = (
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "referrer", "page_url", "landing_page", "landing",
)


def strip_marketing_metadata(metadata: Optional[dict]) -> dict:
    """Return metadata with all marketing/acquisition keys removed."""
    if not metadata:
        return {}
    return {k: v for k, v in metadata.items() if k not in _MARKETING_KEYS}


def mask_visitor_row(row: dict) -> dict:
    """In-place mask of a conversation/lead row's visitor PII + marketing meta.
    Safe to call on any dict; only touches keys that exist."""
    if not isinstance(row, dict):
        return row
    if "visitor_name" in row:
        row["visitor_name"] = mask_name(row.get("visitor_name"))
    if "visitor_phone" in row:
        row["visitor_phone"] = mask_phone(row.get("visitor_phone"))
    if "visitor_email" in row:
        row["visitor_email"] = mask_email(row.get("visitor_email"))
    if "metadata" in row:
        row["metadata"] = strip_marketing_metadata(row.get("metadata"))
    return row
