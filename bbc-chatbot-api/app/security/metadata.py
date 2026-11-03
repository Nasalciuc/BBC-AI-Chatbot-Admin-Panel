"""Allowlist for widget-supplied conversation metadata (PR-4).

The widget may set only attribution/landing fields. Everything that affects
routing, identity or billing — client_ip, site, engaged_agent_id,
sticky_agent_id and the pipeline's own state keys — is written by the server
only. Before PR-4 the client's values were merged first and server values
added with setdefault, so a client could pin its own client_ip or
engaged_agent_id. This drops anything outside the allowlist at ingress.
"""

from __future__ import annotations

# Exact keys the widget may send.
_ALLOWED_EXACT = frozenset({
    "page_url", "page_path", "referrer", "gclid", "fbclid", "visitor_id",
})
# Prefixes the widget may send (utm_source, landing_city, …).
_ALLOWED_PREFIXES = ("utm_", "landing_")


def allowed_widget_metadata(raw: dict | None) -> dict:
    """Keep only the attribution/landing fields; drop everything else."""
    if not raw:
        return {}
    out: dict = {}
    for k, v in raw.items():
        key = str(k)
        if key in _ALLOWED_EXACT or key.startswith(_ALLOWED_PREFIXES):
            out[key] = v
    return out
