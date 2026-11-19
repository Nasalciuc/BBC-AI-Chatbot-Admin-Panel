"""Qdrant vector search client — server-side embedding via Qdrant Cloud Inference.

Uses httpx REST API (no qdrant-client dependency needed).
Model: sentence-transformers/all-minilm-l6-v2 (384d, FREE on Qdrant Cloud).
Qdrant embeds text server-side — zero client-side embedding.
"""
import asyncio
import logging
import re
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
_VECTOR_SIZE = 384
_DISTANCE = "Cosine"
_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_http_client: Optional[httpx.Client] = None


def _get_client() -> Optional[httpx.Client]:
    """Lazy-init httpx client for Qdrant Cloud REST API."""
    global _http_client
    if not settings.qdrant_url or not settings.qdrant_api_key:
        return None
    if _http_client is None:
        _http_client = httpx.Client(
            base_url=settings.qdrant_url.rstrip("/"),
            headers={"api-key": settings.qdrant_api_key},
            timeout=_TIMEOUT,
        )
    return _http_client


def _collection_url(path: str = "") -> str:
    return f"/collections/{settings.qdrant_collection}{path}"


# ════════════════════════════════════════════════════════════════
# COLLECTION MANAGEMENT
# ════════════════════════════════════════════════════════════════

def _ensure_collection_sync() -> bool:
    """Delete old collection (if exists) and create new one at 384d with inference."""
    client = _get_client()
    if not client:
        logger.warning("Qdrant not configured — skipping collection setup")
        return False
    try:
        # Delete existing collection (ignore 404)
        client.delete(_collection_url())

        # Create collection with named vector using server-side inference
        create_body = {
            "vectors": {
                "size": _VECTOR_SIZE,
                "distance": _DISTANCE,
                "on_disk": True,
            },
        }
        resp = client.put(_collection_url(), json=create_body)
        resp.raise_for_status()
        logger.info(f"Qdrant collection '{settings.qdrant_collection}' created ({_VECTOR_SIZE}d)")
        # A new collection has no indexes: without this one every search
        # would be refused again.
        _ensure_tunnel_index_sync()
        return True
    except Exception as e:
        logger.error(f"Qdrant ensure_collection failed: {e}")
        return False


async def ensure_collection() -> bool:
    return await asyncio.to_thread(_ensure_collection_sync)


# Every search filters on `tunnel`, and Qdrant Cloud refuses a filter on a
# field that has no index: "Index required but not found for "tunnel" of one
# of the following types: [keyword]" (HTTP 400). That refusal is why no turn
# has had a KB entry since 29 May 2026 (QDRANT_HEALTH.refused_because, 16 Nov).
# The index is made once; every start checks that it is there.
_TUNNEL_INDEX = {"field_name": "tunnel", "field_schema": "keyword"}
_INDEX_TIMEOUT = 30.0  # wait=true: the answer comes once the index is built


def _ensure_tunnel_index_sync() -> str:
    """Make sure `tunnel` has its keyword index. Looks first and creates it
    only when it is missing, so running it on every start changes nothing
    once it is there. Returns what it found or did — "present", "created",
    "not configured" or "failed (…)" — and keeps it in
    QDRANT_HEALTH["tunnel_index"]. Never raises: a start never waits on it."""
    client = _get_client()
    if not client:
        state = "not configured"
        QDRANT_HEALTH["tunnel_index"] = state
        return state
    try:
        resp = client.get(_collection_url())
        resp.raise_for_status()
        schema = (resp.json().get("result") or {}).get("payload_schema") or {}
        if (schema.get("tunnel") or {}).get("data_type") == "keyword":
            state = "present"
        else:
            made = client.put(
                _collection_url("/index"), params={"wait": "true"},
                json=_TUNNEL_INDEX, timeout=_INDEX_TIMEOUT,
            )
            made.raise_for_status()
            state = "created"
            logger.info(f"[qdrant] keyword index on 'tunnel' created in '{settings.qdrant_collection}'")
    except Exception as e:  # noqa: BLE001 — broad on purpose: a start must never fail over this
        # The kind and the status, not the answer's text.
        status = getattr(getattr(e, "response", None), "status_code", None)
        state = f"failed ({type(e).__name__}{f', HTTP {status}' if status else ''})"
        logger.error(
            f"[qdrant] keyword index on 'tunnel' not ensured ({type(e).__name__}, status {status}): "
            "KB searches stay refused (HTTP 400)"
        )
    QDRANT_HEALTH["tunnel_index"] = state
    return state


