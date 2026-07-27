"""Supabase Storage helper — avatar uploads via the service_role client.

The `avatars` bucket must exist and be public-read (panel loads images via
plain <img>). Writes go only through this service_role path — never from the
browser. See AVATAR_STORAGE_SETUP.md (owner one-time Dashboard task).
"""

from __future__ import annotations

import logging
import uuid

from app.db.supabase import get_client, _run_sync
from config.settings import settings

logger = logging.getLogger(__name__)

_ALLOWED: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


async def upload_avatar(*, data: bytes, content_type: str, user_id: str) -> str:
    """Upload avatar bytes to the avatars bucket; return the public URL.

    Raises ValueError on bad type/size (caller maps to HTTP 422).
    """
    ext = _ALLOWED.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError("Unsupported image type (use JPEG, PNG or WEBP)")
    if len(data) > settings.avatar_max_bytes:
        raise ValueError("Image too large (max 2MB)")
    if not user_id:
        raise ValueError("Missing user id")

    path = f"{user_id}/{uuid.uuid4().hex}.{ext}"
    bucket = settings.avatar_bucket
    ctype = (content_type or "").split(";")[0].strip().lower()

    def _upload():
        client = get_client()
        client.storage.from_(bucket).upload(
            path,
            data,
            {"content-type": ctype, "upsert": "true"},
        )
        return client.storage.from_(bucket).get_public_url(path)

    try:
        url = await _run_sync(_upload)
    except Exception as e:
        logger.error(f"avatar upload failed user={user_id}: {e}")
        raise RuntimeError("Storage upload failed — check the avatars bucket exists") from e

    if not url or not isinstance(url, str):
        raise RuntimeError("Storage returned no public URL")
    return url
