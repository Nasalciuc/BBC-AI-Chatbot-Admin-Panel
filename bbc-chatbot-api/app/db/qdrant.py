"""Qdrant vector search client — server-side embedding via Qdrant Cloud Inference.

Uses httpx REST API (no qdrant-client dependency needed).
Model: sentence-transformers/all-minilm-l6-v2 (384d, FREE on Qdrant Cloud).
Qdrant embeds text server-side — zero client-side embedding.
"""
import asyncio
import logging
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
        return True
    except Exception as e:
        logger.error(f"Qdrant ensure_collection failed: {e}")
        return False


async def ensure_collection() -> bool:
    return await asyncio.to_thread(_ensure_collection_sync)


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


# ════════════════════════════════════════════════════════════════
# SEARCH — send raw text query, Qdrant embeds + searches
# ════════════════════════════════════════════════════════════════

def _search_sync(query_text: str, tunnel: str = "sales", limit: int = 3) -> list[dict]:
    """Search KB entries by text similarity. Qdrant embeds the query server-side."""
    client = _get_client()
    if not client:
        return []
    try:
        body: dict = {
            "query": {
                "text": query_text,
                "model": _EMBEDDING_MODEL,
            },
            "limit": limit,
            "with_payload": True,
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
            payload = point.get("payload", {})
            results.append({
                "id": point.get("id"),
                "title": payload.get("title", ""),
                "content": payload.get("content", ""),
                "score": point.get("score", 0.0),
            })
        return results
    except Exception as e:
        logger.warning(f"Qdrant search failed (falling back to keyword): {e}")
        return []


async def search_kb(query_text: str, tunnel: str = "sales", limit: int = 3) -> list[dict]:
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