async def ensure_tunnel_index() -> str:
    return await asyncio.to_thread(_ensure_tunnel_index_sync)


# ════════════════════════════════════════════════════════════════
# UPSERT — send raw text, Qdrant embeds server-side
# ════════════════════════════════════════════════════════════════

def _upsert_sync(entry_id: str, title: str, content: str,
                 tunnel: str, category_id: Optional[str]) -> bool:
    """Upsert a single KB entry into Qdrant with server-side embedding."""
    client = _get_client()
    if not client:
        return False
    try:
        text = f"{title} — {content}"
        body = {
            "points": [
                {
                    "id": entry_id,
                    "vector": {
                        "text": text,
                        "model": _EMBEDDING_MODEL,
                    },
                    "payload": {
                        "title": title,
                        "content": content,
                        "tunnel": tunnel,
                        "category_id": category_id,
                    },
                }
            ]
        }
        resp = client.put(_collection_url("/points"), json=body)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Qdrant upsert failed for {entry_id}: {e}")
        return False


async def upsert_kb_entry(entry_id: str, title: str, content: str,
                          tunnel: str = "sales",
                          category_id: Optional[str] = None) -> bool:
    return await asyncio.to_thread(_upsert_sync, entry_id, title, content, tunnel, category_id)


async def upsert_kb_entry_dict(entry: dict) -> bool:
    """Convenience wrapper — accepts a full KB row dict."""
    return await upsert_kb_entry(
        entry_id=entry["id"],
        title=entry.get("title", ""),
        content=entry.get("content", ""),
        tunnel=entry.get("tunnel", "sales"),
        category_id=entry.get("category_id"),
    )


# ════════════════════════════════════════════════════════════════
# DELETE — remove a single point from collection
# ════════════════════════════════════════════════════════════════

def _delete_sync(entry_id: str) -> bool:
    """Delete a single KB entry from Qdrant."""
    client = _get_client()
    if not client:
        return False
    try:
        body = {"points": [entry_id]}
        resp = client.post(_collection_url("/points/delete"), json=body)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Qdrant delete failed for {entry_id}: {e}")
        return False


async def delete_kb_entry_qdrant(entry_id: str) -> bool:
    return await asyncio.to_thread(_delete_sync, entry_id)


# ════════════════════════════════════════════════════════════════
# BATCH SYNC — upsert all entries in one call
# ════════════════════════════════════════════════════════════════

def _sync_all_sync(entries: list[dict]) -> dict:
    """Batch upsert all KB entries to Qdrant in a single PUT."""
    client = _get_client()
    if not client:
        return {"synced": 0, "failed": len(entries)}
    try:
        points = [
            {
                "id": e["id"],
                "vector": {
                    "text": f"{e.get('title', '')} — {e.get('content', '')}",
                    "model": _EMBEDDING_MODEL,
                },
                "payload": {
                    "title": e.get("title", ""),
                    "content": e.get("content", ""),
                    "tunnel": e.get("tunnel", "sales"),
                    "category_id": e.get("category_id"),
                },
            }
            for e in entries
        ]
        resp = client.put(_collection_url("/points"), json={"points": points})
        resp.raise_for_status()
        logger.info(f"[qdrant] batch synced {len(points)} entries")
        return {"synced": len(points), "failed": 0}
    except Exception as e:
        logger.error(f"[qdrant] batch sync failed: {e}")
        return {"synced": 0, "failed": len(entries)}


async def sync_kb_entries(entries: list[dict]) -> dict:
    return await asyncio.to_thread(_sync_all_sync, entries)


