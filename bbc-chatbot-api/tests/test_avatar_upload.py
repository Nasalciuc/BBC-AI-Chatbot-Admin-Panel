"""POST /api/auth/me/avatar — multipart upload → Storage → avatar_url + JWT."""

from __future__ import annotations

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.auth import get_current_user


PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

PUBLIC_URL = "https://exwxdjfeoekfixnjsreq.supabase.co/storage/v1/object/public/avatars/u-1/abc.png"


def _client(role: str = "sales", user_id: str = "u-1") -> TestClient:
    from app.api.auth_routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "email": f"{role}@bbc.com",
        "name": "Op",
        "role": role,
        "tunnel_scope": "sales",
        "avatar_url": None,
        "phone": None,
    }
    return TestClient(app)


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "jwt_secret", "test-secret-for-avatar")
    monkeypatch.setattr(settings, "avatar_max_bytes", 2_000_000)
    monkeypatch.setattr(settings, "avatar_bucket", "avatars")


def test_valid_png_returns_url_and_token():
    updated = {
        "id": "u-1",
        "email": "sales@bbc.com",
        "name": "Op",
        "role": "sales",
        "tunnel_scope": "sales",
        "avatar_url": PUBLIC_URL,
        "phone": None,
    }
    with (
        patch(
            "app.api.auth_routes.upload_avatar",
            new_callable=AsyncMock,
            return_value=PUBLIC_URL,
        ) as mock_up,
        patch(
            "app.api.auth_routes.db.update_user",
            new_callable=AsyncMock,
            return_value=updated,
        ) as mock_db,
    ):
        resp = _client("sales").post(
            "/api/auth/me/avatar",
            files={"file": ("photo.png", BytesIO(PNG_1x1), "image/png")},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatar_url"] == PUBLIC_URL
    assert isinstance(body["token"], str) and body["token"]
    mock_up.assert_awaited_once()
    mock_db.assert_awaited_once_with("u-1", {"avatar_url": PUBLIC_URL})


@pytest.mark.parametrize("role", ["sales", "support", "supervisor"])
def test_any_authenticated_role_can_upload(role):
    updated = {
        "id": "u-1",
        "email": f"{role}@bbc.com",
        "name": "Op",
        "role": role,
        "tunnel_scope": "sales",
        "avatar_url": PUBLIC_URL,
    }
    with (
        patch(
            "app.api.auth_routes.upload_avatar",
            new_callable=AsyncMock,
            return_value=PUBLIC_URL,
        ),
        patch(
            "app.api.auth_routes.db.update_user",
            new_callable=AsyncMock,
            return_value=updated,
        ),
    ):
        resp = _client(role).post(
            "/api/auth/me/avatar",
            files={"file": ("photo.png", BytesIO(PNG_1x1), "image/png")},
        )
    assert resp.status_code == 200, resp.text


def test_unsupported_type_422():
    from app.services.storage import upload_avatar

    with pytest.raises(ValueError, match="Unsupported"):
        asyncio.run(
            upload_avatar(data=b"%PDF", content_type="application/pdf", user_id="u-1")
        )

    with patch(
        "app.api.auth_routes.upload_avatar",
        new_callable=AsyncMock,
        side_effect=ValueError("Unsupported image type (use JPEG, PNG or WEBP)"),
    ):
        resp = _client().post(
            "/api/auth/me/avatar",
            files={"file": ("x.gif", BytesIO(b"GIF89a"), "image/gif")},
        )
    assert resp.status_code == 422
    assert "Unsupported" in resp.json()["detail"]


def test_too_large_422(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "avatar_max_bytes", 10)
    big = b"x" * 20
    resp = _client().post(
        "/api/auth/me/avatar",
        files={"file": ("big.png", BytesIO(big), "image/png")},
    )
    assert resp.status_code == 422
    assert "too large" in resp.json()["detail"].lower()


def test_update_user_failure_500():
    with (
        patch(
            "app.api.auth_routes.upload_avatar",
            new_callable=AsyncMock,
            return_value=PUBLIC_URL,
        ),
        patch(
            "app.api.auth_routes.db.update_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        resp = _client().post(
            "/api/auth/me/avatar",
            files={"file": ("photo.png", BytesIO(PNG_1x1), "image/png")},
        )
    assert resp.status_code == 500
    assert "Failed to save avatar" in resp.json()["detail"]


def test_storage_helper_rejects_oversized():
    from app.services.storage import upload_avatar
    from config.settings import settings

    data = b"x" * (settings.avatar_max_bytes + 1)
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(
            upload_avatar(data=data, content_type="image/png", user_id="u-1")
        )