# ════════════════════════════════════════════════════════════════
# SEARCH — send raw text query, Qdrant embeds + searches
# ════════════════════════════════════════════════════════════════

_QUERY_BODY_LOGGED = False  # dump the failing /points/query body once per process

# Search by meaning, counted for /health: every failure here is a turn that
# got NO knowledge-base entry, and nobody saw it. Nothing searches by keyword
# behind this: the pipeline's keyword search cannot be reached since the KB
# read moved into a parallel task (#57, 27 May 2026), and pipeline_runs has no
# turn with a KB entry after 29 May 2026. `last_error` is the kind of the
# latest failure ("HTTP 400", "ReadTimeout"); `refused_because` is the reason
# Qdrant gives, which the status line alone never said (see _why_refused).
# `tunnel_index` is what this start found or did about the index every search
# needs (_ensure_tunnel_index_sync).
QDRANT_HEALTH: dict = {
    "searches": 0, "failed": 0, "last_status": None, "last_error": None, "refused_because": None,
    "tunnel_index": None,
}

# Our words, not a visitor's: what Qdrant answers to them can be kept whole.
_PROBE_TEXT = "business class flights to London"


def _why_refused(client: httpx.Client, body: dict) -> str:
    """Why Qdrant refuses this kind of request — asked with OUR words.

    The refused request carried a visitor's message, and the answer to it can
    quote that message back (in another spacing, cut short, escaped): no
    search-and-replace cleans that reliably, so that answer is never kept or
    logged. The same request goes out once more with a fixed text of ours:
    nothing of a visitor's is sent, so nothing of a visitor's can come back.

    A diagnostic: whatever goes wrong in it (the request, or reading the
    answer) is said in the result and never costs the turn."""
    try:
        probe = {**body, "query": {**(body.get("query") or {}), "text": _PROBE_TEXT}}
        resp = client.post(_collection_url("/points/query"), json=probe)
        if resp.status_code < 400:
            return f"HTTP {resp.status_code} for our own text: the refusal depends on the text that was sent"
        said = " ".join(str(resp.text or "").split())[:300]
        return f"HTTP {resp.status_code}: {said or '(empty body)'}"
    except Exception as e:  # noqa: BLE001 — broad on purpose: nothing in a diagnostic may cost the turn
        logger.warning(f"[qdrant] the probe failed: {type(e).__name__}")
        return f"probe failed ({type(e).__name__})"


def _search_sync(query_text: str, tunnel: str = "sales", limit: int = 3) -> list[dict]:
    """Search KB entries by text similarity. Qdrant embeds the query server-side."""
    client = _get_client()
    if not client:
        return []
    QDRANT_HEALTH["searches"] += 1
    try:
        _SCORE_THRESHOLD = 0.60  # Minimum cosine similarity — below this, KB entry is irrelevant

        body: dict = {
            "query": {
                "text": query_text,
                "model": _EMBEDDING_MODEL,
            },
            "limit": limit,
            "with_payload": True,
            "score_threshold": _SCORE_THRESHOLD,
        }
        if tunnel:
            body["filter"] = {
                "must": [{"key": "tunnel", "match": {"value": tunnel}}]
            }
        resp = client.post(_collection_url("/points/query"), json=body)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for point in data.get("result", {}).get("points", []):
            score = point.get("score", 0.0)
            if score < _SCORE_THRESHOLD:  # Python safety net
                continue
            payload = point.get("payload", {})
            results.append({
                "id": point.get("id"),
                "title": payload.get("title", ""),
                "content": payload.get("content", ""),
                "score": score,
            })
        # The visitor's words stay out of the log (they can hold a name or a
        # phone number): how long the question was, not what it said.
        logger.info(f"[qdrant] query of {len(query_text)} chars \u2192 {len(results)} results above threshold {_SCORE_THRESHOLD}")
        return results
    except Exception as e:
        # FOLLOW-UP: /points/query returns 400 in production, and the turn
        # then has no KB entry at all (no keyword search runs behind this
        # one). Needs its own investigation — the one-shot lines below
        # capture what was sent and why it was refused.
        # WARNING, not DEBUG: production runs at INFO, a DEBUG line would never
        # surface; and the flag is only consumed on an actual 400 so a
        # timeout can't waste the single shot. Neither line carries the
        # visitor's words: the body is logged with the text's length in its
        # place, and the reason is asked for with a text of ours.
        global _QUERY_BODY_LOGGED
        _status = getattr(getattr(e, "response", None), "status_code", None)
        QDRANT_HEALTH["failed"] += 1
        QDRANT_HEALTH["last_status"] = _status
        QDRANT_HEALTH["last_error"] = f"HTTP {_status}" if _status else type(e).__name__
        if _status == 400 and not _QUERY_BODY_LOGGED:
            _QUERY_BODY_LOGGED = True
            _sent = {**body, "query": {**body["query"], "text": f"<{len(query_text)} chars>"}}
            logger.warning(f"[qdrant] failing /points/query body (once): {_sent}")
            # The exception carries only "400 Bad Request"; this is the reason.
            _why = _why_refused(client, body)
            QDRANT_HEALTH["refused_because"] = _why
            logger.warning(f"[qdrant] what Qdrant answers to our own text (once): {_why}")
        # The kind of failure, not its text (an error raised while the answer
        # is read can quote the answer). And what it means for the turn: no
        # KB entry, not "keyword search instead".
        logger.warning(f"Qdrant search failed ({type(e).__name__}, status {_status}): no KB entries for this turn")
        return []


# A tapped button or a reply of a word or two asks the knowledge base
# nothing. 16-17 Nov: every such search ("Yes, check fares" — the card's
# button — "ok", "Text") came back empty, a round trip to Qdrant each, and a
# loose match would only put an unrelated entry in front of Alex. The labels
# of 16 Nov stay: a card shown before a deploy still sends them.
_BUTTON_TAPS = frozenset({
    "yes, send me fares", "get a fare quote", "email me the fares",
    "yes, check fares", "check fares", "change details", "i have a question", "not now",
    # The card in Spanish and French (app/services/proactive_i18n), and the buttons after it
    # (app/services/card_words).
    "sí, envíenme tarifas", "cambiar detalles", "tengo una pregunta", "quiero una cotización",
    "envíenme las tarifas por correo", "oui, envoyez-moi les tarifs", "modifier", "j'ai une question",
    "obtenir un devis", "envoyez-moi les tarifs par e-mail",
    "whatsapp", "i'll wait here", "espero aquí", "j'attends ici",
})
_NO_QUESTION_WORDS = frozenset("""
ok okay k yes yeah yep yup no nope sure fine great perfect cool good nice alright right
thanks thank you thx ty please pls hi hello hey got it lol hmm sounds
text txt sms email mail call phone
""".split())
# Words in any script: a question in Spanish or Russian is still a question.
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def worth_searching(query_text: str) -> bool:
    """False for a button tap or a message made only of acknowledgements
    and channel words ("Yes please", "ok thanks", "Text")."""
    text = " ".join((query_text or "").lower().split()).strip(" .!?")
    if not text or text in _BUTTON_TAPS:
        return False
    words = _WORD_RE.findall(text)
    return bool(words) and not all(w in _NO_QUESTION_WORDS for w in words)


async def search_kb(query_text: str, tunnel: str = "sales", limit: int = 3) -> list[dict]:
    if not worth_searching(query_text):
        return []
    return await asyncio.to_thread(_search_sync, query_text, tunnel, limit)


# ════════════════════════════════════════════════════════════════
# COLLECTION INFO (for scripts/health checks)
# ════════════════════════════════════════════════════════════════

def _count_points_sync() -> int:
    """Return number of points in collection, or -1 on error."""
    client = _get_client()
    if not client:
        return -1
    try:
        resp = client.get(_collection_url())
        resp.raise_for_status()
        return resp.json().get("result", {}).get("points_count", 0)
    except Exception:
        return -1


async def count_points() -> int:
    return await asyncio.to_thread(_count_points_sync)
